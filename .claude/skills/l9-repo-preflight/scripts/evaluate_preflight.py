#!/usr/bin/env python3
"""Evaluate the eight preflight gates against a read-only probe log.

Deterministic: parse the probe log (+ an optional expected contract and an
optional baseline record) and emit a per-gate verdict, the Golden-Rule red-line
check, a readiness verdict, and the single smallest next action.

Doctrine (matches references/preflight-pipeline.md):
  - verified evidence outranks the blueprint; a foreign expectation the repo does
    not meet is `adapt` (fix the blueprint), never `blocked` (fail the repo).
  - never continue with unknown files; an unidentified untracked file is a red line.
  - never continue if the baseline cannot be reproduced.
  - classify baseline failures existing (record) vs new (block); only new blocks.
  - autofix (default on) is fenced to safe non-code hygiene; `--strict` disables it
    and treats every fixable NO as a hard stop.

Verdict vocabulary: pass · blocked · confirm · adapt. `ready` is true only when
gates 1-7 are `pass` and no red line is tripped.

Exit codes: 0 ready, 1 not ready (blockers/confirms/adapts remain), 2 unreadable input.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

_SECTION = re.compile(r"^=====\s+(.*?)\s+=====\s*$")
_KV = re.compile(r"^([A-Z0-9_]+)=(.*)$")

# Untracked files matching these are known development artifacts, never "unknown".
_GENERATED = (
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".coverage",
    "htmlcov",
    "build/",
    "dist/",
)
# A path SEGMENT ending in one of these is a generated dir/file anywhere in the path
# (e.g. .github/scripts/pkg.egg-info/PKG-INFO).
_GENERATED_SEG_SUFFIX = (".egg-info", ".dist-info", ".pyc")
# The probe writes its own timestamped log into the worktree; it is a known
# artifact of this tool, not an unknown file. (Gitignore it: repo-preflight-*.log)
_PROBE_LOG = re.compile(r"^repo-preflight-\d{8}T\d{6}Z\.log$")
# Foundations that are genuinely required; missing one is a wrong-checkout blocker,
# not a blueprint mismatch.
_CORE_FOUNDATIONS = ("pyproject.toml", "tests")


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


def parse_sections(text: str) -> dict[str, list[str]]:
    """Split a probe log into {section_name: [lines]}."""
    sections: dict[str, list[str]] = {}
    current = "_preamble"
    sections[current] = []
    for line in text.splitlines():
        m = _SECTION.match(line)
        if m:
            current = m.group(1)
            sections.setdefault(current, [])
            continue
        sections[current].append(line)
    return sections


def _kv(lines: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in lines:
        m = _KV.match(line.strip())
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _is_generated(path: str) -> bool:
    p = path.strip().strip("/")
    segs = p.split("/")
    if _PROBE_LOG.match(segs[-1]):
        return True
    if any(seg in _GENERATED for seg in segs):
        return True
    if any(seg.endswith(_GENERATED_SEG_SUFFIX) for seg in segs):
        return True
    return p.startswith(_GENERATED)


def _untracked(status_lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in status_lines:
        if line.startswith("?? "):
            out.append(line[3:].strip())
    return out


def _present_paths(key_file_lines: list[str]) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for line in key_file_lines:
        s = line.strip()
        if s.startswith("present "):
            out[s[len("present ") :].strip()] = True
        elif s.startswith("missing "):
            out[s[len("missing ") :].strip()] = False
    return out


def _gate(gid: int, name: str, question: str, verdict: str, **extra: Any) -> dict[str, Any]:
    g: dict[str, Any] = {"id": gid, "name": name, "question": question, "verdict": verdict}
    g.update(extra)
    return g


# --------------------------------------------------------------------------- #
# Gate evaluators
# --------------------------------------------------------------------------- #
def gate1_probe(sections: dict[str, list[str]]) -> dict[str, Any]:
    complete = "PROBE COMPLETE" in sections
    core = ["REPOSITORY IDENTITY", "WORKTREE STATUS", "KEY FILE PRESENCE", "PYTHON TOOLCHAIN"]
    missing = [s for s in core if s not in sections]
    failures = sum(line.count("[command failed:") for lines in sections.values() for line in lines)
    if complete and not missing:
        return _gate(
            1,
            "probe-completed",
            "did the probe complete successfully?",
            "pass",
            evidence={"sections": len(sections), "command_failures": failures},
        )
    taxonomy = "missing-command" if failures else "shell-or-git-error"
    return _gate(
        1,
        "probe-completed",
        "did the probe complete successfully?",
        "blocked",
        taxonomy=taxonomy,
        evidence={"complete": complete, "missing_sections": missing, "command_failures": failures},
        remediation="fix the error, re-run the probe end-to-end",
    )


def gate2_identity(sections: dict[str, list[str]], expected: dict[str, Any]) -> dict[str, Any]:
    ident = _kv(sections.get("REPOSITORY IDENTITY", []))
    remotes = "\n".join(sections.get("REMOTES", []))
    branch, head, root = ident.get("BRANCH", ""), ident.get("HEAD", ""), ident.get("ROOT", "")
    ev = {"root": root, "branch": branch, "head": head[:12]}
    if not expected:
        return _gate(
            2,
            "correct-identity",
            "correct repository / branch / commit?",
            "confirm",
            evidence=ev,
            remediation="provide an expected contract or confirm this identity",
        )
    for field, observed, want, tax in (
        ("repo", remotes + " " + root, expected.get("repo"), "wrong-repo"),
        ("branch", branch, expected.get("branch"), "wrong-branch"),
        ("commit", head, expected.get("commit"), "wrong-commit"),
    ):
        if want and str(want) not in observed:
            return _gate(
                2,
                "correct-identity",
                "correct repository / branch / commit?",
                "blocked",
                taxonomy=tax,
                evidence=ev | {"expected_" + field: want},
                remediation=f"{tax.replace('-', ' ')}: correct it, re-run the probe",
            )
    return _gate(
        2, "correct-identity", "correct repository / branch / commit?", "pass", evidence=ev
    )


def gate3_worktree(sections: dict[str, list[str]], strict: bool) -> dict[str, Any]:
    status = _kv(sections.get("WORKTREE STATUS", []))
    tracked = int(status.get("TRACKED_MODIFIED_COUNT", "0") or 0)
    staged = int(status.get("STAGED_COUNT", "0") or 0)
    untracked = _untracked(sections.get("WORKTREE STATUS", []))
    generated = [u for u in untracked if _is_generated(u)]
    unknown = [u for u in untracked if not _is_generated(u)]
    ev = {"tracked_modified": tracked, "staged": staged, "unknown": unknown, "generated": generated}
    if unknown:
        return _gate(
            3,
            "worktree-clean",
            "worktree clean?",
            "blocked",
            taxonomy="unknown-files",
            evidence=ev,
            red_line="never continue with unknown files",
            remediation="identify each unknown file (origin, purpose); remove/revert if unsafe",
        )
    if tracked or staged:
        return _gate(
            3,
            "worktree-clean",
            "worktree clean?",
            "blocked",
            taxonomy="tracked-files",
            evidence=ev,
            remediation="review diffs; commit/stash expected edits or git restore unexpected ones",
        )
    if generated:
        verdict = "blocked" if strict else "pass"
        return _gate(
            3,
            "worktree-clean",
            "worktree clean?",
            verdict,
            taxonomy="generated-files",
            evidence=ev,
            autofix="update .gitignore; remove untracked known-generated files; verify none tracked",
            remediation=None if verdict == "pass" else "strict: resolve generated artifacts first",
        )
    return _gate(3, "worktree-clean", "worktree clean?", "pass", evidence=ev)


def _alt_layout_evidence(sections: dict[str, list[str]]) -> bool:
    """True if the repo clearly uses a non-src Python layout (packages found)."""
    disc = "\n".join(sections.get("PACKAGE DISCOVERY", []))
    if "NOT_IMPORTABLE" not in disc and "=" in disc and ".py" in disc:
        return True
    return any(line.strip().endswith(".py") for line in sections.get("PACKAGE DISCOVERY", []))


def gate4_foundations(sections: dict[str, list[str]], expected: dict[str, Any]) -> dict[str, Any]:
    present = _present_paths(sections.get("KEY FILE PRESENCE", []))
    tstamp = _kv(sections.get("TIMESTAMP", []))
    if expected.get("foundations"):
        wanted = list(expected["foundations"])
    else:
        wanted = (tstamp.get("PROBE_FOUNDATIONS") or "pyproject.toml tests schemas").split()
    missing = [f for f in wanted if present.get(f) is not True]
    ev = {"expected": wanted, "missing": missing}
    if not missing:
        return _gate(4, "foundations-present", "required foundations present?", "pass", evidence=ev)
    core_missing = [m for m in missing if m in _CORE_FOUNDATIONS]
    if core_missing:
        return _gate(
            4,
            "foundations-present",
            "required foundations present?",
            "blocked",
            taxonomy="missing-core (wrong checkout / partial clone)",
            evidence=ev | {"core_missing": core_missing},
            remediation="verify the checkout/branch; re-clone if partial; re-run the probe",
        )
    # non-core missing + a real alternate layout -> the blueprint is wrong for this repo
    if _alt_layout_evidence(sections):
        return _gate(
            4,
            "foundations-present",
            "required foundations present?",
            "adapt",
            taxonomy="missing-but-not-expected (adapt blueprint)",
            evidence=ev,
            remediation="update the blueprint/contract: this repo uses a different layout",
        )
    return _gate(
        4,
        "foundations-present",
        "required foundations present?",
        "confirm",
        taxonomy="missing-uncertain",
        evidence=ev,
        remediation="confirm whether these foundations are expected in this repo",
    )


def _repo_tools(sections: dict[str, list[str]]) -> set[str]:
    tools: set[str] = set()
    present = _present_paths(sections.get("KEY FILE PRESENCE", []))
    meta = "\n".join(sections.get("PROJECT METADATA", []))
    if present.get("ruff.toml") or "ruff" in meta:
        tools.add("ruff")
    if present.get("mypy.ini") or "mypy" in meta:
        tools.add("mypy")
    if present.get("tests") or "pytest" in meta:
        tools.add("pytest")
    for line in sections.get("VALIDATION TOOL AVAILABILITY", []):
        parts = line.split()
        if len(parts) >= 2 and parts[1] != "MISSING":
            tools.add(parts[0])
    return tools


def gate5_toolchain(sections: dict[str, list[str]], expected: dict[str, Any]) -> dict[str, Any]:
    py_lines = sections.get("PYTHON TOOLCHAIN", [])
    python_ok = any("_PATH=" in line for line in py_lines)
    repo_tools = _repo_tools(sections)
    contract = expected.get("toolchain", {}) if expected else {}
    want_tools = set(contract.get("test_tools", []) or [])
    ev = {
        "repo_tools": sorted(repo_tools),
        "python_present": python_ok,
        "wanted": sorted(want_tools),
    }
    if not expected:
        return _gate(
            5,
            "toolchain-matches",
            "toolchain matches execution contract?",
            "confirm",
            evidence=ev,
            remediation="provide the toolchain contract to decide the match",
        )
    unmet = want_tools - repo_tools
    if python_ok and not unmet:
        return _gate(
            5, "toolchain-matches", "toolchain matches execution contract?", "pass", evidence=ev
        )
    # repo defines its own tools -> follow the repo, adapt the contract
    if repo_tools:
        return _gate(
            5,
            "toolchain-matches",
            "toolchain matches execution contract?",
            "adapt",
            taxonomy="repo-defines-tooling (follow the repository)",
            evidence=ev | {"unmet": sorted(unmet)},
            remediation="follow the repo's tools/versions; do NOT replace existing tooling",
        )
    return _gate(
        5,
        "toolchain-matches",
        "toolchain matches execution contract?",
        "blocked",
        taxonomy="missing-toolchain",
        evidence=ev | {"unmet": sorted(unmet)},
        remediation="record a blocker; install/define the required toolchain",
    )


def gate6_install(sections: dict[str, list[str]], expected: dict[str, Any]) -> dict[str, Any]:
    disc_lines = sections.get("PACKAGE DISCOVERY", [])
    imports = {}
    for line in disc_lines:
        m = re.match(r"^([A-Za-z0-9_]+)=(.+)$", line.strip())
        if m:
            imports[m.group(1)] = m.group(2)
    not_importable = [k for k, v in imports.items() if v == "NOT_IMPORTABLE"]
    ev = {"packages": imports}
    if imports and not not_importable:
        return _gate(6, "install-succeeded", "installation succeeded?", "pass", evidence=ev)
    if not imports:
        return _gate(
            6,
            "install-succeeded",
            "installation succeeded?",
            "confirm",
            evidence=ev,
            remediation="run the repo's install method, then re-run the probe to confirm imports",
        )
    disc_text = "\n".join(disc_lines)
    in_repo = [p for p in not_importable if f"/{p}/" in disc_text or f"/{p}." in disc_text]
    foreign = [p for p in not_importable if p not in in_repo]
    if foreign and not in_repo:
        return _gate(
            6,
            "install-succeeded",
            "installation succeeded?",
            "adapt",
            taxonomy="foreign-package (not in this repo)",
            evidence=ev | {"foreign": foreign},
            remediation="update the blueprint: these packages are not part of this repo",
        )
    return _gate(
        6,
        "install-succeeded",
        "installation succeeded?",
        "blocked",
        taxonomy="editable-install (repo package not importable)",
        evidence=ev | {"not_importable": not_importable},
        remediation="run the repo's install (e.g. pip install -e .); re-run the probe",
    )


def gate7_baseline(baseline: dict[str, Any] | None, prior: dict[str, Any] | None) -> dict[str, Any]:
    if baseline is None:
        return _gate(
            7,
            "baseline-reproduces",
            "baseline validation passes?",
            "confirm",
            evidence={},
            remediation="run pytest/mypy/ruff to reproduce the baseline, then re-evaluate",
        )
    results = baseline.get("results", baseline)
    ev = {"results": results}
    if prior is None:
        return _gate(
            7,
            "baseline-reproduces",
            "baseline validation passes?",
            "pass",
            evidence=ev | {"note": "first run: these failures are the existing baseline"},
        )
    prior_results = prior.get("results", prior)
    new: list[str] = []
    for tool, cur in results.items():
        cur_f = int(cur.get("failed", 0)) if isinstance(cur, dict) else 0
        old_f = 0
        if isinstance(prior_results.get(tool), dict):
            old_f = int(prior_results[tool].get("failed", 0))
        if cur_f > old_f:
            new.append(f"{tool}: {old_f} -> {cur_f}")
    if new:
        return _gate(
            7,
            "baseline-reproduces",
            "baseline validation passes?",
            "blocked",
            taxonomy="new-failure",
            evidence=ev | {"new_failures": new},
            red_line="never continue if the baseline cannot be reproduced",
            remediation="fix the new failure(s) before continuing",
        )
    return _gate(
        7,
        "baseline-reproduces",
        "baseline validation passes?",
        "pass",
        evidence=ev | {"note": "no new failures vs prior baseline"},
    )


def evaluate(
    sections: dict[str, list[str]],
    expected: dict[str, Any],
    baseline: dict[str, Any] | None,
    prior: dict[str, Any] | None,
    strict: bool,
) -> dict[str, Any]:
    gates = [
        gate1_probe(sections),
        gate2_identity(sections, expected),
        gate3_worktree(sections, strict),
        gate4_foundations(sections, expected),
        gate5_toolchain(sections, expected),
        gate6_install(sections, expected),
        gate7_baseline(baseline, prior),
    ]
    red_lines = [
        {"rule": g["red_line"], "gate": g["id"], "detail": g.get("taxonomy", "")}
        for g in gates
        if g.get("red_line")
    ]
    ready = all(g["verdict"] == "pass" for g in gates) and not red_lines
    first_blocker = next((g for g in gates if g["verdict"] != "pass"), None)
    if ready:
        next_action = "gates 1-7 pass, no red line: START IMPLEMENTATION"
        gate8 = _gate(8, "implementation-ready", "implementation ready?", "pass")
    else:
        b = first_blocker
        next_action = f"gate {b['id']} ({b['name']}) [{b['verdict']}]: {b.get('remediation')}"
        gate8 = _gate(
            8,
            "implementation-ready",
            "implementation ready?",
            "blocked",
            remediation="smallest blocker first; resolve, re-run the probe",
        )
    gates.append(gate8)
    ident = _kv(sections.get("REPOSITORY IDENTITY", []))
    tstamp = _kv(sections.get("TIMESTAMP", []))
    return {
        "generated_from": tstamp.get("UTC", "Unknown"),
        "repo": {
            "root": ident.get("ROOT", "Unknown"),
            "branch": ident.get("BRANCH", "Unknown"),
            "head": ident.get("HEAD", "Unknown")[:12],
        },
        "strict": strict,
        "gates": gates,
        "red_lines": red_lines,
        "ready": ready,
        "next_action": next_action,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the eight preflight gates.")
    parser.add_argument("log", help="Path to a probe log")
    parser.add_argument("--expected", default=None, help="Optional expected-contract (json/yaml)")
    parser.add_argument("--baseline", default=None, help="Optional baseline results record")
    parser.add_argument("--prior", default=None, help="Optional prior baseline to diff against")
    parser.add_argument("--strict", action="store_true", help="Disable autofix; every NO is a stop")
    parser.add_argument("--json", action="store_true", help="Emit the full report as JSON")
    args = parser.parse_args()

    log_path = Path(args.log)
    if not log_path.is_file():
        print(f"FAIL: not a file: {log_path}", file=sys.stderr)
        return 2
    try:
        sections = parse_sections(log_path.read_text(encoding="utf-8"))
        expected = _load(Path(args.expected)) if args.expected else {}
        baseline = _load(Path(args.baseline)) if args.baseline else None
        prior = _load(Path(args.prior)) if args.prior else None
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    if not isinstance(expected, dict):
        expected = {}

    report = evaluate(sections, expected, baseline, prior, args.strict)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(
            f"repo: {report['repo']['root']} @ {report['repo']['branch']} ({report['repo']['head']})"
        )
        for g in report["gates"]:
            tax = f" — {g['taxonomy']}" if g.get("taxonomy") else ""
            print(f"  gate {g['id']} {g['name']:22} [{g['verdict']}]{tax}")
        for rl in report["red_lines"]:
            print(f"  RED LINE (gate {rl['gate']}): {rl['rule']}")
        print(f"ready: {report['ready']}")
        print(f"next: {report['next_action']}")
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
