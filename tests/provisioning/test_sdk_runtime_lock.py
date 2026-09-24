from __future__ import annotations

import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / ".github" / "actions" / "provision-sdk" / "provision.py"
spec = importlib.util.spec_from_file_location("provision_sdk", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

SDK_REVISION = "bc678190582694f6efee08b6b7ea39be7e09bd5c"
REQUIREMENTS_SHA256 = "96cad34733f60567c110bc9b7a25679ae5d2c76a3107dad8e118868ab59b70e3"
LOCK_NAME = "cpython-3.12.14-linux-x86_64.txt"


class CompatibilityBootstrapTests(unittest.TestCase):
    def test_manifest_is_parsed_without_acquiring_yaml(self) -> None:
        with patch.object(module, "run") as run:
            entry = module.select_manifest_entry(SDK_REVISION)
        run.assert_not_called()
        self.assertEqual(REQUIREMENTS_SHA256, entry["requirements_sha256"])
        self.assertEqual([LOCK_NAME], entry["runtime_locks"])

    def test_unsupported_yaml_features_fail_closed(self) -> None:
        with self.assertRaises(module.ProvisioningError):
            module._parse_manifest("schema: {name: unsupported}\n")

    def test_bootstrap_imports_only_the_standard_library(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        for dependency in ("yaml", "jsonschema", "packaging", "requests"):
            self.assertNotRegex(source, rf"(?m)^\s*(?:from|import)\s+{dependency}\b")


class RequirementsVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.checkout = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_exact_checkout_requirements_bytes_are_accepted(self) -> None:
        content = b"jsonschema==4.26.0\nreferencing==0.37.0\nPyYAML==6.0.3\n"
        (self.checkout / "requirements.txt").write_bytes(content)
        module.verify_requirements_file(
            self.checkout, hashlib.sha256(content).hexdigest()
        )

    def test_changed_checkout_requirements_fail_closed(self) -> None:
        (self.checkout / "requirements.txt").write_text(
            "PyYAML==6.0.4\n", encoding="utf-8"
        )
        with self.assertRaises(module.ProvisioningError):
            module.verify_requirements_file(self.checkout, REQUIREMENTS_SHA256)

    def test_missing_checkout_requirements_fail_closed(self) -> None:
        with self.assertRaises(module.ProvisioningError):
            module.verify_requirements_file(self.checkout, REQUIREMENTS_SHA256)


class RuntimeLockTests(unittest.TestCase):
    def test_current_platform_selects_the_shipped_lock(self) -> None:
        entry = module.select_manifest_entry(SDK_REVISION)
        with patch.object(module, "runtime_platform", return_value=LOCK_NAME[:-4]):
            lock = module.select_runtime_lock(entry)
        self.assertEqual(LOCK_NAME, lock.name)

    def test_unbound_platform_fails_closed(self) -> None:
        entry = module.select_manifest_entry(SDK_REVISION)
        with (
            patch.object(module, "runtime_platform", return_value="other-platform"),
            self.assertRaises(module.ProvisioningError),
        ):
            module.select_runtime_lock(entry)

    def test_lock_must_bind_the_selected_requirements_digest(self) -> None:
        entry = module.select_manifest_entry(SDK_REVISION).copy()
        entry["requirements_sha256"] = "0" * 64
        with (
            patch.object(module, "runtime_platform", return_value=LOCK_NAME[:-4]),
            self.assertRaises(module.ProvisioningError),
        ):
            module.select_runtime_lock(entry)

    def test_lock_is_complete_exact_hashed_and_wheel_only(self) -> None:
        lock = module.RUNTIME_LOCKS / LOCK_NAME
        text = lock.read_text(encoding="utf-8")
        for requirement in (
            "attrs==26.1.0",
            "jsonschema==4.26.0",
            "jsonschema-specifications==2025.9.1",
            "pip==26.2.1",
            "pyyaml==6.0.3",
            "referencing==0.37.0",
            "rpds-py==2026.6.3",
            "typing-extensions==4.16.0",
        ):
            self.assertIn(requirement, text)
        requirement_lines = [
            line
            for line in text.splitlines()
            if line and not line.startswith(("#", " "))
        ]
        self.assertEqual(8, len(requirement_lines))
        self.assertNotIn("git+", text)
        self.assertNotIn("http://", text)
        self.assertNotIn("https://", text)

    def test_install_uses_only_binary_and_required_hashes(self) -> None:
        python = Path("/runtime/venv/bin/python")
        lock = Path("/core/locks/sdk.txt")
        with patch.object(module, "run") as run:
            module.install_runtime_dependencies(python, lock)
        command = run.call_args.args[0]
        self.assertEqual(str(python), command[0])
        self.assertIn("--only-binary", command)
        self.assertIn(":all:", command)
        self.assertIn("--require-hashes", command)
        self.assertEqual(str(lock), command[-1])

    def test_create_runtime_never_installs_checkout_requirements(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checkout = root / "source"
            checkout.mkdir()
            requirements = checkout / "requirements.txt"
            requirements.write_text("PyYAML==6.0.3\n", encoding="utf-8")
            runtime = root / "runtime"
            runtime.mkdir()
            lock = root / "runtime-lock.txt"
            lock.write_text("locked\n", encoding="utf-8")
            calls: list[list[str]] = []

            def record(command: list[str], **kwargs) -> None:
                calls.append(command)

            with patch.object(module, "run", record):
                module.create_runtime(checkout, runtime, lock)

        flattened = [argument for command in calls for argument in command]
        self.assertIn(str(lock), flattened)
        self.assertNotIn(str(requirements), flattened)


if __name__ == "__main__":
    unittest.main()
