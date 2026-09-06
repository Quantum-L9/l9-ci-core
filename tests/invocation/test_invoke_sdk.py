from __future__ import annotations
import importlib.util
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / ".github/actions/invoke-sdk/invoke.py"
spec = importlib.util.spec_from_file_location("invoke_sdk", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class InvokeSDKTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        # invoke.resolve_workspace_path canonicalizes symlinks via Path.resolve()
        # as a path-escape defense, so it emits resolved paths. Resolve the
        # workspace root here too; otherwise on platforms whose temp dir is a
        # symlink (e.g. macOS /var -> /private/var) the fixtures stay unresolved
        # and the exact-path assertions mismatch. Keeps the assertions strict.
        self.workspace = Path(self.temp.name).resolve()
        self.executable = self.workspace / "l9-ci"
        self.executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.executable.chmod(self.executable.stat().st_mode | stat.S_IXUSR)
        self.report = self.workspace / "semgrep.json"
        self.report.write_text("{}\n", encoding="utf-8")
        self.root = self.workspace / "repository"
        self.root.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def environment(self, **values: str) -> dict[str, str]:
        result = {
            "GITHUB_WORKSPACE": str(self.workspace),
            "L9_EXECUTABLE": str(self.executable),
            "L9_OPERATION": "semgrep-normalize",
            "L9_INPUT": str(self.report),
            "L9_OUTPUT": str(self.workspace / "bundle.json"),
            "L9_ROOT": str(self.root),
            "L9_SNAPSHOT_ID": "snapshot-1",
            "L9_PROVIDER_VERSION": "1.0.0",
            "L9_IDENTITY_MAP": "",
            "L9_POLICY": "",
            "L9_GENERATED_AT": "",
            "L9_REVISION": "a" * 40,
            "L9_STRICT": "true",
            "L9_REQUIRED": "true",
            "L9_DIRTY": "false",
            "L9_MINIMUM_SDK_VERSION": "",
        }
        result.update(values)
        return result

    def test_semgrep_normalize_command_is_structured(self) -> None:
        with patch.dict(os.environ, self.environment(), clear=True):
            command = module.build_command(self.executable)
        self.assertEqual(
            [str(self.executable), "semgrep", "normalize"],
            command[:3],
        )
        self.assertIn("--strict", command)
        self.assertIn("--required", command)
        self.assertIn("--no-dirty", command)

    def test_semgrep_normalize_always_passes_the_provider_version(self) -> None:
        """The flag the SDK requires must actually be on the command line."""
        with patch.dict(os.environ, self.environment(), clear=True):
            command = module.build_command(self.executable)
        self.assertIn("--provider-version", command)
        self.assertEqual(
            "1.0.0",
            command[command.index("--provider-version") + 1],
        )

    def test_semgrep_normalize_without_a_provider_version_fails_here(self) -> None:
        """Fail in Core, naming the input, not in the SDK naming a flag.

        `l9-ci semgrep normalize` declares --provider-version required=True, so
        omitting it never worked -- it produced `the following arguments are
        required: --provider-version` from argparse one layer down, which names
        neither the workflow input nor this action. This asserts the failure is
        now raised where the missing input actually is.
        """
        with patch.dict(
            os.environ,
            self.environment(L9_PROVIDER_VERSION=""),
            clear=True,
        ):
            with self.assertRaises(module.InvocationError) as caught:
                module.build_command(self.executable)
        self.assertIn("provider-version", str(caught.exception))

    def test_semgrep_run_needs_no_provider_version(self) -> None:
        """The asymmetry is deliberate, so pin it.

        `run` has the SDK execute Semgrep, so the SDK knows the version and
        the flag does not exist on that subcommand. Requiring it for `run`
        too would be wrong.
        """
        with patch.dict(
            os.environ,
            self.environment(
                L9_OPERATION="semgrep-run",
                L9_LANGUAGE="python",
                L9_PROVIDER_VERSION="",
            ),
            clear=True,
        ):
            command = module.build_command(self.executable)
        self.assertNotIn("--provider-version", command)

    def test_semgrep_run_maps_to_sdk_execution(self) -> None:
        bundle = self.workspace / "bundle.json"
        raw = self.workspace / "raw" / "report.json"
        with patch.dict(
            os.environ,
            self.environment(
                L9_OPERATION="semgrep-run",
                L9_LANGUAGE="python",
                L9_OUTPUT=str(bundle),
                L9_RAW_OUTPUT=str(raw),
                L9_SEMGREP_PROFILE="l9-baseline",
                L9_PROVIDER_VERSION="",
            ),
            clear=True,
        ):
            command = module.build_command(self.executable)
        self.assertEqual(
            [str(self.executable), "semgrep", "run", "--language", "python"],
            command[:5],
        )
        self.assertIn("--output", command)
        self.assertIn(str(bundle), command)
        self.assertIn("--raw-output", command)
        self.assertIn(str(raw), command)
        self.assertIn("--profile", command)
        self.assertIn("l9-baseline", command)
        self.assertIn("--strict", command)
        self.assertIn("--required", command)
        self.assertIn("--no-dirty", command)
        # Core never parses the provider report or authors a --config list.
        self.assertNotIn("--config", command)
        self.assertNotIn("--provider-version", command)

    def test_semgrep_run_rejects_unsupported_language(self) -> None:
        with patch.dict(
            os.environ,
            self.environment(
                L9_OPERATION="semgrep-run",
                L9_LANGUAGE="ruby",
                L9_OUTPUT=str(self.workspace / "bundle.json"),
            ),
            clear=True,
        ):
            with self.assertRaises(module.InvocationError):
                module.build_command(self.executable)

    def test_semgrep_run_requires_language(self) -> None:
        with patch.dict(
            os.environ,
            self.environment(
                L9_OPERATION="semgrep-run",
                L9_LANGUAGE="",
                L9_OUTPUT=str(self.workspace / "bundle.json"),
            ),
            clear=True,
        ):
            with self.assertRaises(module.InvocationError):
                module.build_command(self.executable)

    def test_semgrep_run_raw_output_path_escape_is_rejected(self) -> None:
        outside = self.workspace.parent / "outside-raw.json"
        with patch.dict(
            os.environ,
            self.environment(
                L9_OPERATION="semgrep-run",
                L9_LANGUAGE="python",
                L9_OUTPUT=str(self.workspace / "bundle.json"),
                L9_RAW_OUTPUT=str(outside),
            ),
            clear=True,
        ):
            with self.assertRaises(module.InvocationError):
                module.build_command(self.executable)

    def test_unknown_operation_is_rejected(self) -> None:
        with patch.dict(
            os.environ,
            self.environment(L9_OPERATION="shell"),
            clear=True,
        ):
            with self.assertRaises(module.InvocationError):
                module.build_command(self.executable)

    def test_path_escape_is_rejected(self) -> None:
        outside = self.workspace.parent / "outside.json"
        outside.write_text("{}\n", encoding="utf-8")
        try:
            with patch.dict(
                os.environ,
                self.environment(L9_INPUT=str(outside)),
                clear=True,
            ):
                with self.assertRaises(module.InvocationError):
                    module.build_command(self.executable)
        finally:
            outside.unlink(missing_ok=True)

    def test_bundle_validation_command(self) -> None:
        bundle = self.workspace / "bundle.json"
        bundle.write_text("{}\n", encoding="utf-8")
        with patch.dict(
            os.environ,
            self.environment(
                L9_OPERATION="bundle-validate",
                L9_INPUT=str(bundle),
            ),
            clear=True,
        ):
            command = module.build_command(self.executable)
        self.assertEqual(
            [
                str(self.executable),
                "bundle",
                "validate",
                str(bundle),
            ],
            command,
        )

    def test_bundle_project_sarif_command(self) -> None:
        bundle = self.workspace / "bundle.json"
        bundle.write_text("{}\n", encoding="utf-8")
        sarif = self.workspace / "results.sarif"
        with patch.dict(
            os.environ,
            self.environment(
                L9_OPERATION="bundle-project-sarif",
                L9_INPUT=str(bundle),
                L9_OUTPUT=str(sarif),
                L9_STRICT="true",
            ),
            clear=True,
        ):
            command = module.build_command(self.executable)
        self.assertEqual(
            [
                str(self.executable),
                "bundle",
                "project-sarif",
                "--input",
                str(bundle),
                "--output",
                str(sarif),
                "--strict",
            ],
            command,
        )
        # Core hands off to the SDK; it never translates findings itself.
        self.assertNotIn("--config", command)

    def test_bundle_project_sarif_output_path_escape_is_rejected(self) -> None:
        bundle = self.workspace / "bundle.json"
        bundle.write_text("{}\n", encoding="utf-8")
        outside = self.workspace.parent / "outside.sarif"
        with patch.dict(
            os.environ,
            self.environment(
                L9_OPERATION="bundle-project-sarif",
                L9_INPUT=str(bundle),
                L9_OUTPUT=str(outside),
            ),
            clear=True,
        ):
            with self.assertRaises(module.InvocationError):
                module.build_command(self.executable)


if __name__ == "__main__":
    unittest.main()
