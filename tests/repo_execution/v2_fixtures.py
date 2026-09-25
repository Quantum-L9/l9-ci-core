"""Shared fixtures for the Repository Execution V2 suite.

Imported by path (each test module puts its own directory on ``sys.path``) so
the suite behaves the same under ``unittest discover`` from the repository
root, from ``tests``, and from ``tests/repo_execution``.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from l9_repo.contract import PHASES, V2_SCHEMA_NAME, render_facade  # noqa: E402

GIT_ENV = {
    **os.environ,
    # Keep the developer's ~/.gitconfig (hooks, signing, excludes) out of fixtures.
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}


def v2_contract(**overrides: object) -> dict[str, object]:
    contract: dict[str, object] = {
        "schema": V2_SCHEMA_NAME,
        "facade": "make-v1",
        "required_phases": list(PHASES),
    }
    contract.update(overrides)
    return contract


def git(root: pathlib.Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
        env=GIT_ENV,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed ({result.returncode}) in {root}\n"
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout


def init_repo(root: pathlib.Path) -> None:
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "tests@example.com")
    git(root, "config", "user.name", "Tests")
    git(root, "config", "gc.auto", "0")
    git(root, "config", "maintenance.auto", "false")


def commit_all(root: pathlib.Path, message: str) -> None:
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", message)


def logging_repo_mk(*, failing: str | None = None, extra: str = "") -> str:
    """A repository-owned Repo.mk whose leaves append their phase to phases.log."""

    lines = [
        "# repository-owned implementation",
        ".PHONY: " + " ".join(f"repo-{phase}" for phase in PHASES),
        "",
    ]
    for phase in PHASES:
        lines.append(f"repo-{phase}:")
        lines.append(f"\t@printf '{phase}\\n' >> phases.log")
        if phase == failing:
            lines.append("\t@exit 1")
        lines.append("")
    return "\n".join(lines) + extra


def write_consumer(
    root: pathlib.Path,
    *,
    contract: object | None = None,
    repo_mk: str | None = None,
    makefile: bytes | None = None,
    init_git: bool = True,
) -> None:
    """Lay out a minimal V2 consumer: contract, generated Makefile, Repo.mk."""

    (root / ".l9").mkdir(parents=True, exist_ok=True)
    document = v2_contract() if contract is None else contract
    (root / ".l9" / "repo-workflow.json").write_text(
        json.dumps(document, indent=2) + "\n", encoding="utf-8"
    )
    (root / "Makefile").write_bytes(
        render_facade("make-v1") if makefile is None else makefile
    )
    (root / "Repo.mk").write_text(
        logging_repo_mk() if repo_mk is None else repo_mk, encoding="utf-8"
    )
    if init_git:
        init_repo(root)
        commit_all(root, "consumer")


def make(
    root: pathlib.Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "--no-print-directory", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def phases_log(root: pathlib.Path) -> list[str]:
    path = root / "phases.log"
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8").split()
