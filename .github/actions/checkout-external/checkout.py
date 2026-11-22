"""Check out the one repository a consumer declares.

The declaration is ``.l9/external-checkout.json`` in the consumer workspace.
``L9_GITHUB_READ_TOKEN`` reads Quantum-L9 repositories. A repository owned
outside the organization (``cryptoxdog/IB-Odoo_19``) is read with
``L9_EXTERNAL_READ_TOKEN``: a fine-grained token covers one resource owner, so
the organization token cannot read it. Both are removed from the process
environment before git runs. It reaches git only as a per-command ``http.extraheader`` for
``https://github.com/``, never as an argument, a remote URL, or a variable
for a later step. A missing declaration does nothing unless
``L9_EXTERNAL_CHECKOUT_REQUIRED`` is true.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path

READ_TOKEN_ENV = "L9_GITHUB_READ_TOKEN"
EXTERNAL_TOKEN_ENV = "L9_EXTERNAL_READ_TOKEN"
ORGANIZATION = "Quantum-L9"
WORKSPACE_ENV = "L9_REPOSITORY_WORKSPACE"
REQUIRED_ENV = "L9_EXTERNAL_CHECKOUT_REQUIRED"
MANIFEST = Path(".l9") / "external-checkout.json"
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA = re.compile(r"^[0-9a-f]{40}$")
PATH_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,40}$")


class CheckoutError(Exception):
    """The declaration or the fetch cannot be used."""


def github_auth_env(token: str, base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment that authenticates git to https://github.com/ with ``token``."""
    env = dict(os.environ if base is None else base)
    env.pop(READ_TOKEN_ENV, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    if not token:
        return env
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    index = int(env.get("GIT_CONFIG_COUNT", "0") or "0")
    env[f"GIT_CONFIG_KEY_{index}"] = "http.https://github.com/.extraheader"
    env[f"GIT_CONFIG_VALUE_{index}"] = f"AUTHORIZATION: basic {basic}"
    env["GIT_CONFIG_COUNT"] = str(index + 1)
    return env


def _required() -> bool:
    return os.environ.get(REQUIRED_ENV, "").strip().lower() in {"1", "true"}


def load_declaration(workspace: Path) -> dict[str, str] | None:
    """The declared checkout, or None when the consumer did not declare one."""
    manifest = workspace / MANIFEST
    if not manifest.is_file():
        if _required():
            raise CheckoutError(f"missing {MANIFEST.as_posix()}")
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CheckoutError(f"invalid {MANIFEST.as_posix()}") from exc
    if not isinstance(data, dict):
        raise CheckoutError(f"invalid {MANIFEST.as_posix()}")
    repository = data.get("repository")
    revision = data.get("ref")
    relative = data.get("path")
    variable = data.get("env")
    if not isinstance(repository, str) or REPOSITORY.fullmatch(repository) is None:
        raise CheckoutError("repository must be owner/name")
    if not isinstance(revision, str) or SHA.fullmatch(revision) is None:
        raise CheckoutError("ref must be a full lowercase commit SHA")
    if not isinstance(relative, str) or PATH_NAME.fullmatch(relative) is None:
        raise CheckoutError("path must be a single relative directory name")
    if not isinstance(variable, str) or ENV_NAME.fullmatch(variable) is None:
        raise CheckoutError("env must be an uppercase environment variable name")
    return {
        "repository": repository,
        "ref": revision,
        "path": relative,
        "env": variable,
    }


def fetch_revision(
    destination: Path, repository: str, revision: str, token: str
) -> None:
    """Fetch ``revision`` of ``owner/name`` into a new git directory."""
    if destination.exists():
        raise CheckoutError(f"checkout path already exists: {destination.name}")
    destination.mkdir()
    url = f"https://github.com/{repository}.git"
    commands = (
        ["git", "init", "--quiet"],
        ["git", "remote", "add", "origin", url],
        [
            "git",
            "-c",
            "protocol.version=2",
            "fetch",
            "--quiet",
            "--depth=1",
            "origin",
            revision,
        ],
        ["git", "checkout", "--quiet", "--detach", "FETCH_HEAD"],
    )
    for command in commands:
        env = (
            github_auth_env(token)
            if command[1:4] == ["-c", "protocol.version=2", "fetch"]
            else None
        )
        completed = subprocess.run(command, cwd=destination, env=env, check=False)
        if completed.returncode != 0:
            raise CheckoutError(f"git failed ({completed.returncode})")
    probed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=destination,
        capture_output=True,
        text=True,
        check=False,
    )
    actual = probed.stdout.strip()
    if probed.returncode != 0 or actual != revision:
        raise CheckoutError("checked out revision does not match the declaration")


def token_for(repository: str, org_token: str, external_token: str) -> str:
    """The read token for ``owner/name``.

    The organization token for a Quantum-L9 repository; the external token for
    any other owner, falling back to the organization token when no external
    token is configured (a public repository then still reads anonymously).
    """

    owner = repository.split("/", 1)[0]
    if owner.lower() != ORGANIZATION.lower() and external_token:
        return external_token
    return org_token


def publish(name: str, value: str) -> None:
    """Export ``name`` to later steps. The value is a directory path."""
    handle = os.environ.get("GITHUB_ENV")
    if not handle:
        return
    with open(handle, "a", encoding="utf-8") as stream:
        stream.write(f"{name}={value}\n")


def main() -> int:
    org_token = os.environ.pop(READ_TOKEN_ENV, "").strip()
    external_token = os.environ.pop(EXTERNAL_TOKEN_ENV, "").strip()
    workspace = Path(os.environ.get(WORKSPACE_ENV) or ".").resolve()
    try:
        declared = load_declaration(workspace)
        if declared is None:
            return 0
        destination = workspace / declared["path"]
        token = token_for(declared["repository"], org_token, external_token)
        fetch_revision(destination, declared["repository"], declared["ref"], token)
        publish(declared["env"], str(destination))
    except CheckoutError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
