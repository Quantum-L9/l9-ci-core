"""``build``: reproducible release artifacts from a clean exact commit.

Each build round clones the exact commit into a fresh directory and runs the
adapter with ``SOURCE_DATE_EPOCH`` set to the commit time. Two rounds must be
byte-identical or the build fails: reproducibility is proven, not assumed.

Adapters:

``python-uv``  ``uv build --sdist --wheel``. setuptools ignores
               ``SOURCE_DATE_EPOCH`` for sdist members (tar member mtimes and the
               gzip header differ per build), so only the sdist archive
               *envelope* is normalised: members sorted, ``mtime`` = epoch,
               uid/gid 0, empty uname/gname, PAX headers dropped, gzip mtime 0
               and no filename. Every member's name, type, mode, link target and
               content digest is proven unchanged by the normalisation.
               Wheels are not touched.
``node-npm``   ``npm pack --ignore-scripts``. The package's published content
               must be committed; lifecycle scripts do not run at release.

The make-v1 phases (``setup validate check test``) are a precondition.
``--phases run`` executes them in a separate clean clone of the exact commit
first; Core's release runner always uses it, because org CI never evaluates a
consumer's default-branch commit. ``--phases verified-upstream`` is for a
caller holding its own green evidence at that exact SHA (for example a manual
release right after verification). There is no default.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import pathlib
import subprocess
import tarfile
from dataclasses import dataclass

from . import gitops
from .config import ReleaseConfig
from .errors import ReleaseInfrastructureError, ReleaseRefused
from .identity import read_version, release_identity

SUMS = "SHA256SUMS"
BUILD_RECORD = "build-record.json"
BUILD_RECORD_SCHEMA = "l9.release-build/v1"
PHASES = ("setup", "validate", "check", "test")
PHASE_MODES = ("verified-upstream", "run")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# sdist envelope normalisation
# ---------------------------------------------------------------------------

Member = tuple[str, bytes, int, str, str | None, int]


def _members(archive: tarfile.TarFile) -> dict[str, Member]:
    members: dict[str, Member] = {}
    for info in archive.getmembers():
        digest: str | None = None
        if info.isfile():
            stream = archive.extractfile(info)
            if stream is None:
                raise ReleaseInfrastructureError(f"unreadable sdist member {info.name}")
            digest = sha256(stream.read())
        if info.name in members:
            raise ReleaseRefused(f"sdist has a duplicate member {info.name!r}")
        members[info.name] = (
            info.name,
            info.type,
            info.mode,
            info.linkname,
            digest,
            info.size,
        )
    return members


def normalise_sdist(data: bytes, epoch: int) -> bytes:
    """Return the sdist with a deterministic envelope and identical members."""

    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as source:
            before = _members(source)
            entries: list[tuple[tarfile.TarInfo, bytes | None]] = []
            for info in sorted(source.getmembers(), key=lambda item: item.name):
                content: bytes | None = None
                if info.isfile():
                    stream = source.extractfile(info)
                    if stream is None:
                        raise ReleaseInfrastructureError(f"unreadable {info.name}")
                    content = stream.read()
                clean = tarfile.TarInfo(info.name)
                clean.type = info.type
                clean.mode = info.mode
                clean.linkname = info.linkname
                clean.size = len(content) if content is not None else 0
                clean.mtime = epoch
                clean.uid = clean.gid = 0
                clean.uname = clean.gname = ""
                clean.devmajor, clean.devminor = info.devmajor, info.devminor
                entries.append((clean, content))
    except (tarfile.TarError, OSError, EOFError) as error:
        raise ReleaseRefused(f"sdist is not a readable .tar.gz: {error}") from error

    tar_bytes = io.BytesIO()
    with tarfile.open(fileobj=tar_bytes, mode="w", format=tarfile.PAX_FORMAT) as target:
        for clean, content in entries:
            target.addfile(clean, io.BytesIO(content) if content is not None else None)
    compressed = io.BytesIO()
    with gzip.GzipFile(
        filename="", mode="wb", fileobj=compressed, mtime=0, compresslevel=9
    ) as gz:
        gz.write(tar_bytes.getvalue())
    result = compressed.getvalue()

    with tarfile.open(fileobj=io.BytesIO(result), mode="r:gz") as check:
        after = _members(check)
    if before != after:
        changed = sorted(
            name
            for name in set(before) | set(after)
            if before.get(name) != after.get(name)
        )
        raise ReleaseInfrastructureError(
            "sdist normalisation changed member identity: " + ", ".join(changed)
        )
    return result


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


def adapter_command(adapter: str, out_dir: pathlib.Path) -> list[str]:
    if adapter == "python-uv":
        return ["uv", "build", "--sdist", "--wheel", "--out-dir", str(out_dir)]
    if adapter == "node-npm":
        return ["npm", "pack", "--ignore-scripts", "--pack-destination", str(out_dir)]
    raise ReleaseRefused(f"unsupported adapter {adapter!r}")


def _environment(epoch: int) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "SOURCE_DATE_EPOCH": str(epoch),
            "TZ": "UTC",
            "LC_ALL": "C.UTF-8",
            "PYTHONHASHSEED": "0",
            "UV_NO_PROGRESS": "1",
            "npm_config_update_notifier": "false",
            "npm_config_fund": "false",
            "npm_config_audit": "false",
        }
    )
    return env


def _run(command: list[str], cwd: pathlib.Path, env: dict[str, str], what: str) -> None:
    try:
        completed = subprocess.run(
            command, cwd=cwd, env=env, text=True, capture_output=True, check=False
        )
    except OSError as error:
        raise ReleaseInfrastructureError(f"{what}: cannot run {command[0]}: {error}")
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout).strip()[-2000:]
        raise ReleaseRefused(f"{what} failed ({completed.returncode}): {tail}")


#: Files a build tool writes into its output directory that are not artifacts:
#: ``uv build`` drops a ``.gitignore`` (``*``) into ``--out-dir``.
TOOL_DROPPINGS = {"python-uv": frozenset({".gitignore"}), "node-npm": frozenset()}


def _collect(adapter: str, out_dir: pathlib.Path) -> dict[str, bytes]:
    files = {
        path.name: path.read_bytes()
        for path in sorted(out_dir.iterdir())
        if path.is_file() and path.name not in TOOL_DROPPINGS[adapter]
    }
    names = set(files)
    if adapter == "python-uv":
        sdists = [name for name in names if name.endswith(".tar.gz")]
        wheels = [name for name in names if name.endswith(".whl")]
        if len(sdists) != 1 or not wheels or len(names) != len(sdists) + len(wheels):
            raise ReleaseRefused(
                f"python-uv must produce one sdist and at least one wheel: {sorted(names)}"
            )
    else:
        if len(names) != 1 or not next(iter(names)).endswith(".tgz"):
            raise ReleaseRefused(f"node-npm must produce one .tgz: {sorted(names)}")
    return files


def build_round(
    workspace: pathlib.Path,
    adapter: str,
    commit: str,
    epoch: int,
    scratch: pathlib.Path,
) -> dict[str, bytes]:
    source = scratch / "src"
    out_dir = scratch / "out"
    gitops.clean_clone(workspace, source, commit)
    out_dir.mkdir(parents=True)
    _run(
        adapter_command(adapter, out_dir),
        source,
        _environment(epoch),
        f"{adapter} build",
    )
    files = _collect(adapter, out_dir)
    if adapter == "python-uv":
        files = {
            name: normalise_sdist(data, epoch) if name.endswith(".tar.gz") else data
            for name, data in files.items()
        }
    return files


def run_phases(
    workspace: pathlib.Path, commit: str, epoch: int, scratch: pathlib.Path
) -> None:
    source = scratch / "phases"
    gitops.clean_clone(workspace, source, commit)
    _run(["make", *PHASES], source, _environment(epoch), "make-v1 phases")


def sums_text(files: dict[str, bytes]) -> str:
    return "".join(f"{sha256(files[name])}  {name}\n" for name in sorted(files))


@dataclass(frozen=True)
class BuildResult:
    version: str
    commit: str
    epoch: int
    artifacts: dict[str, str]


def build(
    workspace: pathlib.Path,
    config: ReleaseConfig,
    commit: str,
    out_dir: pathlib.Path,
    scratch: pathlib.Path,
    phases: str,
) -> BuildResult:
    if phases not in PHASE_MODES:
        raise ReleaseRefused(f"--phases must be one of {PHASE_MODES}")
    gitops.require_clean(workspace)
    commit = gitops.resolve_commit(workspace, commit)
    epoch = gitops.commit_epoch(workspace, commit)
    version = read_version(
        config.version_authority,
        gitops.file_at(workspace, commit, config.version_authority),
    )
    release_identity(version)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ReleaseRefused(f"output directory {out_dir} is not empty")
    scratch.mkdir(parents=True, exist_ok=True)
    if phases == "run":
        run_phases(workspace, commit, epoch, scratch)

    first = build_round(workspace, config.adapter, commit, epoch, scratch / "round-1")
    second = build_round(workspace, config.adapter, commit, epoch, scratch / "round-2")
    if first != second:
        lines = [
            f"  {name}: {sha256(first[name]) if name in first else '-'} vs "
            f"{sha256(second[name]) if name in second else '-'}"
            for name in sorted(set(first) | set(second))
        ]
        raise ReleaseRefused("build is not reproducible:\n" + "\n".join(lines))

    out_dir.mkdir(parents=True, exist_ok=True)
    for name, data in first.items():
        (out_dir / name).write_bytes(data)
    (out_dir / SUMS).write_text(sums_text(first), encoding="utf-8")
    artifacts = {name: sha256(data) for name, data in sorted(first.items())}
    record = {
        "schema": BUILD_RECORD_SCHEMA,
        "adapter": config.adapter,
        "version": version,
        "commit": commit,
        "source_date_epoch": epoch,
        "rounds": 2,
        "phases": phases,
        "artifacts": artifacts,
    }
    # The record is not an asset: publish uploads only the files SHA256SUMS
    # names, plus SHA256SUMS itself, and reads the record for provenance.
    (out_dir / BUILD_RECORD).write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return BuildResult(version=version, commit=commit, epoch=epoch, artifacts=artifacts)
