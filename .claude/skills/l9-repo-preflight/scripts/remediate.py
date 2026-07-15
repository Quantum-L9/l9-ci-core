#!/usr/bin/env python3
"""Fail-OPEN preflight remediation loop: probe -> evaluate -> autofix -> re-probe.

The primary entrypoint. Runs the read-only probe, classifies the eight gates
(evaluate_preflight), APPLIES every safe, reversible autofix to the live repo,
re-probes, and repeats to a fixpoint. The run always completes; the output is a
machine-readable genuine-blocker report (only what could not be safely
auto-resolved) plus an autofix-log audit trail.

Safe autofix allow-list (nothing else is ever auto-applied):
  clean_generated    remove untracked known-generated artifacts + gitignore them
  git_switch_branch  switch to the expected branch (clean tree only)
  adapt_blueprint    write an evidence-adapted expected-contract (new file)
  pip_install        install the repo's declared/pinned tools
  editable_install   run the declared editable install so packages import
  ruff_fix           ruff check --fix + ruff format (clean tree only; tool-owned)

Unknown-provenance files, user tracked/staged edits, wrong repo/commit, missing
core foundations, and NEW type/test failures are NEVER auto-applied — they are
reported as genuine blockers for downstream remediation.

    python scripts/remediate.py [REPO] [--expected C] [--work-dir .preflight]
        [--max-iters 5] [--dry-run] [--no-fix-code] [--json]

Exit codes (informational, never a halt): 0 no blockers, 1 blockers remain,
2 could not run at all.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import evaluate_preflight as ev  # noqa: E402

PROBE = HERE / "preflight_probe.sh"


def _run(cmd: list[str], cwd: Path, timeout: int = 300) -> tuple[int, str]:
    try:
        p = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, check=False
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as exc:  # noqa: BLE001
        return 127, f"[remediate: could not run {' '.join(cmd)}: {exc}]"


def _git(args: list[str], repo: Path) -> tuple[int, str]:
    return _run(["git", *args], repo)


def run_probe(repo: Path, work_dir: Path) -> dict[str, list[str]] | None:
    """Run the read-only probe and return its parsed sections (log kept in work_dir)."""
    rc, _ = _run(["bash", str(PROBE)], repo)
    logs = sorted(repo.glob("repo-preflight-*.log"))
    if not logs:
        return None
    log = logs[-1]
    text = log.read_text(encoding="utf-8", errors="ignore")
    (work_dir / log.name).write_text(text, encoding="utf-8")
    log.unlink()  # keep the worktree clean; the copy lives in work_dir
    return ev.parse_sections(text)


def _has_eslint(repo: Path) -> bool:
    return any(
        (repo / n).exists()
        for n in (
            ".eslintrc",
            ".eslintrc.json",
            ".eslintrc.js",
            ".eslintrc.cjs",
            "eslint.config.js",
        )
    )


def _has_prettier(repo: Path) -> bool:
    return any(
        (repo / n).exists() for n in (".prettierrc", ".prettierrc.json", "prettier.config.js")
    )


def measure_baseline(repo: Path) -> dict[str, Any]:
    """Run whatever validators exist; failures feed Gate 7. Ecosystem-neutral: a tool
    that is not installed/configured is simply skipped (Python AND Node supported)."""
    results: dict[str, dict[str, int]] = {}
    # --- Python ---
    if shutil.which("ruff"):
        rc, out = _run(["ruff", "check", "--output-format=concise", "."], repo)
        results["ruff"] = {"failed": 0 if rc == 0 else max(1, out.count(":"))}
        rc, out = _run(["ruff", "format", "--check", "."], repo)
        results["ruff_format"] = {"failed": 0 if rc == 0 else max(1, out.lower().count("reformat"))}
    if shutil.which("mypy"):
        rc, out = _run(["mypy", "."], repo)
        results["mypy"] = {"failed": 0 if rc == 0 else max(1, out.count(" error:"))}
    if shutil.which("pytest"):
        rc, out = _run(["pytest", "-q"], repo)
        results["pytest"] = {"failed": 0 if rc == 0 else _count(out, "failed") or 1}
    # --- Node (only once dependencies are installed) ---
    if (
        (repo / "package.json").exists()
        and (repo / "node_modules").exists()
        and shutil.which("npx")
    ):
        if _has_eslint(repo):
            rc, out = _run(["npx", "--no-install", "eslint", "."], repo)
            results["eslint"] = {"failed": 0 if rc == 0 else max(1, out.lower().count("error"))}
        if _has_prettier(repo):
            rc, out = _run(["npx", "--no-install", "prettier", "--check", "."], repo)
            results["prettier"] = {"failed": 0 if rc == 0 else max(1, out.count("\n"))}
        if (repo / "tsconfig.json").exists():
            rc, out = _run(["npx", "--no-install", "tsc", "--noEmit"], repo)
            results["tsc"] = {"failed": 0 if rc == 0 else max(1, out.count(" error"))}
    return {"results": results}


def _count(out: str, word: str) -> int:
    toks = out.split()
    for i, tok in enumerate(toks):
        if word in tok and i > 0 and toks[i - 1].isdigit():
            return int(toks[i - 1])
    return 0


# --------------------------------------------------------------------------- #
# Safe autofix actions — each returns an action record; none touches user work
# --------------------------------------------------------------------------- #
def _record(
    gate: int, action: str, target: Any, command: str, rc: int, note: str = ""
) -> dict[str, Any]:
    return {
        "gate": gate,
        "action": action,
        "target": target,
        "command": command,
        "result": "ok" if rc == 0 else f"rc={rc}",
        "reversible": True,
        "note": note,
    }


def _is_tracked(repo: Path, path: str) -> bool:
    rc, _ = _git(["ls-files", "--error-unmatch", path], repo)
    return rc == 0


def _gitignore_add(repo: Path, patterns: list[str], self_modified: set[str]) -> None:
    gi = repo / ".gitignore"
    existing = gi.read_text(encoding="utf-8").splitlines() if gi.exists() else []
    add = [p for p in patterns if p not in existing]
    if not add:
        return
    with gi.open("a", encoding="utf-8") as fh:
        if existing and existing[-1].strip():
            fh.write("\n")
        fh.write("# added by l9-repo-preflight remediate\n" + "\n".join(add) + "\n")
    self_modified.add(".gitignore")


_IGNORE_DIRS = {
    ".venv",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "htmlcov",
    "build",
    "dist",
    "node_modules",
    ".next",
    ".turbo",
    "coverage",
}


def _ignore_pattern(target: str) -> str:
    """Canonical .gitignore pattern for a generated path — the generated SEGMENT,
    never the enclosing package dir (e.g. `__pycache__/`, not `pkg/`)."""
    segs = target.strip("/").split("/")
    for s in segs:
        if s == "__pycache__":
            return "__pycache__/"
        if s.endswith(".egg-info"):
            return "*.egg-info/"
        if s.endswith(".dist-info"):
            return "*.dist-info/"
        if s in _IGNORE_DIRS:
            return s + "/"
        if s == ".coverage":
            return ".coverage"
    base = segs[-1]
    if base.endswith(".pyc"):
        return "*.pyc"
    if base.startswith("repo-preflight-") and base.endswith(".log"):
        return "repo-preflight-*.log"
    return base


# Untracked dependency dirs: gitignore them, but never delete (they are needed,
# just not committed) — unlike throwaway caches which are removed.
_KEEP_IGNORE = {"node_modules"}


def apply_clean_generated(
    repo: Path, plan: dict[str, Any], self_modified: set[str]
) -> list[dict[str, Any]]:
    recs, globs = [], set()
    for target in plan.get("targets", []):
        if _is_tracked(repo, target):  # safety: never remove a tracked file
            recs.append(
                _record(3, "clean_generated", target, "skip (tracked)", 1, "tracked; skipped")
            )
            continue
        top = target.strip("/").split("/")[0]
        if top in _KEEP_IGNORE:  # dependency dir: ignore, do not remove
            globs.add(top + "/")
            recs.append(
                _record(
                    3, "clean_generated", target, f"gitignore {top}/ (kept)", 0, "dependency dir"
                )
            )
            continue
        tp = repo / target
        if tp.exists():
            if tp.is_dir():
                shutil.rmtree(tp, ignore_errors=True)
            else:
                tp.unlink(missing_ok=True)
        globs.add(_ignore_pattern(target))
        recs.append(_record(3, "clean_generated", target, f"rm -rf {target}", 0, "regenerable"))
    if globs:
        _gitignore_add(repo, sorted(globs), self_modified)
        recs.append(_record(3, "clean_generated", sorted(globs), "gitignore += generated globs", 0))
    return recs


def apply_git_switch(repo: Path, plan: dict[str, Any]) -> list[dict[str, Any]]:
    branch = (plan.get("targets") or [""])[0]
    rc, _ = _git(["switch", branch], repo)
    return [_record(2, "git_switch_branch", branch, f"git switch {branch}", rc)]


def apply_pip_install(repo: Path, plan: dict[str, Any]) -> list[dict[str, Any]]:
    reqs = repo / "requirements-ci.txt"
    if reqs.exists():  # honour the repo's pins
        rc, _ = _run([sys.executable, "-m", "pip", "install", "-r", str(reqs)], repo)
        return [
            _record(
                5, "pip_install", "requirements-ci.txt", "pip install -r requirements-ci.txt", rc
            )
        ]
    tools = plan.get("targets", [])
    rc, _ = _run([sys.executable, "-m", "pip", "install", *tools], repo) if tools else (0, "")
    return [_record(5, "pip_install", tools, f"pip install {' '.join(tools)}", rc)]


def apply_editable_install(repo: Path, plan: dict[str, Any]) -> list[dict[str, Any]]:
    if not (repo / "pyproject.toml").exists() and not (repo / "setup.py").exists():
        return [_record(6, "editable_install", plan.get("targets"), "skip (no build config)", 1)]
    rc, _ = _run([sys.executable, "-m", "pip", "install", "-e", "."], repo)
    return [_record(6, "editable_install", plan.get("targets"), "pip install -e .", rc)]


def apply_npm_install(repo: Path, plan: dict[str, Any]) -> list[dict[str, Any]]:
    if not shutil.which("npm"):
        return [_record(plan.get("gate", 6), "npm_install", "npm", "skip (npm not installed)", 1)]
    # npm ci needs a lockfile; fall back to npm install otherwise.
    lock = any((repo / n).exists() for n in ("package-lock.json", "npm-shrinkwrap.json"))
    cmd = ["npm", "ci"] if lock else ["npm", "install"]
    rc, _ = _run(cmd, repo, timeout=600)
    return [_record(plan.get("gate", 6), "npm_install", plan.get("targets"), " ".join(cmd), rc)]


def apply_eslint_fix(repo: Path, self_modified: set[str]) -> list[dict[str, Any]]:
    if not shutil.which("npx") or not (repo / "node_modules").exists():
        return [_record(7, "eslint_fix", "eslint", "skip (node_modules/npx missing)", 1)]
    files = ev._status_files(_git(["status", "--short"], repo)[1].splitlines())
    if set(files["tracked"]) - self_modified or files["staged"]:
        return [
            _record(
                7,
                "eslint_fix",
                "eslint",
                "skip (user tracked edits present)",
                1,
                "deferred: offered as a blocker remediation",
            )
        ]
    rc1, _ = _run(["npx", "--no-install", "eslint", "--fix", "."], repo)
    rc2 = 0
    if _has_prettier(repo):
        rc2, _ = _run(["npx", "--no-install", "prettier", "--write", "."], repo)
    changed = [
        ln.strip() for ln in _git(["diff", "--name-only"], repo)[1].splitlines() if ln.strip()
    ]
    self_modified.update(changed)
    return [
        _record(
            7,
            "eslint_fix",
            changed,
            "eslint --fix . && prettier --write .",
            0 if rc1 == 0 and rc2 == 0 else 1,
            f"{len(changed)} file(s) fixed",
        )
    ]


def apply_ruff_fix(repo: Path, self_modified: set[str]) -> list[dict[str, Any]]:
    if not shutil.which("ruff"):
        return [_record(7, "ruff_fix", "ruff", "skip (ruff not installed)", 1)]
    # Clean-tree guard: only run when there are no pre-existing user tracked edits,
    # so every change is tool-owned and git-reversible (never entangles user work).
    files = ev._status_files(_git(["status", "--short"], repo)[1].splitlines())
    if set(files["tracked"]) - self_modified or files["staged"]:
        return [
            _record(
                7,
                "ruff_fix",
                "ruff",
                "skip (user tracked edits present)",
                1,
                "deferred: offered as a blocker remediation",
            )
        ]
    rc1, _ = _run(["ruff", "check", "--fix", "."], repo)
    rc2, _ = _run(["ruff", "format", "."], repo)
    changed = [
        ln.strip() for ln in _git(["diff", "--name-only"], repo)[1].splitlines() if ln.strip()
    ]
    self_modified.update(changed)
    return [
        _record(
            7,
            "ruff_fix",
            changed,
            "ruff check --fix . && ruff format .",
            0 if rc1 == 0 and rc2 == 0 else 1,
            f"{len(changed)} file(s) formatted",
        )
    ]


def adapt_blueprint(
    repo: Path,
    plan: dict[str, Any],
    expected: dict[str, Any],
    sections: dict[str, list[str]],
    work_dir: Path,
    expected_src: Path | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Write an evidence-adapted expected-contract to a NEW file (non-destructive)."""
    adapted = json.loads(json.dumps(expected)) if expected else {}
    gate = plan.get("gate")
    targets = plan.get("targets", [])
    if gate == 4:  # drop missing foundations
        adapted["foundations"] = [f for f in adapted.get("foundations", []) if f not in targets]
    elif gate == 5:  # follow the repo's own tools
        adapted.setdefault("toolchain", {})["test_tools"] = sorted(ev._repo_tools(sections))
    elif gate == 6:  # drop foreign packages
        adapted["packages"] = [p for p in adapted.get("packages", []) if p not in targets]
    dest = work_dir / (
        (expected_src.stem + ".adapted.json") if expected_src else "expected-contract.adapted.json"
    )
    dest.write_text(json.dumps(adapted, indent=2) + "\n", encoding="utf-8")
    rec = _record(
        gate or 0,
        "adapt_blueprint",
        targets,
        f"write {dest.name}",
        0,
        "blueprint adapted to evidence (new file)",
    )
    return adapted, [rec]


