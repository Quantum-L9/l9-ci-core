"""Reusable-workflow permission chains must be callable end to end.

GitHub validates the reusable-workflow graph before it schedules a job. The
``GITHUB_TOKEN`` permissions a calling job grants can only be maintained or
reduced down the chain, never elevated, so a called workflow whose
``permissions`` block requests a scope the caller did not grant is rejected at
startup: the run ends ``startup_failure`` with zero jobs and nothing in the
tree ever executes.

That is exactly how a copied ``L9 Analysis`` caller failed three consecutive
runs (workflow audit packet, 2026-09-13, F-001): its ``publish`` job granted
``actions``, ``checks`` and ``contents`` but not the ``security-events: write``
that ``publish-analysis.yml`` declares for the SARIF upload. Core's own
``nightly.yml`` carried the same shape against ``analyze-semgrep.yml``.

This test walks every call edge Core ships — its workflows calling each other,
the kernels nesting ``publish-analysis.yml``, and the frozen starter/template
callers — and asserts the calling job grants a superset of everything the
callee requires: its workflow-level block plus every job-level block, including
the job that nests the next workflow. A pinned Core reference is checked
against the pinned revision as well when that object is available locally.
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
LEGACY_CALLERS = (
    "starter-workflows/python/l9-analysis.yml",
    "starter-workflows/typescript/l9-analysis.yml",
    "docs/templates/l9-analysis.yml",
)
CORE_REF = re.compile(
    r"^Quantum-L9/l9-ci-core/(\.github/workflows/[A-Za-z0-9._-]+\.yml)@([0-9a-f]{40})$"
)
LEVELS = {"none": 0, "read": 1, "write": 2}


def load(text: str) -> dict:
    document = yaml.safe_load(text)
    if not isinstance(document, dict):
        raise AssertionError("workflow is not a mapping")
    return document


def permission_block(value: object, where: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise AssertionError(
            f"{where}: shorthand permissions ({value!r}) are not auditable; "
            "declare each scope explicitly"
        )
    block: dict[str, str] = {}
    for scope, level in value.items():
        if str(level) not in LEVELS:
            raise AssertionError(f"{where}: unknown permission level {level!r}")
        block[str(scope)] = str(level)
    return block


def required_by(callee: dict, where: str) -> dict[str, str]:
    """Every scope the called workflow can request from the caller's token."""
    required = permission_block(callee.get("permissions"), f"{where} permissions")
    for job_id, job in (callee.get("jobs") or {}).items():
        for scope, level in permission_block(
            (job or {}).get("permissions"), f"{where} jobs.{job_id}.permissions"
        ).items():
            if LEVELS[level] > LEVELS.get(required.get(scope, "none"), 0):
                required[scope] = level
    return required


def granted_by(caller: dict, job_id: str, where: str) -> dict[str, str]:
    job = caller["jobs"][job_id] or {}
    if "permissions" in job:
        return permission_block(
            job["permissions"], f"{where} jobs.{job_id}.permissions"
        )
    return permission_block(caller.get("permissions"), f"{where} permissions")


def pinned_text(path: str, revision: str) -> str | None:
    proc = subprocess.run(
        ["git", "-C", str(ROOT), "cat-file", "-p", f"{revision}:{path}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout if proc.returncode == 0 else None


def call_edges() -> list[tuple[str, str, str, str, str]]:
    """``(caller, job, callee path, revision label, callee text)`` per edge."""
    edges: list[tuple[str, str, str, str, str]] = []
    callers = sorted(WORKFLOWS.glob("*.yml")) + [ROOT / rel for rel in LEGACY_CALLERS]
    for caller in callers:
        text = caller.read_text(encoding="utf-8")
        document = load(text)
        for job_id, job in (document.get("jobs") or {}).items():
            reference = (job or {}).get("uses")
            if not isinstance(reference, str):
                continue
            label = str(caller.relative_to(ROOT))
            if reference.startswith("./"):
                callee = reference[2:]
                edges.append(
                    (
                        label,
                        job_id,
                        callee,
                        "worktree",
                        (ROOT / callee).read_text(encoding="utf-8"),
                    )
                )
                continue
            match = CORE_REF.match(reference)
            if match is None:
                continue  # another repository's workflow is outside this audit
            callee, revision = match.group(1), match.group(2)
            edges.append(
                (
                    label,
                    job_id,
                    callee,
                    "worktree",
                    (ROOT / callee).read_text(encoding="utf-8"),
                )
            )
            pinned = pinned_text(callee, revision)
            if pinned is not None:
                edges.append((label, job_id, callee, revision[:12], pinned))
    return edges


class PermissionChainTests(unittest.TestCase):
    def test_every_caller_grants_what_its_callee_requires(self) -> None:
        edges = call_edges()
        self.assertGreaterEqual(
            len(edges), 6, "call-edge discovery found too few reusable-workflow edges"
        )
        callers: dict[str, dict] = {}
        for caller, job_id, callee, revision, callee_text in edges:
            with self.subTest(caller=caller, job=job_id, callee=callee, at=revision):
                document = callers.setdefault(
                    caller, load((ROOT / caller).read_text(encoding="utf-8"))
                )
                granted = granted_by(document, job_id, caller)
                self.assertTrue(
                    granted,
                    f"{caller} jobs.{job_id} declares no permissions; a reusable "
                    "call must state the ceiling it hands down",
                )
                required = required_by(load(callee_text), f"{callee}@{revision}")
                missing = {
                    scope: level
                    for scope, level in required.items()
                    if LEVELS[level] > LEVELS[granted.get(scope, "none")]
                }
                self.assertEqual(
                    {},
                    missing,
                    f"{caller} jobs.{job_id} grants {granted} but {callee}@{revision} "
                    f"requires {required}; GitHub rejects this call at startup "
                    "(zero jobs) because a called workflow cannot elevate permissions",
                )

    def test_core_kernel_chain_is_internally_consistent(self) -> None:
        """nightly.yml → analyze-semgrep.yml → publish-analysis.yml stays callable."""
        publish = required_by(
            load((WORKFLOWS / "publish-analysis.yml").read_text(encoding="utf-8")),
            "publish-analysis.yml",
        )
        self.assertEqual("write", publish.get("security-events"))
        analyze = load((WORKFLOWS / "analyze-semgrep.yml").read_text(encoding="utf-8"))
        nightly = load((WORKFLOWS / "nightly.yml").read_text(encoding="utf-8"))
        for scope, level in publish.items():
            self.assertEqual(
                level, granted_by(analyze, "publish", "analyze-semgrep.yml").get(scope)
            )
            self.assertEqual(
                level, granted_by(nightly, "analyze", "nightly.yml").get(scope)
            )


class LegacyCallerTests(unittest.TestCase):
    """Frozen copy-first callers say so, and say what a live copy must grant."""

    def test_legacy_callers_are_marked_deprecated_and_point_at_central_ci(self) -> None:
        for relative in LEGACY_CALLERS:
            with self.subTest(caller=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                header = text.split("\nname:", 1)[0]
                self.assertIn("DEPRECATED", header)
                self.assertIn(".github/workflows/org-ci.yml", header)
                self.assertIn("security-events", header)
                self.assertIn("presets/LEGACY.md", header)


if __name__ == "__main__":
    unittest.main()
