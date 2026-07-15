#!/usr/bin/env python3
"""Reconcile a build spec against an in-scope repo, fail-closed on drift.

Presence is NOT conformance. When the repo is available, every spec item is
*verified* against what is actually built, and the work queue is compressed to
the real delta — but nothing is marked done merely because a file exists.

Per-item verdicts:
  conformant         found AND a deterministic conformance check passed
  present_unverified found, but conformance is not statically decidable -> needs
                     an agent/test to confirm (NOT counted as done)
  drifted            found but the conformance check FAILED, OR the spec declares
                     it `existing` yet the repo lacks it (inverse drift)
  absent             not built -> stays in the work queue
  external           owned by another repo (contracts[].owner_repo) -> out of scope
  deferred           spec-deferred

Compression: conformant + external + deferred drop out of remaining_work.
absent + present_unverified + drifted remain (drifted first — it must be reconciled).

Deterministic conformance checks: path presence (dirs + `*` globs), contract JSON
has a `$id`, and command wiring (an `npm run X` maps to package.json scripts.X, a
path command resolves to a file). Anything deeper is present_unverified, never done.

Exit codes: 0 no drift, 1 drift found, 2 unreadable spec/repo.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _load(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except Exception:
        try:
            import yaml  # type: ignore[import-not-found]

            return yaml.safe_load(text)
        except Exception as exc:
            raise ValueError(f"cannot parse {path.name} as json/yaml: {exc}") from exc


def _path_present(repo: Path, rel: str) -> bool:
    rel = rel.rstrip("/")
    if "*" in rel:
        return any(repo.glob(rel))
    p = repo / rel
    return p.exists()


def _package_scripts(repo: Path) -> dict[str, Any]:
    pkg = repo / "package.json"
    if not pkg.exists():
        return {}
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
        scripts = data.get("scripts", {})
        return scripts if isinstance(scripts, dict) else {}
    except Exception:
        return {}


def _check_file_plan(repo: Path, spec: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for entry in spec.get("file_plan", []) or []:
        path = entry.get("path", "")
        status = entry.get("status", "build")
        present = _path_present(repo, path)
        if status == "deferred":
            verdict, reason = "deferred", "spec-deferred"
        elif status == "existing":
            verdict = "conformant" if present else "drifted"
            reason = "exists" if present else "spec declares `existing` but repo lacks it"
        else:  # build | extract | adapt
            verdict = "present_unverified" if present else "absent"
            reason = "present — verify against contract" if present else "not built"
        out.append(
            {"kind": "file", "path": path, "status": status, "verdict": verdict, "reason": reason}
        )
    return out


def _check_contracts(repo: Path, spec: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in spec.get("contracts", []) or []:
        path = c.get("path", "")
        if c.get("owner_repo"):
            out.append(
                {
                    "kind": "contract",
                    "path": path,
                    "verdict": "external",
                    "reason": f"owned by {c['owner_repo']}",
                }
            )
            continue
        p = repo / path
        if not p.exists():
            out.append(
                {"kind": "contract", "path": path, "verdict": "absent", "reason": "not present"}
            )
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and str(doc.get("$id", "")).strip():
                out.append(
                    {"kind": "contract", "path": path, "verdict": "conformant", "reason": "has $id"}
                )
            else:
                out.append(
                    {
                        "kind": "contract",
                        "path": path,
                        "verdict": "drifted",
                        "reason": "missing $id",
                    }
                )
        except Exception as exc:
            out.append(
                {
                    "kind": "contract",
                    "path": path,
                    "verdict": "drifted",
                    "reason": f"unparseable: {exc}",
                }
            )
    return out


def _check_commands(repo: Path, spec: dict[str, Any]) -> list[dict[str, Any]]:
    scripts = _package_scripts(repo)
    out: list[dict[str, Any]] = []
    for cmd in spec.get("commands", []) or []:
        name = cmd.get("name", "")
        raw = str(cmd.get("cmd", "")).strip()
        first = raw.split()[0] if raw else ""
        verdict, reason = "present_unverified", "not statically checkable"
        if raw.startswith("npm run ") or raw.startswith("pnpm run "):
            script = raw.split("run", 1)[1].strip().split()[0]
            if script in scripts:
                verdict, reason = "conformant", f"wired: package.json scripts.{script}"
            elif scripts:
                verdict, reason = "drifted", f"declared but package.json has no scripts.{script}"
            else:
                verdict, reason = "absent", "no package.json"
        elif "/" in first:  # a path-based command
            verdict = "conformant" if _path_present(repo, first) else "absent"
            reason = "script present" if verdict == "conformant" else "script not present"
        out.append({"kind": "command", "name": name, "verdict": verdict, "reason": reason})
    return out


COMPRESSED = {"conformant", "external", "deferred"}


def reconcile(repo: Path, spec: dict[str, Any]) -> dict[str, Any]:
    items = (
        _check_file_plan(repo, spec) + _check_contracts(repo, spec) + _check_commands(repo, spec)
    )
    counts: dict[str, int] = {}
    for it in items:
        counts[it["verdict"]] = counts.get(it["verdict"], 0) + 1
    drift = [it for it in items if it["verdict"] == "drifted"]
    remaining = [it for it in items if it["verdict"] not in COMPRESSED]
    compressed_out = [it for it in items if it["verdict"] in COMPRESSED]
    # drift-first ordering in the remaining work
    remaining.sort(key=lambda it: 0 if it["verdict"] == "drifted" else 1)
    return {
        "repo": str(repo),
        "counts": counts,
        "drift": drift,
        "remaining_work": remaining,
        "compressed_out_count": len(compressed_out),
        "note": "present_unverified is NOT done — conformance needs an agent/test. Nothing is done by presence alone.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile a build spec against an in-scope repo.")
    parser.add_argument("spec", help="Path to a build spec (json/yaml)")
    parser.add_argument("--repo", default=".", help="Repo root to reconcile against (default: cwd)")
    parser.add_argument("--json", action="store_true", help="Emit the full report as JSON")
    args = parser.parse_args()
    spec_path, repo = Path(args.spec), Path(args.repo)
    if not spec_path.is_file():
        print(f"FAIL: not a file: {spec_path}", file=sys.stderr)
        return 2
    if not repo.is_dir():
        print(f"FAIL: not a directory: {repo}", file=sys.stderr)
        return 2
    try:
        spec = _load(spec_path)
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    report = reconcile(repo, spec)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"counts={report['counts']}")
        print(
            f"compressed_out={report['compressed_out_count']} (conformant/external/deferred dropped)"
        )
        if report["drift"]:
            print("DRIFT (present but non-conformant — reconcile, do not skip):")
            for d in report["drift"]:
                print(f"  - {d.get('path') or d.get('name')}: {d['reason']}")
        print(
            f"remaining_work ({len(report['remaining_work'])} items — the delta to build/verify):"
        )
        for it in report["remaining_work"]:
            print(f"  - [{it['verdict']}] {it.get('path') or it.get('name')}: {it['reason']}")
    return 1 if report["drift"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
