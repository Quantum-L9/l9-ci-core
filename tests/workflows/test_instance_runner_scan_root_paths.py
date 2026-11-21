"""Every instance-runner copy hands the SDK paths that survive its chdir.

The SDK's Semgrep provider runs ``semgrep scan --json-output <report>`` with
the working directory set to ``--root`` and then checks ``<report>.exists()``
from its own working directory. Once instances scan the clean scan tree
(``.l9/runtime/<matrix>/scan-tree``) instead of the checkout, ``--root`` is
not the working directory, so a relative ``--output`` lands under the scan
tree, the SDK finds no report and the instance ends ``incomplete``
(provider failure ``report_missing``, Semgrep exit 0). That is what failed the
Cursor-Governance promotion-canary leg at 7804cea7 for the typescript and
consumer instances while the primary language (absolute paths through
invoke-sdk) passed.

These tests run each copy of the runner that scans the scan tree (org-ci.yml,
nightly-sink.yml and the promotion canary's candidate legs) against a fake SDK
that reproduces that chdir, and prove the fake catches the defect by running
the same copies with the instance directory left relative.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
ACTIONS = ROOT / ".github" / "actions"
INVENTORY = ROOT / "tests" / "fixtures" / "capability-inventory" / "mixed.json"
COPIES = ("org-ci.yml", "nightly-sink.yml", "promotion-canary.yml")
# The canary's base leg is origin/main's org-ci.yml verbatim; it is judged
# where it comes from and does not scan a scan tree.
BASE_LEG = re.compile(
    r"^ {2}# --- base pins leg: begin .*?^ {2}# --- base pins leg: end ---\n",
    re.MULTILINE | re.DOTALL,
)
RESOLVED_DIRECTORY = 'Path(env["L9_INSTANCE_DIR"]).resolve()'
RELATIVE_DIRECTORY = 'Path(env["L9_INSTANCE_DIR"])'
SDK = "d" * 40

# Mirrors the SDK provider: Semgrep runs in --root, the report is looked up
# from the SDK's own working directory, and a missing report is an incomplete
# bundle (exit 0) that the gate evaluates as incomplete (exit 6).
FAKE_SDK = """\
#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys
args = sys.argv[1:]
if args[:2] in (["semgrep", "run"], ["semgrep", "consumer-run"]):
    root = args[args.index("--root") + 1]
    bundle = pathlib.Path(args[args.index("--output") + 1])
    report = bundle.with_name("semgrep-report.json")
    subprocess.run(["semgrep", "scan", "--json-output", str(report)], cwd=root, check=True)
    failures = [] if report.exists() else [{"kind": "report_missing", "exit_code": 0}]
    bundle.parent.mkdir(parents=True, exist_ok=True)
    bundle.write_text(json.dumps({"provider_failures": failures}), encoding="utf-8")
    raise SystemExit(0)
if args[:2] == ["bundle", "validate"]:
    raise SystemExit(0)
if args[:2] == ["gate", "evaluate"]:
    bundle = json.loads(pathlib.Path(args[args.index("--bundle") + 1]).read_text())
    raise SystemExit(6 if bundle["provider_failures"] else 0)
