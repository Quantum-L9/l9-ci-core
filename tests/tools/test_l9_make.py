from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from l9_make.__main__ import (  # noqa: E402
    CompilerError,
    STANDARD_BINDINGS,
    check,
    load_plan,
    main,
    plan_digest,
    render,
    render_repo_mk,
    validate_local_extensions,
)


class MakeCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.plan_path = self.root / "plan.json"
        self.output_path = self.root / "Repo.mk"
        self.local_path = self.root / "Repo.local.mk"
        self.plan_path.write_text(
            json.dumps(
                {
                    "schema": "l9.make-capability-plan/v1",
                    "bindings": [
                        {"target": target, "command": command}
                        for target, command in reversed(tuple(STANDARD_BINDINGS.items()))
                    ],
                }
            ),
            encoding="utf-8",
        )

    def test_load_plan_normalizes_binding_order(self) -> None:
        plan = load_plan(self.plan_path)
        self.assertEqual(list(STANDARD_BINDINGS), [entry["target"] for entry in plan["bindings"]])
        self.assertEqual(
            "3e347d477591620d6fb803acca60af491f959099bcd75330ba4a52aaf7e3f6cc",
            plan_digest(plan),
        )

    def test_render_and_check_are_deterministic(self) -> None:
        render(self.plan_path, self.output_path, self.local_path)
        first = self.output_path.read_text(encoding="utf-8")
        render(self.plan_path, self.output_path, self.local_path)
        self.assertEqual(first, self.output_path.read_text(encoding="utf-8"))
        check(self.plan_path, self.output_path, self.local_path)
        self.assertIn("repo-validate:", first)
        self.assertIn("$(L9_REPO) validate", first)
        self.assertNotIn("git push", first)
        self.assertNotIn("gh pr", first)

    def test_check_rejects_generated_output_drift(self) -> None:
        render(self.plan_path, self.output_path, self.local_path)
        self.output_path.write_text("drift\n", encoding="utf-8")
        with self.assertRaisesRegex(CompilerError, "drifted"):
            check(self.plan_path, self.output_path, self.local_path)

    def test_plan_rejects_unapproved_or_duplicate_target(self) -> None:
        data = json.loads(self.plan_path.read_text(encoding="utf-8"))
        data["bindings"][0]["target"] = "pr"
        self.plan_path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(CompilerError, "reserved"):
            load_plan(self.plan_path)

        data["bindings"][0] = {"target": "repo-setup", "command": "setup"}
        self.plan_path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(CompilerError, "duplicate"):
            load_plan(self.plan_path)

    def test_local_extensions_cannot_override_generated_or_governance_targets(self) -> None:
        self.local_path.write_text("repo-check:\n\t@true\n", encoding="utf-8")
        with self.assertRaisesRegex(CompilerError, "overrides protected target 'repo-check'"):
            validate_local_extensions(self.local_path, STANDARD_BINDINGS)

        self.local_path.write_text("pr:\n\t@true\n", encoding="utf-8")
        with self.assertRaisesRegex(CompilerError, "overrides protected target 'pr'"):
            validate_local_extensions(self.local_path, STANDARD_BINDINGS)

    def test_renderer_is_closed_and_shell_free(self) -> None:
        source = (ROOT / "tools" / "l9_make" / "__main__.py").read_text(encoding="utf-8")
        for forbidden in ("subprocess", "os.system", "shell=True", "eval(", "git push", "gh pr"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        plan = load_plan(self.plan_path)
        self.assertEqual(render_repo_mk(plan), render_repo_mk(plan))

    def test_cli_returns_nonzero_for_drift(self) -> None:
        self.assertEqual(
            0,
            main(["render", "--plan", str(self.plan_path), "--output", str(self.output_path)]),
        )
        self.output_path.write_text("drift\n", encoding="utf-8")
        self.assertEqual(
            2,
            main(["check", "--plan", str(self.plan_path), "--output", str(self.output_path)]),
        )


if __name__ == "__main__":
    unittest.main()
