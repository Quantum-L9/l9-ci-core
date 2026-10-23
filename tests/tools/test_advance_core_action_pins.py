"""``tools/advance_core_action_pins.py``: the mechanical second step of a Core
action change (AGENTS.md §16)."""

from __future__ import annotations

import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "advance_core_action_pins.py"
ORG_CI = ROOT / ".github" / "workflows" / "org-ci.yml"

spec = importlib.util.spec_from_file_location("advance_core_action_pins", TOOL)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

OLD = "a" * 40
OTHER = "b" * 40
NEW = "c" * 40
SAMPLE = f"""name: sample
jobs:
  analyze:
    steps:
      - id: consumer
        # CORE_ACTIONS_PIN
        uses: Quantum-L9/l9-ci-core/.github/actions/resolve-consumer-metadata@{OLD}
      - uses: Quantum-L9/l9-ci-core/.github/actions/install-biome@{OTHER}  # toolchain
      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
      - uses: Quantum-L9/l9-ci-sdk/.github/workflows/l9-biome-scan.yml@{OLD}
"""


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = module.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class RewriteTests(unittest.TestCase):
    def test_rewrites_only_core_action_pins(self) -> None:
        text, changed = module.rewrite(SAMPLE, NEW)
        self.assertEqual([(7, OLD), (8, OTHER)], [(n, o) for n, o, _ in changed])
        self.assertIn(f"resolve-consumer-metadata@{NEW}\n", text)
        self.assertIn(f"install-biome@{NEW}  # toolchain\n", text)
        # Comments, external actions and SDK workflow references are untouched.
        self.assertIn("# CORE_ACTIONS_PIN", text)
        self.assertIn(
            "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97", text
        )
        self.assertIn(f"l9-biome-scan.yml@{OLD}", text)

    def test_rewrite_is_idempotent(self) -> None:
        once, _ = module.rewrite(SAMPLE, NEW)
        twice, changed = module.rewrite(once, NEW)
        self.assertEqual(once, twice)
        self.assertEqual([], changed)


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.workflow = self.tmp / "org-ci.yml"
        self.workflow.write_text(SAMPLE, encoding="utf-8")

    def test_check_reports_drift_and_passes_once_uniform(self) -> None:
        code, out, _ = run(NEW, "--workflow", str(self.workflow), "--check")
        self.assertEqual(1, code)
        self.assertIn(f"pinned {OLD}, expected {NEW}", out)
        self.assertEqual(0, run(NEW, "--workflow", str(self.workflow))[0])
        code, out, _ = run(NEW, "--workflow", str(self.workflow), "--check")
        self.assertEqual(0, code, out)
        self.assertIn("all 2 Core action pin(s)", out)

    def test_dry_run_writes_nothing(self) -> None:
        code, out, _ = run(NEW, "--workflow", str(self.workflow), "--dry-run")
        self.assertEqual(0, code)
        self.assertIn("2 pin(s) would change", out)
        self.assertEqual(SAMPLE, self.workflow.read_text(encoding="utf-8"))

    def test_floating_or_short_references_are_refused(self) -> None:
        for bad in ("main", "v2", OLD[:12], OLD.upper()):
            with self.subTest(sha=bad):
                code, _, err = run(bad, "--workflow", str(self.workflow))
                self.assertEqual(2, code)
                self.assertIn("40 lowercase hex", err)
        self.assertEqual(SAMPLE, self.workflow.read_text(encoding="utf-8"))

    def test_floating_core_reference_is_reported_and_fails_every_mode(self) -> None:
        """A `@main` edge must be seen and rejected, never skipped (Codex P2)."""
        text = SAMPLE.replace(f"install-biome@{OTHER}", "install-biome@main")
        self.workflow.write_text(text, encoding="utf-8")
        self.assertEqual([(8, "main")], module.floating(text))
        for extra in ((), ("--check",), ("--dry-run",)):
            with self.subTest(mode=extra or ("rewrite",)):
                code, out, _ = run(NEW, "--workflow", str(self.workflow), *extra)
                self.assertEqual(1, code)
                self.assertIn("'main' is not a full lowercase SHA", out)
        # Nothing was rewritten around the offending edge.
        self.assertEqual(text, self.workflow.read_text(encoding="utf-8"))

    def test_workflow_without_core_pins_is_an_error(self) -> None:
        empty = self.tmp / "none.yml"
        empty.write_text("name: none\n", encoding="utf-8")
        code, _, err = run(NEW, "--workflow", str(empty))
        self.assertEqual(2, code)
        self.assertIn("carries no Core action pin", err)


class LiveWorkflowTests(unittest.TestCase):
    def test_org_ci_pins_are_discoverable(self) -> None:
        """Every ``uses:`` of a Core action in org-ci.yml matches the tool's
        pattern, so an advance never silently skips one."""
        text = ORG_CI.read_text(encoding="utf-8")
        expected = sum(
            1
            for line in text.splitlines()
            if "uses:" in line and "Quantum-L9/l9-ci-core/.github/actions/" in line
        )
        self.assertGreater(expected, 0)
        self.assertEqual(expected, len(module.pins(text)))
        self.assertEqual([], module.floating(text))


if __name__ == "__main__":
    unittest.main()
