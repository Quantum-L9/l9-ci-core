#!/usr/bin/env python3
"""Run a consumer repository's canonical execution contract with pinned Core code."""
from __future__ import annotations

import os
import sys
from pathlib import Path

CORE_ROOT = Path(__file__).resolve().parents[3]
if str(CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(CORE_ROOT))

from tools.l9_repo.__main__ import RepositoryWorkflow  # noqa: E402


def _write_output(name: str, value: str) -> None:
    output = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not output:
        return
    with Path(output).open("a", encoding="utf-8") as stream:
        stream.write(f"{name}={value}\n")


def main() -> int:
    workspace = Path(os.environ.get("L9_REPOSITORY_WORKSPACE", ".")).resolve()
    contract = workspace / ".l9" / "repo-workflow.json"
    if not contract.is_file():
        _write_output("present", "false")
        _write_output("status", "not_applicable")
        print("repository verification: NOT_APPLICABLE (.l9/repo-workflow.json absent)")
        return 0

    _write_output("present", "true")
    workflow = RepositoryWorkflow(workspace)

    # Setup is preparatory. The three authoritative verification phases remain
    # validate -> check -> test. All contract parsing and command execution is
    # performed by this immutable Core checkout, never consumer-vendored
    # tools/l9_repo code.
    workflow.setup()
    workflow.validate()
    workflow.check()
    workflow.test()

    _write_output("status", "pass")
    print("repository verification: PASS (validate + check + test)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
