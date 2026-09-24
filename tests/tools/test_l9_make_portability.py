"""Behavioral portability proof for generated Compiler V2 consumer artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from l9_make.__main__ import STANDARD_CAPABILITIES, render  # noqa: E402


class GeneratedConsumerPortabilityTests(unittest.TestCase):
    @staticmethod
    def digest(seed: str) -> str:
        return "sha256:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()

    @classmethod
    def portable_plan(cls) -> dict[str, object]:
        capabilities: list[dict[str, object]] = []
        for name in STANDARD_CAPABILITIES:
            capability: dict[str, object] = {
                "name": name,
                "state": "not_required",
                "kind": "compatibility_alias" if name == "check" else "native_binding",
                "diagnostic": f"portable fixture does not require {name}",
                "provenance": {
                    "source": f"fixture/{name}",
                    "digest": cls.digest(name),
                },
            }
            if name == "build":
                capability = {
                    "name": name,
                    "state": "supported",
                    "kind": "native_binding",
                    "argv": ["native-tool", "build"],
                    "provenance": {
                        "source": "fixture/native-manifest",
                        "digest": cls.digest("native-manifest"),
                    },
                }
            elif name == "benchmark":
                capability["state"] = "unsupported"
                capability["diagnostic"] = "portable fixture has no benchmark"
            capabilities.append(capability)
        return {
            "schema": "l9.make-plan/v1",
            "repository_class": "portable-fixture",
            "provenance": {
                "producer": "portable-fixture-producer",
                "source": "fixture/resolved-plan",
                "digest": cls.digest("resolved-plan"),
            },
            "capabilities": capabilities,
        }

    @staticmethod
    def write_executable(path: pathlib.Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)

    def run_make(
        self, consumer: pathlib.Path, environment: dict[str, str], target: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["make", "--no-print-directory", target],
            cwd=consumer,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    def test_generated_consumer_executes_without_core_runtime_or_local_extension(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sandbox = pathlib.Path(temporary)
            consumer = sandbox / "consumer"
            fake_bin = consumer / "fake-bin"
            consumer.mkdir()
            fake_bin.mkdir()
            plan_path = sandbox / "resolved-plan.json"
            plan_path.write_text(
                json.dumps(self.portable_plan(), indent=2), encoding="utf-8"
            )

            native_receipt = sandbox / "native-argv.txt"
            dispatcher_receipt = sandbox / "dispatcher-argv.txt"
            forbidden_receipt = sandbox / "forbidden-command.txt"
            self.write_executable(
                fake_bin / "native-tool",
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                'printf "%s\\n" "$@" > "$NATIVE_RECEIPT"\n',
            )
            self.write_executable(
                fake_bin / "l9",
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                'printf "%s\\n" "$@" > "$L9_RECEIPT"\n',
            )
            for forbidden in ("git", "gh"):
                self.write_executable(
                    fake_bin / forbidden,
                    "#!/usr/bin/env bash\n"
                    "set -euo pipefail\n"
                    f'printf "%s\\n" "{forbidden} $*" >> "$FORBIDDEN_RECEIPT"\n'
                    "exit 99\n",
                )

            render(
                plan_path,
                consumer / "Repo.mk",
                consumer / "Repo.local.mk",
                consumer / "Makefile",
            )

            self.assertFalse((consumer / "Repo.local.mk").exists())
            self.assertFalse((consumer / "tools").exists())
            self.assertEqual(
                {"Makefile", "Repo.mk", "fake-bin"},
                {path.name for path in consumer.iterdir()},
            )

            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
            environment["NATIVE_RECEIPT"] = str(native_receipt)
            environment["L9_RECEIPT"] = str(dispatcher_receipt)
            environment["FORBIDDEN_RECEIPT"] = str(forbidden_receipt)

            help_result = self.run_make(consumer, environment, "help")
            self.assertEqual(0, help_result.returncode, help_result.stderr)
            self.assertIn("capabilities", help_result.stdout)

            capabilities_result = self.run_make(consumer, environment, "capabilities")
            self.assertEqual(
                0, capabilities_result.returncode, capabilities_result.stderr
            )
            self.assertIn("build            supported", capabilities_result.stdout)
            self.assertIn("package          not_required", capabilities_result.stdout)
            self.assertIn("benchmark        unsupported", capabilities_result.stdout)

            build_result = self.run_make(consumer, environment, "build")
            self.assertEqual(0, build_result.returncode, build_result.stderr)
            self.assertEqual("build\n", native_receipt.read_text(encoding="utf-8"))

            not_required_result = self.run_make(consumer, environment, "package")
            self.assertEqual(
                0, not_required_result.returncode, not_required_result.stderr
            )
            self.assertIn("NOT_REQUIRED: package", not_required_result.stdout)

            unsupported_result = self.run_make(consumer, environment, "benchmark")
            self.assertEqual(2, unsupported_result.returncode)
            self.assertIn("UNSUPPORTED: benchmark", unsupported_result.stderr)

            pr_result = self.run_make(consumer, environment, "pr")
            self.assertEqual(0, pr_result.returncode, pr_result.stderr)
            self.assertEqual("pr\n", dispatcher_receipt.read_text(encoding="utf-8"))
            self.assertFalse(forbidden_receipt.exists())


if __name__ == "__main__":
    unittest.main()
