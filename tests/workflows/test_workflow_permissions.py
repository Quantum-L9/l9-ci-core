from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


class WorkflowPermissionTests(unittest.TestCase):
    def test_every_workflow_declares_read_only_contents(self) -> None:
        for workflow in WORKFLOWS.glob("*.yml"):
            with self.subTest(workflow=workflow.name):
                text = workflow.read_text(encoding="utf-8")
                self.assertRegex(text, re.compile(r"(?m)^\s*contents:\s+read\s*$"))

    # Write permissions are read-only everywhere except these audited
    # exceptions. Any change to this table is a trust-boundary change.
    #
    #   publish-analysis.yml    checks:write — publishes the GitHub check run;
    #                           security-events:write — uploads the SDK-projected
    #                           SARIF to code scanning. workflow_call-only, gated
    #                           by the caller.
    #   analyze-semgrep.yml     checks:write + security-events:write — its
    #                           `publish` job grants the nested
    #                           publish-analysis.yml call check-run and SARIF
    #                           upload scope. workflow_call-only; the caller
    #                           gates the trigger surface.
    #   self-analysis.yml       checks:write + security-events:write — audited
    #                           self-only dogfood caller of analyze-semgrep on
    #                           pull_request/push; grants the reusable the
    #                           same publish scopes.
    #   regenerate-identity-maps.yml
    #                           contents/pull-requests:write — opens a PR when
    #                           the semgrep registry drifts. Confined to its
    #                           `regenerate` job and unreachable from untrusted
    #                           events (enforced by the trigger test below).
    WRITE_EXCEPTIONS = {
        "publish-analysis.yml": ["checks", "security-events"],
        "analyze-semgrep.yml": ["checks", "security-events"],
        # Organization-facing entrypoint: the nested publish job needs the
        # same checked scopes analyze-semgrep.yml's publish job uses.
        "org-ci.yml": ["checks", "security-events"],
        "self-analysis.yml": ["checks", "security-events"],
        "regenerate-identity-maps.yml": ["contents", "pull-requests"],
    }

    def test_only_authorized_workflows_request_write(self) -> None:
        write_pattern = re.compile(
            r"(?m)^\s+(actions|checks|contents|deployments|discussions|"
            r"id-token|issues|packages|pages|pull-requests|"
            r"repository-projects|security-events|statuses):\s+write"
        )
        for workflow in WORKFLOWS.glob("*.yml"):
            with self.subTest(workflow=workflow.name):
                text = workflow.read_text(encoding="utf-8")
                matches = write_pattern.findall(text)
                expected = self.WRITE_EXCEPTIONS.get(workflow.name, [])
                self.assertEqual(expected, matches)

    # Preset/starter workflows that invoke the SDK-owned reusable Biome
    # workflow. They live outside .github/workflows (so the globs above skip
    # them) but must hold the same least-privilege line: contents: read only,
    # no write scopes, and the reusable call pinned to a full SHA.
    BIOME_LINT_TEST_WORKFLOWS = (
        "presets/typescript/.github/workflows/l9-lint-test.yml",
        "starter-workflows/typescript/l9-lint-test.yml",
    )

    def test_biome_lint_test_presets_are_read_only(self) -> None:
        write_pattern = re.compile(
            r"(?m)^\s+(actions|checks|contents|deployments|discussions|"
            r"id-token|issues|packages|pages|pull-requests|"
            r"repository-projects|security-events|statuses):\s+write"
        )
        for relative in self.BIOME_LINT_TEST_WORKFLOWS:
            workflow = ROOT / relative
            with self.subTest(workflow=relative):
                text = workflow.read_text(encoding="utf-8")
                self.assertRegex(text, re.compile(r"(?m)^\s*contents:\s+read\s*$"))
                self.assertEqual(
                    [],
                    write_pattern.findall(text),
                    f"{relative} invokes a reusable workflow and must stay "
                    "least-privilege (contents: read only)",
                )
                self.assertRegex(
                    text,
                    re.compile(
                        r"uses:\s*Quantum-L9/l9-ci-sdk/\.github/workflows/"
                        r"l9-biome-scan\.yml@[0-9a-f]{40}"
                    ),
                    f"{relative} must pin the SDK Biome reusable workflow to a "
                    "full 40-char SHA",
                )

    def test_write_scoped_workflows_are_not_pull_request_triggered(self) -> None:
        # A workflow that can obtain write permissions must never run on
        # untrusted `pull_request` events, or a fork PR could reach that scope.
        # publish-analysis.yml and analyze-semgrep.yml are reusable
        # (workflow_call) and gated by the caller. self-analysis.yml is an
        # audited self-only dogfood caller that deliberately triggers on
        # pull_request to exercise the kernel in this repo. The maintenance
        # workflow must be schedule/dispatch only.
        trigger_pattern = re.compile(r"(?m)^\s*(pull_request|pull_request_target):")
        reusable = {"publish-analysis.yml", "analyze-semgrep.yml"}
        audited_pr_callers = {"self-analysis.yml"}
        for name in self.WRITE_EXCEPTIONS:
            workflow = WORKFLOWS / name
            if name in reusable or name in audited_pr_callers:
                continue
            with self.subTest(workflow=name):
                text = workflow.read_text(encoding="utf-8")
                self.assertIsNone(
                    trigger_pattern.search(text),
                    f"{name} requests write permissions and must not be "
                    "triggered by pull_request events",
                )


if __name__ == "__main__":
    unittest.main()
