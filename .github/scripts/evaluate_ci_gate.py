#!/usr/bin/env python3
"""Evaluate the L9 PR Pipeline Gate for L9 CI-debt nodes (rule-mode aware).

Debt-node policy ("build the rails first"):
  - Blocking conditions are derived from `.github/governance/rule-modes.yaml`.
    A rule blocks merge only when its mode resolves to `blocking`.
  - By debt-node default only `SECRET-LEAK-NEW` is blocking: a newly-introduced
    secret in the PR commit range (secret_scan job failed OR secret_findings>0).
  - `UNKNOWN-DIFF` follows its rule mode (advisory on debt nodes today; the
    canonical mature default is fail-closed/blocking). This is the one lever a
    platform owner flips to promote the node toward canonical strictness.
  - Every other gate (lint, type, audit, semgrep, supply-chain, docs, skipped
    optional jobs) is advisory and surfaced but never blocks.

Reads ci_context.json (path via CI_CONTEXT_PATH) and rule-modes policy
(path via RULE_MODES_PATH). Exit code 1 == gate blocks merge.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

FAIL_RESULTS = {"failure", "timed_out", "action_required"}
VALID_MODES = {"blocking", "advisory", "shadow", "disabled"}
DEFAULT_RULE_MODES_PATH = ".github/governance/rule-modes.yaml"


def _load_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[WARN] could not read {path}: {exc}", file=sys.stderr)
        return {}


def _load_rule_modes() -> tuple[str, dict[str, str]]:
    """Return (default_mode, {RULE_ID: mode}). Std-lib only; tolerant parser.

    Avoids a hard PyYAML dependency in the gate step: rule-modes.yaml is a flat
    `key: value` mapping under `rules:` plus a top-level `default_mode:`.
    """
    path = os.environ.get("RULE_MODES_PATH", DEFAULT_RULE_MODES_PATH)
    default_mode = "advisory"
    rules: dict[str, str] = {}
    text = ""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        print(
            f"[WARN] rule-modes policy unreadable ({path}): {exc}; using advisory default",
            file=sys.stderr,
        )
        return default_mode, rules

    in_rules = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            in_rules = stripped.startswith("rules:")
            if stripped.startswith("default_mode:"):
                val = stripped.split(":", 1)[1].strip().strip("\"'").lower()
                if val in VALID_MODES:
                    default_mode = val
            continue
        if in_rules and ":" in stripped:
            key, _, val = stripped.partition(":")
            mode = val.strip().strip("\"'").lower()
            key = key.strip()
            if key and mode in VALID_MODES:
                rules[key] = mode
    return default_mode, rules


def _mode_for(rule_id: str, default_mode: str, rules: dict[str, str]) -> str:
    for candidate in (rule_id, rule_id.upper(), rule_id.replace("_", "-").upper()):
        if candidate in rules:
            return rules[candidate]
    return default_mode


def main() -> int:
    ctx = _load_json(os.environ.get("CI_CONTEXT_PATH", "ci_context.json"))
    default_mode, rule_modes = _load_rule_modes()
    results = ctx.get("results", {}) if isinstance(ctx.get("results"), dict) else {}

    blocking: list[str] = []
    advisory: list[str] = []
    shadow: list[str] = []

    def route(rule_id: str, detail: str) -> None:
        mode = _mode_for(rule_id, default_mode, rule_modes)
        entry = f"{mode}:{rule_id}:{detail}"
        if mode == "blocking":
            blocking.append(entry)
        elif mode == "shadow":
            shadow.append(entry)
        elif mode == "disabled":
            return
        else:
            advisory.append(entry)

    # --- New-secret rail (diff-scoped) -----------------------------------
    secret_result = str(results.get("secret_scan", "missing")).lower()
    secret_findings = int(ctx.get("secret_findings", 0) or 0)
    if secret_result in FAIL_RESULTS or secret_findings > 0:
        route("SECRET-LEAK-NEW", f"findings={secret_findings or 'job_failed'}")

    # --- Unknown-diff (canonical fail-closed; advisory on debt) ----------
    if ctx.get("diff_unknown"):
        route("UNKNOWN-DIFF", "unclassified_changed_files")

    # --- Every other job -> advisory ------------------------------------
    for job, result in results.items():
        if job == "secret_scan":
            continue
        res = str(result).lower()
        if res in FAIL_RESULTS or res == "missing":
            advisory.append(f"advisory:{job}:{res}")

    verdict = {
        "result": "FAIL" if blocking else "PASS",
        "merge_meaning": "blocked_by_real_governance" if blocking else "advisory_only",
        "blocking": blocking,
        "advisory": advisory,
        "shadow": shadow,
        "secret_findings": secret_findings,
        "pr_class": ctx.get("pr_class", "unknown"),
        "default_mode": default_mode,
    }
    print(json.dumps(verdict, indent=2, sort_keys=True))

    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write(f"gate_result={verdict['result']}\n")
            fh.write(f"merge_meaning={verdict['merge_meaning']}\n")
            fh.write(f"blocking_count={len(blocking)}\n")

    if blocking:
        print("[FAIL] PR Pipeline Gate: merge blocked by a blocking-mode rule.")
        return 1
    print("[PASS] PR Pipeline Gate: no blocking-mode rule triggered (advisories/shadow may exist).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
