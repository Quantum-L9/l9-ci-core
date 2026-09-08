#!/usr/bin/env python3
"""Run a consumer repository's canonical execution contract with pinned Core code."""

from __future__ import annotations

import os
import sys
from pathlib import Path

CORE_ROOT = Path(__file__).resolve().parents[3]
CORE_TOOLS = CORE_ROOT / "tools"
if str(CORE_TOOLS) not in sys.path:
    sys.path.insert(0, str(CORE_TOOLS))

from l9_repo.__main__ import RepositoryWorkflow  # noqa: E402


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
    try:
        workflow = RepositoryWorkflow(workspace)

        # Setup is preparatory. The three authoritative verification phases remain
        # validate -> check -> test. Contract parsing and command execution are
        # performed by this immutable Core checkout, never consumer-vendored
        # tools/l9_repo code.
        workflow.setup()
        workflow.validate()
        workflow.check()
        workflow.test()
    except Exception as exc:
        # Expected repository-verification failures become typed evidence so the
        # existing SDK/Semgrep analysis can still finish. The workflow's final
        # enforcement step fails blocking profiles on any non-pass typed result.
        _write_output("status", "fail")
        print(f"repository verification: FAIL ({exc})", file=sys.stderr)
        return 0

    _write_output("status", "pass")
    print("repository verification: PASS (validate + check + test)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
