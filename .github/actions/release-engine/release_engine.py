"""Composite-action entrypoint for the release engine.

Translates the action's environment into the ``tools/l9_release`` CLI. The
engine is imported from this pinned Core revision (``CORE_ROOT/tools``), never
from the consumer workspace, mirroring ``run-repository-verification``. The
target is always ``GITHUB_REPOSITORY`` at ``GITHUB_SHA`` from the event
context; no input names another repository or revision.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

CORE_ROOT = Path(__file__).resolve().parents[3]
CORE_TOOLS = CORE_ROOT / "tools"
if str(CORE_TOOLS) not in sys.path:
    sys.path.insert(0, str(CORE_TOOLS))

from l9_release.__main__ import main  # noqa: E402

STAGES = ("plan", "build", "publish", "verify")


def arguments(environ: dict[str, str]) -> list[str]:
    stage = environ.get("L9_RELEASE_STAGE", "")
    if stage not in STAGES:
        raise SystemExit(
            f"release-engine: stage must be one of {STAGES}, got {stage!r}"
        )
    commit = environ.get("L9_RELEASE_COMMIT", "")
    if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        raise SystemExit("release-engine: GITHUB_SHA is not a full commit SHA")
    workspace = environ.get("L9_RELEASE_WORKSPACE") or "."
    argv = ["--workspace", workspace, "--commit", commit, stage]
    dist = environ.get("L9_RELEASE_DIST", "")
    runner_temp = environ.get("RUNNER_TEMP") or str(Path(workspace).resolve().parent)
    if stage == "build":
        phases = environ.get("L9_RELEASE_PHASES", "")
        if not dist or not phases:
            raise SystemExit("release-engine: build requires dist and phases")
        argv += [
            "--out",
            dist,
            "--scratch",
            str(Path(runner_temp) / "l9-release-scratch"),
            "--phases",
            phases,
        ]
    elif stage == "publish":
        if not dist:
            raise SystemExit("release-engine: publish requires dist")
        argv += ["--dist", dist]
    elif stage == "verify" and dist:
        argv += ["--dist", dist]
    return argv


if __name__ == "__main__":
    raise SystemExit(main(arguments(dict(os.environ))))
