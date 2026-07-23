#!/usr/bin/env python3
"""Preview and apply the L9 org-level required-status-checks ruleset.

Reads docs/governance/org-ruleset/l9-required-checks.ruleset.json and either:
  - previews current pass/fail state of the required checks across every
    scoped repo (no API mutation), or
  - creates/updates the actual organization ruleset via the GitHub API
    (requires --confirm; defaults to dry-run).

This does NOT run automatically. It is a manually-invoked control-plane tool.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ORG = "Quantum-L9"
RULESET_PATH = (
    Path(__file__).resolve().parent.parent
    / "docs"
    / "governance"
    / "org-ruleset"
    / "l9-required-checks.ruleset.json"
)


def load_ruleset() -> dict:
    return json.loads(RULESET_PATH.read_text())


def gh_api(args: list[str], input_json: dict | None = None) -> dict:
    cmd = ["gh", "api", *args]
    kwargs: dict = {"capture_output": True, "text": True}
    if input_json is not None:
        cmd += ["--input", "-"]
        kwargs["input"] = json.dumps(input_json)
    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0:
        msg = f"gh api failed: {result.stderr.strip()}"
        raise RuntimeError(msg)
    return json.loads(result.stdout) if result.stdout.strip() else {}


def preview(ruleset: dict) -> int:
    """Show current CI status for the required checks across every scoped repo."""
    repos = ruleset["conditions"]["repository_name"]["include"]
    required = [c["context"] for c in ruleset["rules"][0]["parameters"]["required_status_checks"]]

    print(f"Required checks: {required}")
    print(f"Scoped repos: {len(repos)}\n")

    blocking = []
    for repo in repos:
        result = subprocess.run(
            ["gh", "api", f"repos/{ORG}/{repo}/commits/main/check-runs", "--paginate", "--jq",
             ".check_runs[] | {name, conclusion}"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"{repo:<35} ERROR: {result.stderr.strip()[:80]}")
            continue

        latest_by_name: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            run = json.loads(line)
            name = run.get("name", "")
            if name not in latest_by_name:
                latest_by_name[name] = run.get("conclusion") or "unknown"

        missing = [c for c in required if c not in latest_by_name]
        failing = [c for c in required if latest_by_name.get(c) not in (None, "success")]

        status = "OK" if not missing and not failing else "WOULD_BLOCK"
        if status == "WOULD_BLOCK":
            blocking.append(repo)
        detail = f"missing={missing} failing={[c for c in failing if c not in missing]}" if status == "WOULD_BLOCK" else ""
        print(f"{repo:<35} {status:<12} {detail}")

    print(f"\n{len(blocking)}/{len(repos)} repos would be blocked if enforcement=active today.")
    return 0


def create(ruleset: dict, confirm: bool) -> int:
    if not confirm:
        print("DRY RUN — would POST this ruleset (pass --confirm to actually create it):\n")
        print(json.dumps(ruleset, indent=2))
        return 0
    created = gh_api(["-X", "POST", f"orgs/{ORG}/rulesets"], input_json=ruleset)
    print(f"Created ruleset id={created.get('id')} enforcement={created.get('enforcement')}")
    return 0


def update(ruleset: dict, ruleset_id: str, confirm: bool) -> int:
    if not confirm:
        print(f"DRY RUN — would PUT this ruleset to orgs/{ORG}/rulesets/{ruleset_id} (pass --confirm to apply):\n")
        print(json.dumps(ruleset, indent=2))
        return 0
    updated = gh_api(["-X", "PUT", f"orgs/{ORG}/rulesets/{ruleset_id}"], input_json=ruleset)
    print(f"Updated ruleset id={updated.get('id')} enforcement={updated.get('enforcement')}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("preview", help="Show current pass/fail state of required checks (no mutation)")

    create_parser = sub.add_parser("create", help="POST a new org ruleset")
    create_parser.add_argument("--confirm", action="store_true", help="Actually create it (default: dry-run)")

    update_parser = sub.add_parser("update", help="PUT an existing org ruleset by id")
    update_parser.add_argument("ruleset_id", help="Existing ruleset id, e.g. 18226001 (Quantum AI Policy)")
    update_parser.add_argument("--confirm", action="store_true", help="Actually update it (default: dry-run)")

    args = parser.parse_args()
    ruleset = load_ruleset()

    if args.command == "preview":
        return preview(ruleset)
    if args.command == "create":
        return create(ruleset, args.confirm)
    if args.command == "update":
        return update(ruleset, args.ruleset_id, args.confirm)
    return 1


if __name__ == "__main__":
    sys.exit(main())
