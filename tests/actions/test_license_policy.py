"""Core's organization licence policy (``.github/actions/license-policy``).

Step one of the supply-chain lane: the policy is Core data shipped in a pinned
action, so a consumer cannot author it (AGENTS.md §3). ``render`` feeds
dependency review; ``audit`` is the SBOM licence check with the GPL/AGPL audit.
A policy violation blocks (exit 1); an unreadable or empty SBOM is an
infrastructure failure (exit 3, AGENTS.md §9).
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from dataclasses import dataclass
from typing import Any
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[2]
ACTION_DIR = ROOT / ".github" / "actions" / "license-policy"
SCRIPT = ACTION_DIR / "license_policy.py"
REPO = "Quantum-L9/l9-ci-core"

spec = importlib.util.spec_from_file_location("license_policy", SCRIPT)
assert spec is not None
assert spec.loader is not None
policy = importlib.util.module_from_spec(spec)
sys.modules["license_policy"] = policy
spec.loader.exec_module(policy)


@dataclass(frozen=True)
class Result:
    returncode: int
    stdout: str
    stderr: str


def component(name: str, purl: str, *licences: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "version": "1.0", "purl": purl, "licenses": list(licences)}


def spdx(identifier: str) -> dict[str, Any]:
    return {"license": {"id": identifier}}


def named(text: str) -> dict[str, Any]:
    return {"license": {"name": text}}


def expression(text: str) -> dict[str, Any]:
    return {"expression": text}


class AuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _sbom(self, *components: dict[str, Any], fmt: str = "CycloneDX") -> Path:
        path = self.tmp / "sbom.json"
        path.write_text(
            json.dumps(
                {"bomFormat": fmt, "specVersion": "1.6", "components": list(components)}
            ),
            encoding="utf-8",
        )
        return path

    def _cli(self, *args: str) -> Result:
        """Run the CLI in-process with the temp dir as workspace, so coverage measures it."""
        stdout, stderr = io.StringIO(), io.StringIO()
        extra = ["--workspace", str(self.tmp)] if args and args[0] == "audit" else []
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            contextlib.chdir(self.tmp),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = policy.main([*args, *extra])
        return Result(code, stdout.getvalue(), stderr.getvalue())

    def _status(self, *licences: dict[str, Any], purl: str = "pkg:pypi/example") -> str:
        record = policy.audit(
            self._sbom(component("example", purl, *licences)), REPO, False
        )
        return str(record["components"][0]["status"])

    def test_allowed_licences_pass(self) -> None:
        self.assertEqual("allowed", self._status(spdx("MIT")))
        self.assertEqual(
            "allowed", self._status(expression("Apache-2.0 AND BSD-3-Clause"))
        )
        self.assertEqual("allowed", self._status(expression("(MIT OR GPL-3.0-only)")))
        self.assertEqual(
            "allowed", self._status(expression("Apache-2.0 WITH LLVM-exception"))
        )

    def test_gpl_and_agpl_audit_blocks_by_id_and_by_name(self) -> None:
        for licence in (
            spdx("GPL-3.0-only"),
            spdx("GPL-2.0-or-later"),
            spdx("AGPL-3.0-only"),
            expression("MIT AND GPL-3.0-or-later"),
            named("GNU General Public License v3 (GPLv3)"),
            named("GNU Affero General Public License v3"),
        ):
            with self.subTest(licence=licence):
                self.assertEqual("copyleft", self._status(licence))
        record = policy.audit(
            self._sbom(component("agpl", "pkg:pypi/agpl", spdx("AGPL-3.0-only"))),
            REPO,
            False,
        )
        self.assertEqual(["AGPL"], record["violations"][0]["families"])

    def test_lgpl_is_its_own_family_never_gpl(self) -> None:
        self.assertEqual(["LGPL"], [policy.family_of("LGPL-3.0-only", ("GPL", "LGPL"))])
        self.assertEqual(["GPL"], [policy.family_of("GPL-3.0-only", ("GPL", "LGPL"))])
        self.assertIsNone(policy.family_of("LGPL-3.0-only", ("GPL",)))
        record = policy.audit(
            self._sbom(
                component(
                    "lgpl",
                    "pkg:pypi/lgpl",
                    named("GNU Lesser General Public License v3"),
                )
            ),
            REPO,
            False,
        )
        self.assertEqual(["LGPL"], record["violations"][0]["families"])

    def test_unlisted_licence_is_not_allowed(self) -> None:
        self.assertEqual("not_allowed", self._status(spdx("CC-BY-NC-4.0")))
        self.assertEqual("not_allowed", self._status(named("Proprietary")))

    def test_unknown_licence_is_reported_unless_the_caller_fails_it(self) -> None:
        sbom = self._sbom(component("bare", "pkg:pypi/bare"))
        reported = policy.audit(sbom, REPO, False)
        self.assertEqual("pass", reported["status"])
        self.assertEqual(1, reported["counts"]["unknown"])
        self.assertEqual("fail", policy.audit(sbom, REPO, True)["status"])

    def test_per_repository_exemption_names_the_package_and_its_reason(self) -> None:
        purl = "pkg:githubactions/SonarSource/sonarqube-scan-action@59202af3"
        record = policy.audit(
            self._sbom(component("sonar", purl, spdx("LGPL-3.0"))), REPO, False
        )
        entry = record["components"][0]
        self.assertEqual("exempted", entry["status"])
        self.assertIn("#521", entry["exemption_reason"])
        self.assertEqual("pass", record["status"])
        other = policy.audit(
            self._sbom(component("sonar", purl, spdx("LGPL-3.0"))),
            "Quantum-L9/other",
            False,
        )
        self.assertEqual("fail", other["status"])

    def test_cli_exit_codes(self) -> None:
        ok = self._sbom(component("ok", "pkg:pypi/ok", spdx("MIT")))
        result = self._cli("audit", "--repository", REPO, "--sbom", str(ok))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("l9.license-audit/v1", json.loads(result.stdout)["schema"])
        self.assertEqual(["sbom.json"], sorted(p.name for p in self.tmp.iterdir()))
        bad = self._sbom(component("gpl", "pkg:pypi/gpl", spdx("GPL-3.0-only")))
        result = self._cli("audit", "--repository", REPO, "--sbom", str(bad))
        self.assertEqual(1, result.returncode)
        self.assertIn("copyleft: pkg:pypi/gpl", result.stderr)

    def test_unreadable_or_empty_sbom_is_infrastructure(self) -> None:
        for path in (
            self.tmp / "absent.json",
            self._sbom(fmt="SPDX"),
        ):
            with self.subTest(path=path.name):
                result = self._cli("audit", "--repository", REPO, "--sbom", str(path))
                self.assertEqual(3, result.returncode)
        empty = self._sbom()
        self.assertEqual(
            3, self._cli("audit", "--repository", REPO, "--sbom", str(empty)).returncode
        )
        garbage = self.tmp / "garbage.json"
        garbage.write_text("{", encoding="utf-8")
        self.assertEqual(
            3,
            self._cli("audit", "--repository", REPO, "--sbom", str(garbage)).returncode,
        )

    def test_paths_outside_the_workspace_are_refused(self) -> None:
        ok = self._sbom(component("ok", "pkg:pypi/ok", spdx("MIT")))
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        shutil.copy(ok, outside / "sbom.json")
        result = self._cli(
            "audit", "--repository", REPO, "--sbom", str(outside / "sbom.json")
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("resolves outside the workspace", result.stderr)
        result = self._cli("audit", "--repository", REPO, "--sbom", "../sbom.json")
        self.assertEqual(2, result.returncode)
        self.assertFalse((self.tmp / ".l9").exists())

    def test_the_script_runs_isolated_as_the_action_calls_it(self) -> None:
        self._sbom(component("ok", "pkg:pypi/ok", spdx("MIT")))
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                str(SCRIPT),
                "audit",
                "--repository",
                REPO,
                "--sbom",
                "sbom.json",
            ],
            cwd=self.tmp,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_repository_must_be_owner_slash_repo(self) -> None:
        result = self._cli("render", "--repository", "../escape")
        self.assertEqual(2, result.returncode)


class ExpressionTests(unittest.TestCase):
    def test_spdx_expression_is_split_without_regex(self) -> None:
        self.assertEqual(
            [["MIT"], ["Apache-2.0"]], policy.alternatives("MIT OR Apache-2.0")
        )
        self.assertEqual(
            [["Apache-2.0", "BSD-3-Clause"]],
            policy.alternatives("(Apache-2.0 AND BSD-3-Clause)"),
        )
        self.assertEqual(
            [["GPL-2.0-only"], ["MIT"]],
            policy.alternatives("GPL-2.0-only WITH Classpath-exception-2.0 or MIT"),
        )
        self.assertEqual([], policy.alternatives(" OR AND "))
        long = " OR ".join(["MIT"] * 20000)
        self.assertEqual(20000, len(policy.alternatives(long)))

    def test_audit_summary_reaches_the_step_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sbom = Path(tmp) / "sbom.json"
            sbom.write_text(
                json.dumps(
                    {
                        "bomFormat": "CycloneDX",
                        "components": [
                            component("gpl", "pkg:pypi/gpl", spdx("GPL-3.0-only"))
                        ],
                    }
                ),
                encoding="utf-8",
            )
            summary = Path(tmp) / "summary.md"
            with (
                mock.patch.dict(
                    os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}, clear=True
                ),
                contextlib.chdir(tmp),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                code = policy.main(
                    [
                        "audit",
                        "--repository",
                        REPO,
                        "--sbom",
                        "sbom.json",
                        "--workspace",
                        tmp,
                    ]
                )
            self.assertEqual(1, code)
            text = summary.read_text(encoding="utf-8")
        self.assertIn("Licence audit — FAIL", text)
        self.assertIn("**copyleft** `pkg:pypi/gpl`", text)


class RenderTests(unittest.TestCase):
    def test_render_feeds_dependency_review_from_the_same_policy(self) -> None:
        rendered = policy.render(REPO)
        allow = rendered["allow-licenses"].split(", ")
        self.assertIn("MIT", allow)
        self.assertFalse([item for item in allow if "GPL" in item])
        self.assertEqual(
            "pkg:githubactions/SonarSource/sonarqube-scan-action",
            rendered["allow-dependencies-licenses"],
        )
        self.assertEqual(
            "", policy.render("Quantum-L9/other")["allow-dependencies-licenses"]
        )

    def test_render_writes_github_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "output"
            with (
                mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(out)}, clear=True),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(0, policy.main(["render", "--repository", REPO]))
            text = out.read_text(encoding="utf-8")
        self.assertIn("allow-licenses=", text)
        self.assertIn("allow-dependencies-licenses=pkg:githubactions/", text)


class PolicyDataTests(unittest.TestCase):
    def test_policy_never_allows_a_denied_family(self) -> None:
        doc = json.loads((ACTION_DIR / "policy.json").read_text(encoding="utf-8"))
        families = tuple(doc["deny_families"])
        self.assertLessEqual({"GPL", "AGPL"}, set(families))
        self.assertEqual([], [i for i in doc["allow"] if policy.family_of(i, families)])
        self.assertEqual(sorted(doc["allow"]), doc["allow"])

    def test_every_repository_file_loads_and_names_its_repository(self) -> None:
        files = sorted((ACTION_DIR / "repositories").glob("*.json"))
        self.assertTrue(files)
        for path in files:
            with self.subTest(path=path.name):
                repository = path.stem.replace("__", "/")
                loaded = policy.load_policy(repository)
                self.assertTrue(
                    all(e["reason"].strip() for e in loaded.exemptions.values())
                )

    def test_exemption_without_a_reason_is_refused(self) -> None:
        original = policy.REPOSITORIES
        with tempfile.TemporaryDirectory() as tmp:
            policy.REPOSITORIES = Path(tmp)
            try:
                (Path(tmp) / "Quantum-L9__demo.json").write_text(
                    json.dumps(
                        {
                            "schema": "l9.license-policy-repository/v1",
                            "repository": "Quantum-L9/demo",
                            "exemptions": [
                                {
                                    "package": "pkg:pypi/x",
                                    "license": "GPL-3.0",
                                    "reason": " ",
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                with self.assertRaises(policy.PolicyError) as caught:
                    policy.load_policy("Quantum-L9/demo")
                self.assertEqual(2, caught.exception.code)
            finally:
                policy.REPOSITORIES = original


class ActionTests(unittest.TestCase):
    def test_inputs_reach_the_shell_through_env_only(self) -> None:
        action = yaml.safe_load((ACTION_DIR / "action.yml").read_text(encoding="utf-8"))
        self.assertEqual("composite", action["runs"]["using"])
        (step,) = action["runs"]["steps"]
        self.assertNotIn("${{", step["run"])
        self.assertIn("license_policy.py", step["run"])
        self.assertIn("render)", step["run"])
        self.assertIn("audit)", step["run"])


if __name__ == "__main__":
    unittest.main()
