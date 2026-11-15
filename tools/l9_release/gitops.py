"""Git operations, argv-only.

Every call is ``git`` with an explicit argument list; nothing is interpolated
into a shell. Tag writes are create-only for exact tags (the remote rejects an
ordinary push to an existing ref, so the push itself is the proof that exact
provenance is never overwritten) and compare-and-swap for the maintained major
tag (``--force-with-lease`` bound to the SHA the monotonicity proof read).
"""

from __future__ import annotations

import os
import pathlib
import subprocess
from collections.abc import Mapping

from .errors import ReleaseInfrastructureError, ReleaseRefused
from .identity import EXACT_TAG, MAJOR_TAG

#: Identity for annotated tags created by the engine. The token identity owns
#: the push; this only names the tagger in the tag object.
TAGGER_NAME = "github-actions[bot]"
TAGGER_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"


def git(
    repository: pathlib.Path,
    *arguments: str,
    env: Mapping[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    command = ["git", "-C", str(repository), *arguments]
    merged = dict(os.environ)
    merged.setdefault("GIT_TERMINAL_PROMPT", "0")
    if env:
        merged.update(env)
    try:
        completed = subprocess.run(
            command, text=True, capture_output=True, env=merged, check=False
        )
    except OSError as error:
        raise ReleaseInfrastructureError(f"cannot run git: {error}") from error
    if check and completed.returncode != 0:
        raise ReleaseInfrastructureError(
            f"git {' '.join(arguments)} failed ({completed.returncode}): "
            + (completed.stderr.strip() or completed.stdout.strip())
        )
    return completed


def head_commit(repository: pathlib.Path) -> str:
    return git(repository, "rev-parse", "--verify", "HEAD^{commit}").stdout.strip()


def resolve_commit(repository: pathlib.Path, revision: str) -> str:
    return git(
        repository, "rev-parse", "--verify", f"{revision}^{{commit}}"
    ).stdout.strip()


def commit_epoch(repository: pathlib.Path, commit: str) -> int:
    return int(git(repository, "show", "-s", "--format=%ct", commit).stdout.strip())


def file_at(repository: pathlib.Path, commit: str, path: str) -> str:
    completed = git(repository, "show", f"{commit}:{path}", check=False)
    if completed.returncode != 0:
        raise ReleaseRefused(f"{path} does not exist at {commit}")
    return completed.stdout


def require_clean(repository: pathlib.Path) -> None:
    """Refuse a working tree with tracked changes or untracked files."""

    status = git(repository, "status", "--porcelain", "--untracked-files=all")
    if status.stdout.strip():
        raise ReleaseRefused(
            "working tree is dirty; a release is built only from a clean exact "
            "commit:\n" + status.stdout.rstrip()
        )


def remote_tags(repository: pathlib.Path, remote: str) -> dict[str, str]:
    """Map every remote tag to the commit it points at (annotated tags peeled)."""

    listing = git(repository, "ls-remote", "--tags", remote).stdout
    direct: dict[str, str] = {}
    peeled: dict[str, str] = {}
    for line in listing.splitlines():
        sha, _, ref = line.partition("\t")
        if not ref.startswith("refs/tags/"):
            continue
        name = ref[len("refs/tags/") :]
        if name.endswith("^{}"):
            peeled[name[:-3]] = sha
        else:
            direct[name] = sha
    return {name: peeled.get(name, sha) for name, sha in direct.items()}


def fetch_commit(repository: pathlib.Path, remote: str, commit: str) -> None:
    present = git(repository, "cat-file", "-e", f"{commit}^{{commit}}", check=False)
    if present.returncode != 0:
        git(repository, "fetch", "--no-tags", "--depth=1", remote, commit)


def clean_clone(source: pathlib.Path, destination: pathlib.Path, commit: str) -> None:
    """Clone ``source`` into ``destination`` and detach at ``commit``.

    A full local clone (``--no-local`` copies objects instead of hard-linking),
    so build residue in ``destination`` can never reach the source repository.
    """

    try:
        completed = subprocess.run(
            [
                "git",
                "clone",
                "--quiet",
                "--no-local",
                "--no-checkout",
                str(source),
                str(destination),
            ],
            text=True,
            capture_output=True,
            check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except OSError as error:
        raise ReleaseInfrastructureError(f"cannot run git clone: {error}") from error
    if completed.returncode != 0:
        raise ReleaseInfrastructureError(
            f"git clone failed: {completed.stderr.strip()}"
        )
    git(
        destination,
        "-c",
        "advice.detachedHead=false",
        "checkout",
        "--quiet",
        "--detach",
        commit,
    )


def _tagger_env(epoch: int | None) -> dict[str, str]:
    env = {
        "GIT_COMMITTER_NAME": TAGGER_NAME,
        "GIT_COMMITTER_EMAIL": TAGGER_EMAIL,
    }
    if epoch is not None:
        env["GIT_COMMITTER_DATE"] = f"{epoch} +0000"
    return env


def create_exact_tag(
    repository: pathlib.Path,
    remote: str,
    tag: str,
    commit: str,
    message: str,
    epoch: int | None = None,
) -> None:
    """Create and push an annotated exact tag. Create-only; never moves one."""

    if EXACT_TAG.fullmatch(tag) is None:
        raise ReleaseInfrastructureError(f"refusing to write non-exact tag {tag!r}")
    git(
        repository,
        "tag",
        "--annotate",
        "--message",
        message,
        tag,
        commit,
        env=_tagger_env(epoch),
    )
    # No force flag and no lease: the server refuses an existing ref.
    git(repository, "push", remote, f"refs/tags/{tag}:refs/tags/{tag}")


def write_major_tag(
    repository: pathlib.Path,
    remote: str,
    tag: str,
    commit: str,
    previous: str | None,
) -> None:
    """Create the maintained major tag, or advance it by compare-and-swap.

    Only a ``vMAJOR`` ref can reach this function's lease. An exact tag is
    refused before anything is written.
    """

    if MAJOR_TAG.fullmatch(tag) is None:
        raise ReleaseInfrastructureError(f"refusing to move non-major tag {tag!r}")
    ref = f"refs/tags/{tag}"
    if previous is None:
        git(repository, "push", remote, f"{commit}:{ref}")
        return
    git(
        repository,
        "push",
        f"--force-with-lease={ref}:{previous}",
        remote,
        f"{commit}:{ref}",
    )