raise SystemExit(64)
"""
FAKE_SEMGREP = """\
#!/usr/bin/env python3
import pathlib, sys
args = sys.argv[1:]
report = pathlib.Path(args[args.index("--json-output") + 1])
report.parent.mkdir(parents=True, exist_ok=True)
report.write_text("{}", encoding="utf-8")
"""


def runners(name: str) -> list[str]:
    """The embedded Python of every scan-tree instances step in a workflow."""
    text = BASE_LEG.sub("", (WORKFLOWS / name).read_text(encoding="utf-8"))
    document = yaml.safe_load(text)
    found = []
    for job in document["jobs"].values():
        for step in job.get("steps", []):
            if step.get("id") != "instances":
                continue
            run = str(step["run"])
            start = run.index("<<'PY'\n") + len("<<'PY'\n")
            end = run.index("\nPY", start)
            found.append(textwrap.dedent(run[start:end]) + "\n")
    return found


def _executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class ScanRootPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.directory = self.tmp / ".l9" / "runtime" / "m" / "required-instances"
        self.directory.mkdir(parents=True)
        (self.tmp / ".l9" / "runtime" / "m" / "scan-tree").mkdir()
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        self.sdk = bin_dir / "l9-ci"
        self.semgrep = bin_dir / "semgrep"
        _executable(self.sdk, FAKE_SDK)
        _executable(self.semgrep, FAKE_SEMGREP)
        self._plan()

    def _plan(self) -> None:
        record: dict[str, Any] = json.loads(INVENTORY.read_text(encoding="utf-8"))
        record["result"]["provider_prerequisites"].append(
            {
                "provider_id": "semgrep",
                "target": "consumer:quantum-l9.fixture",
                "state": "applicable",
                "reason": "consumer rulepack declared and in scope",
            }
        )
        (self.directory / "inventory.json").write_text(
            json.dumps(record), encoding="utf-8"
        )
        completed = subprocess.run(
            [sys.executable, str(ACTIONS / "plan-required-instances" / "plan.py")],
            cwd=self.tmp,
            env={
                "PATH": os.environ["PATH"],
                "GITHUB_WORKSPACE": str(self.tmp),
                "GITHUB_OUTPUT": str(self.tmp / "plan.out"),
                "L9_PLAN_INVENTORY": ".l9/runtime/m/required-instances/inventory.json",
                "L9_PLAN_REPOSITORY": "Quantum-L9/fixture",
                "L9_PLAN_REVISION": record["result"]["snapshot"]["revision"],
                "L9_PLAN_SDK_REVISION": SDK,
                "L9_PLAN_REQUIRED_PROVIDERS": "semgrep",
                "L9_PLAN_OUTPUT": ".l9/runtime/m/required-instances/plan.json",
            },
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)

    def run_copy(self, source: str) -> str:
        shutil.rmtree(self.directory / "results", ignore_errors=True)
        completed = subprocess.run(
            [sys.executable, "-I", "-c", source],
            cwd=self.tmp,
            env={
                "PATH": os.environ["PATH"],
                "L9_INSTANCE_DIR": ".l9/runtime/m/required-instances",
                "L9_SCAN_ROOT": ".l9/runtime/m/scan-tree",
                "L9_EXECUTABLE": str(self.sdk),
                "L9_PROVIDER_EXECUTABLE": str(self.semgrep),
                "L9_PRIMARY_LANGUAGE": "python",
                "L9_PRIMARY_STATUS": "pass",
                "L9_STRICT": "false",
                "L9_REQUIRED": "true",
                "L9_POLICY": "policy.yaml",
                "L9_IDENTITY_DIR": "maps",
            },
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        return completed.stdout

    def copies(self) -> list[tuple[str, int, str]]:
        found = [
            (name, index, source)
            for name in COPIES
            for index, source in enumerate(runners(name))
        ]
        self.assertEqual(
            set(COPIES), {name for name, _, _ in found}, "every copy is under test"
        )
        return found

    def test_every_copy_scans_the_scan_tree(self) -> None:
        for name, index, source in self.copies():
            with self.subTest(workflow=name, copy=index):
                self.assertIn('env.get("L9_SCAN_ROOT"', source)
                self.assertIn(RESOLVED_DIRECTORY, source)

    def test_every_instance_concludes_when_the_root_is_not_the_cwd(self) -> None:
        for name, index, source in self.copies():
            with self.subTest(workflow=name, copy=index):
                printed = self.run_copy(source)
                self.assertIn("instance semgrep:language:typescript: pass", printed)
                self.assertIn(
                    "instance semgrep:consumer:quantum-l9.fixture: pass", printed
                )

    def test_a_relative_instance_directory_reproduces_report_missing(self) -> None:
        """The 7804cea7 defect: the report lands under the scan tree."""
        for name, index, source in self.copies():
            with self.subTest(workflow=name, copy=index):
                relative = source.replace(RESOLVED_DIRECTORY, RELATIVE_DIRECTORY)
                self.assertNotEqual(source, relative)
                printed = self.run_copy(relative)
                self.assertIn(
                    "instance semgrep:language:typescript: incomplete", printed
                )
                self.assertIn(
                    "instance semgrep:consumer:quantum-l9.fixture: incomplete",
                    printed,
                )
                stray = self.tmp / ".l9/runtime/m/scan-tree/.l9/runtime/m"
                self.assertTrue(stray.is_dir(), "Semgrep wrote under the scan tree")
                shutil.rmtree(self.tmp / ".l9/runtime/m/scan-tree/.l9")


if __name__ == "__main__":
    unittest.main()
