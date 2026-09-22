from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "actions"
    / "provision-sdk"
    / "provision.py"
)
spec = importlib.util.spec_from_file_location("provision_sdk", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RuntimeDirectorySafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name).resolve()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_fixed_action_owned_runtime_is_accepted_when_absent(self) -> None:
        runtime = module.resolve_runtime_directory(self.workspace, ".l9/runtime/sdk")
        self.assertEqual(self.workspace / ".l9" / "runtime" / "sdk", runtime)
        self.assertFalse(runtime.exists())

    def test_destructive_or_escaping_runtime_choices_fail_closed(self) -> None:
        for runtime_input in (".", ".l9", ".l9/runtime", "../sdk"):
            with self.subTest(runtime_input=runtime_input):
                with self.assertRaises(module.ProvisioningError):
                    module.resolve_runtime_directory(self.workspace, runtime_input)
        with self.assertRaises(module.ProvisioningError):
            module.resolve_runtime_directory(
                self.workspace, str(self.workspace / ".l9" / "runtime" / "sdk")
            )

    def test_existing_runtime_is_never_deleted_or_replaced(self) -> None:
        runtime = self.workspace / ".l9" / "runtime" / "sdk"
        runtime.mkdir(parents=True)
        sentinel = runtime / "consumer-owned.txt"
        sentinel.write_text("preserve\n", encoding="utf-8")

        with self.assertRaises(module.ProvisioningError):
            module.resolve_runtime_directory(self.workspace, ".l9/runtime/sdk")

        self.assertEqual("preserve\n", sentinel.read_text(encoding="utf-8"))

    @unittest.skipIf(os.name == "nt", "symlink creation varies on Windows")
    def test_symlinked_runtime_component_is_rejected_without_touching_target(
        self,
    ) -> None:
        outside = self.workspace / "outside"
        outside.mkdir()
        sentinel = outside / "consumer-owned.txt"
        sentinel.write_text("preserve\n", encoding="utf-8")
        (self.workspace / ".l9").symlink_to(outside, target_is_directory=True)

        with self.assertRaises(module.ProvisioningError) as caught:
            module.resolve_runtime_directory(self.workspace, ".l9/runtime/sdk")

        self.assertIn("symlink", str(caught.exception))
        self.assertEqual("preserve\n", sentinel.read_text(encoding="utf-8"))

    def test_main_rejects_unsafe_runtime_before_checkout_or_install(self) -> None:
        environment = {
            "GITHUB_WORKSPACE": str(self.workspace),
            "INPUT_SDK_SOURCE": module.EXPECTED_SOURCE,
            "INPUT_SDK_REPOSITORY": module.EXPECTED_REPOSITORY,
            "INPUT_SDK_REVISION": module.EXPECTED_REVISION,
            "INPUT_RUNTIME_DIRECTORY": ".",
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(module, "checkout_sdk") as checkout,
            patch.object(module, "create_runtime") as create_runtime,
        ):
            self.assertEqual(1, module.main())
        checkout.assert_not_called()
        create_runtime.assert_not_called()

    def test_runtime_creation_is_create_only_under_a_race(self) -> None:
        runtime = self.workspace / ".l9" / "runtime" / "sdk"
        runtime.mkdir(parents=True)
        sentinel = runtime / "consumer-owned.txt"
        sentinel.write_text("preserve\n", encoding="utf-8")

        with self.assertRaises(module.ProvisioningError):
            module.create_runtime_directory(runtime)

        self.assertEqual("preserve\n", sentinel.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