def _local_exclude(repo: Path, pattern: str) -> None:
    """Ignore a path locally via .git/info/exclude — no tracked-file mutation."""
    exclude = repo / ".git" / "info" / "exclude"
    if not exclude.parent.exists():
        return
    lines = exclude.read_text(encoding="utf-8").splitlines() if exclude.exists() else []
    if pattern not in lines:
        with exclude.open("a", encoding="utf-8") as fh:
            fh.write(pattern + "\n")


def remediate(
    repo: Path,
    expected: dict[str, Any],
    expected_src: Path | None,
    work_dir: Path,
    max_iters: int,
    dry_run: bool,
    fix_code: bool,
) -> dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)
    _local_exclude(repo, ".preflight/")  # keep the tool's workdir out of the worktree
    self_modified: set[str] = set()
    actions: list[dict[str, Any]] = []
    prior_baseline: dict[str, Any] | None = None
    report: dict[str, Any] = {}
    iters = 0

    for i in range(max_iters):
        iters = i + 1
        sections = run_probe(repo, work_dir)
        if sections is None:
            return {
                "run_completed": True,
                "error": "probe produced no log",
                "genuine_blockers": [],
                "blocker_count": 0,
                "ready_after_remediation": False,
            }
        baseline = measure_baseline(repo)
        if prior_baseline is None:
            prior_baseline = baseline  # the initial state = the existing baseline
        report = ev.evaluate(
            sections, expected, baseline, prior_baseline, self_modified=frozenset(self_modified)
        )
        plans = report["autofix_plans"]
        if dry_run or not plans:
            break
        applied = 0
        for plan in plans:
            action = plan["action"]
            if action == ev.ACTION_CLEAN_GENERATED:
                recs = apply_clean_generated(repo, plan, self_modified)
            elif action == ev.ACTION_GIT_SWITCH:
                recs = apply_git_switch(repo, plan)
            elif action == ev.ACTION_PIP_INSTALL:
                recs = apply_pip_install(repo, plan)
            elif action == ev.ACTION_EDITABLE_INSTALL:
                recs = apply_editable_install(repo, plan)
            elif action == ev.ACTION_NPM_INSTALL:
                recs = apply_npm_install(repo, plan)
            elif action == ev.ACTION_ADAPT:
                expected, recs = adapt_blueprint(
                    repo, plan, expected, sections, work_dir, expected_src
                )
            elif action == ev.ACTION_RUFF_FIX:
                if not fix_code:
                    recs = [_record(7, "ruff_fix", plan.get("targets"), "skip (--no-fix-code)", 1)]
                else:
                    recs = apply_ruff_fix(repo, self_modified)
            elif action == ev.ACTION_ESLINT_FIX:
                if not fix_code:
                    recs = [
                        _record(7, "eslint_fix", plan.get("targets"), "skip (--no-fix-code)", 1)
                    ]
                else:
                    recs = apply_eslint_fix(repo, self_modified)
            else:
                recs = [
                    _record(plan.get("gate", 0), action, plan.get("targets"), "unknown action", 1)
                ]
            actions.extend(recs)
            applied += sum(1 for r in recs if r["result"] == "ok")
        if applied == 0:  # fixpoint: nothing more we can safely do
            break

    report["mode"] = "dry-run" if dry_run else "applied"
    report["iterations"] = iters
    report["autofixed"] = actions
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="Fail-open preflight remediation loop.")
    ap.add_argument("repo", nargs="?", default=".", help="Repo root (default: cwd)")
    ap.add_argument("--expected", default=None, help="Expected-contract (json/yaml)")
    ap.add_argument(
        "--work-dir", default=".preflight", help="Work/output dir (default: .preflight)"
    )
    ap.add_argument("--max-iters", type=int, default=5)
    ap.add_argument("--dry-run", action="store_true", help="Plan only; apply nothing")
    ap.add_argument(
        "--no-fix-code", action="store_true", help="Do not run ruff --fix on tracked source"
    )
    ap.add_argument("--json", action="store_true", help="Print the full report as JSON")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    if not (repo / ".git").exists() and not (repo / ".git").is_file():
        print(f"FAIL: not a git repo: {repo}", file=sys.stderr)
        return 2
    work_dir = (
        repo / args.work_dir if not Path(args.work_dir).is_absolute() else Path(args.work_dir)
    )
    expected_src = Path(args.expected) if args.expected else None
    try:
        expected = ev._load(expected_src) if expected_src else {}
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    if not isinstance(expected, dict):
        expected = {}

    report = remediate(
        repo,
        expected,
        expected_src,
        work_dir,
        args.max_iters,
        args.dry_run,
        fix_code=not args.no_fix_code,
    )

    (work_dir / "blocker-report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    (work_dir / "autofix-log.json").write_text(
        json.dumps(
            {"iterations": report.get("iterations", 0), "actions": report.get("autofixed", [])},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"mode: {report.get('mode')} | iterations: {report.get('iterations')}")
        for a in report.get("autofixed", []):
            print(f"  autofix (gate {a['gate']}): {a['action']} [{a['result']}] — {a['target']}")
        for b in report.get("genuine_blockers", []):
            print(
                f"  BLOCKER {b['id']} [{b['severity']}]: {b['class']} — {b['why_not_autofixable']}"
            )
        print(
            f"run_completed: {report.get('run_completed')} | "
            f"blockers: {report.get('blocker_count')} | "
            f"ready_after_remediation: {report.get('ready_after_remediation')}"
        )
        print(f"reports -> {work_dir}/blocker-report.json, {work_dir}/autofix-log.json")
    return 0 if report.get("blocker_count", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
