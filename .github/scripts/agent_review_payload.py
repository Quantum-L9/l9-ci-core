#!/usr/bin/env python3
"""Emit the future-agent-review payload (Kernel agent_review_loop, advisory-only).

Builds agent_review_payload.json from ci_summary.json + classifier context. This
is the machine-readable state a future agent review loop would CONSUME. The loop
is prepared, not enabled: this script grants no merge authority, requests no
secrets, and posts nothing. It only produces an artifact.

Reads : ci_summary.json (CI_SUMMARY_PATH, default ci_summary.json)
Writes: agent_review_payload.json
Stdlib only.
"""

from __future__ import annotations

import json
import os
import sys


def _load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[WARN] could not read {path}: {exc}", file=sys.stderr)
        return {}


def main() -> int:
    summary = _load(os.environ.get("CI_SUMMARY_PATH", "ci_summary.json"))

    payload = {
        "schema": "l9.agent_review_payload/v1",
        "enabled": False,
        "mode": "advisory_only",
        "merge_authority": False,
        "secret_access": False,
        "inputs": {
            "pr_class": summary.get("pr_class", "unknown"),
            "diff_unknown": summary.get("diff_unknown", False),
            "changed_count": summary.get("changed_count", 0),
            "labels": summary.get("labels", []),
            "relevant_surfaces": summary.get("relevant_surfaces", []),
            "results": summary.get("results", {}),
            "blocking": summary.get("blocking", False),
            "secret_findings": summary.get("secret_findings", 0),
            "advisory_jobs": summary.get("advisory_jobs", []),
        },
        "forbidden": [
            "treating_agent_approval_as_merge_authority",
            "bypassing_pr_pipeline_gate",
            "hiding_failed_checks",
            "creating_new_governance_semantics_without_updating_kernel",
        ],
        "expected_agent_outputs": [
            "structured_review_comment",
            "suggested_patch_plan",
        ],
    }

    with open("agent_review_payload.json", "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
    print("[INFO] wrote agent_review_payload.json (advisory-only, loop not enabled)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
