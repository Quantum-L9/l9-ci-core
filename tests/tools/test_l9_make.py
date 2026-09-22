from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from l9_make.__main__ import (  # noqa: E402
    CAPABILITY_STATES,
    CompilerError,
    STANDARD_CAPABILITIES,
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
        self.plan_path = self.root / "make-plan.json"
        self.output_path = self.root / "Repo.mk"
        self.makefile_path = self.root / "Makefile"
        self.legacy_template_path = (
            self.root / "tools" / "l9_repo" / "Makefile.template"
        )
        self.local_path = self.root / "Repo.local.mk"
        self.local_path.write_text("# repository extension layer\n", encoding="utf-8")
        self.plan_path.write_text(
            json.dumps(self.valid_plan(), indent=2), encoding="utf-8"
        )

    @staticmethod
    def digest(seed: str) -> str:
        return "sha256:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()

    @classmethod
    def valid_plan(cls) -> dict[str, object]:
        capabilities: list[dict[str, object]] = []
        for name in reversed(STANDARD_CAPABILITIES):
            capability: dict[str, object] = {
                "name": name,
                "state": "supported",
                "kind": "compatibility_alias" if name == "check" else "native_binding",
                "argv": ["@python", "-m", "tools.l9_repo", "validate"],
                "provenance": {
                    "source": f"fixture:{name}",
                    "digest": cls.digest(name[0]),
                },
            }
            if name == "build":
                capability.pop("argv")
                capability["state"] = "not_required"
                capability["diagnostic"] = "fixture has no build artifact"
            elif name == "benchmark":
                capability.pop("argv")
                capability["state"] = "unsupported"
                capability["diagnostic"] = "fixture has no benchmark capability"
            capabilities.append(capability)
        return {
            "schema": "l9.make-plan/v1",
            "repository_class": "fixture-repository",
            "provenance": {
                "producer": "fixture-producer",
                "source": "fixture-plan",
                "digest": cls.digest("f"),
            },
            "capabilities": capabilities,
        }

    def plan_data(self) -> dict[str, object]:
        return json.loads(self.plan_path.read_text(encoding="utf-8"))

    def write_plan(self, value: object) -> None:
        self.plan_path.write_text(json.dumps(value, indent=2), encoding="utf-8")

    def test_load_plan_normalizes_capability_order_and_schema(self) -> None:
        plan = load_plan(self.plan_path)
        self.assertEqual(
            list(STANDARD_CAPABILITIES),
            [entry["name"] for entry in plan["capabilities"]],
        )
        self.assertEqual(
            CAPABILITY_STATES,
            {entry["state"] for entry in plan["capabilities"]} | {"supported"},
        )
        self.assertEqual(plan_digest(plan), plan_digest(load_plan(self.plan_path)))

    def test_render_and_check_are_deterministic_and_stateful(self) -> None:
        render(
            self.plan_path,
            self.output_path,
            self.local_path,
            self.makefile_path,
            self.legacy_template_path,
        )
        first = self.output_path.read_text(encoding="utf-8")
        first_makefile = self.makefile_path.read_text(encoding="utf-8")
        first_legacy_template = self.legacy_template_path.read_text(encoding="utf-8")
        render(
            self.plan_path,
            self.output_path,
            self.local_path,
            self.makefile_path,
            self.legacy_template_path,
        )
        self.assertEqual(first, self.output_path.read_text(encoding="utf-8"))
        self.assertEqual(first_makefile, self.makefile_path.read_text(encoding="utf-8"))
        self.assertEqual(
            first_legacy_template,
            self.legacy_template_path.read_text(encoding="utf-8"),
        )
        self.assertEqual(first_makefile, first_legacy_template)
        check(
            self.plan_path,
            self.output_path,
            self.local_path,
            self.makefile_path,
            self.legacy_template_path,
        )
        self.assertIn("repo-capabilities:", first)
        self.assertIn("repo-build:", first)
        self.assertIn("NOT_REQUIRED: build - fixture has no build artifact", first)
        self.assertIn(
            "UNSUPPORTED: benchmark - fixture has no benchmark capability", first
        )
        self.assertIn("$(PYTHON) -m tools.l9_repo validate", first)
        self.assertNotIn("git push", first)
        self.assertNotIn("gh pr", first)
        self.assertNotIn("l9 pr", first)

    def test_check_rejects_generated_output_drift(self) -> None:
        render(self.plan_path, self.output_path, self.local_path)
        self.output_path.write_text("drift\n", encoding="utf-8")
        with self.assertRaisesRegex(CompilerError, "drifted"):
            check(self.plan_path, self.output_path, self.local_path)

    def test_check_rejects_generated_root_facade_drift(self) -> None:
        render(self.plan_path, self.output_path, self.local_path, self.makefile_path)
        self.makefile_path.write_text("drift\n", encoding="utf-8")
        with self.assertRaisesRegex(CompilerError, "Makefile drifted"):
            check(self.plan_path, self.output_path, self.local_path, self.makefile_path)

    def test_check_rejects_legacy_compatibility_projection_drift(self) -> None:
        render(
            self.plan_path,
            self.output_path,
            self.local_path,
            self.makefile_path,
            self.legacy_template_path,
        )
        self.legacy_template_path.write_text("drift\n", encoding="utf-8")
        with self.assertRaisesRegex(CompilerError, "Makefile drifted"):
            check(
                self.plan_path,
                self.output_path,
                self.local_path,
                self.makefile_path,
                self.legacy_template_path,
            )

    def test_schema_rejects_missing_or_unapproved_contract_fields(self) -> None:
        data = self.plan_data()
        assert isinstance(data, dict)
        data["unexpected"] = True
        self.write_plan(data)
        with self.assertRaisesRegex(CompilerError, "contract violation"):
            load_plan(self.plan_path)

        data = self.valid_plan()
        capabilities = data["capabilities"]
        assert isinstance(capabilities, list)
        capabilities.pop()
        self.write_plan(data)
        with self.assertRaisesRegex(
            CompilerError, "contract violation|standard vocabulary"
        ):
            load_plan(self.plan_path)

    def test_schema_rejects_invalid_state_transport_and_alias_kind(self) -> None:
        data = self.valid_plan()
        capabilities = data["capabilities"]
        assert isinstance(capabilities, list)
        target = next(item for item in capabilities if item["name"] == "setup")
        target["state"] = "invented"
        self.write_plan(data)
        with self.assertRaisesRegex(CompilerError, "contract violation|invalid"):
            load_plan(self.plan_path)

        data = self.valid_plan()
        capabilities = data["capabilities"]
        assert isinstance(capabilities, list)
        target = next(item for item in capabilities if item["name"] == "check")
        target["kind"] = "native_binding"
        self.write_plan(data)
        with self.assertRaisesRegex(CompilerError, "compatibility_alias"):
            load_plan(self.plan_path)

        data = self.valid_plan()
        capabilities = data["capabilities"]
        assert isinstance(capabilities, list)
        target = next(item for item in capabilities if item["name"] == "package")
        target["argv"] = ["echo\nunsafe"]
        self.write_plan(data)
        with self.assertRaisesRegex(CompilerError, "contract violation|line break"):
            load_plan(self.plan_path)

    def test_local_extensions_cannot_override_generated_or_reserved_targets(
        self,
    ) -> None:
        for declaration, target in (
            ("repo-check:\n\t@true\n", "repo-check"),
            ("pr:\n\t@true\n", "pr"),
            ("inventory repo-build:\n\t@true\n", "repo-build"),
            ("release::\n\t@true\n", "release"),
        ):
            with self.subTest(target=target):
                self.local_path.write_text(declaration, encoding="utf-8")
                with self.assertRaisesRegex(
                    CompilerError, rf"protected target '{target}'"
                ):
                    validate_local_extensions(
                        self.local_path,
                        [f"repo-{name}" for name in STANDARD_CAPABILITIES],
                    )

    def test_missing_optional_local_extension_is_valid(self) -> None:
        self.local_path.unlink()
        render(self.plan_path, self.output_path, self.local_path, self.makefile_path)
        check(self.plan_path, self.output_path, self.local_path, self.makefile_path)

    def test_portable_resolved_plan_does_not_bind_core_runtime(self) -> None:
        data = self.valid_plan()
        capabilities = data["capabilities"]
        assert isinstance(capabilities, list)
        for capability in capabilities:
            assert isinstance(capability, dict)
            if capability["state"] == "supported":
                capability["argv"] = ["echo", str(capability["name"])]
        self.write_plan(data)
        render(self.plan_path, self.output_path, self.local_path, self.makefile_path)
        generated = self.output_path.read_text(encoding="utf-8")
        self.assertIn("@echo doctor", generated)
        self.assertNotIn("tools.l9_repo", generated)

    def test_renderer_is_closed_and_shell_free(self) -> None:
        source = (ROOT / "tools" / "l9_make" / "__main__.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "subprocess",
            "os.system",
            "shell=True",
            "eval(",
            "git push",
            "gh pr",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        plan = load_plan(self.plan_path)
        self.assertEqual(render_repo_mk(plan), render_repo_mk(copy.deepcopy(plan)))

    def test_cli_returns_nonzero_for_output_drift(self) -> None:
        self.assertEqual(
            0,
            main(
                [
                    "render",
                    "--plan",
                    str(self.plan_path),
                    "--output",
                    str(self.output_path),
                    "--local",
                    str(self.local_path),
                ]
            ),
        )
        self.output_path.write_text("drift\n", encoding="utf-8")
        self.assertEqual(
            2,
            main(
                [
                    "check",
                    "--plan",
                    str(self.plan_path),
                    "--output",
                    str(self.output_path),
                    "--local",
                    str(self.local_path),
                ]
            ),
        )


if __name__ == "__main__":
    unittest.main()
