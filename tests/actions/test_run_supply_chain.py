"""Core's supply-chain lane (``.github/actions/run-supply-chain``).

The policy merge, the licence-expression judgement and the change detection
run here against real files and a real git history; the action's wiring is
read from its YAML.
"""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / ".github" / "actions" / "run-supply-chain"
SELF = ROOT / ".github" / "workflows" / "self-supply-chain.yml"


def _load():
    spec = importlib.util.spec_from_file_location(
        "core_supply_chain", ACTION / "supply_chain.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sc = _load()


def component(name: str, licence: dict[str, object] | None, purl: str = "") -> dict:
    entry: dict[str, object] = {
        "type": "library",
        "name": name,
        "version": "1.0",
        "purl": purl or f"pkg:pypi/{name}@1.0",
    }
    if licence is not None:
        entry["licenses"] = [licence]
    return entry


class WorkspaceCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def repo_policy(self, payload: object) -> None:
        path = self.root / ".l9" / "supply-chain-policy.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            payload if isinstance(payload, str) else json.dumps(payload),
            encoding="utf-8",
        )


class PolicyTests(WorkspaceCase):
    def test_core_default_policy_alone(self) -> None:
        policy = sc.load_policy(self.root)
        self.assertIn("MIT", policy.allowed)
        self.assertIn("GPL-3.0-only", policy.denied)
        self.assertFalse(policy.allowed & policy.denied)
        self.assertEqual("high", policy.fail_on_severity)
        self.assertEqual((), policy.exempt)

    def test_a_repository_adds_licences_and_exempts_packages(self) -> None:
        self.repo_policy(
            {
                "schema": "l9.supply-chain-repo-policy/v1",
                "additional_allowed_licenses": ["LGPL-3.0-only"],
                "allow_dependencies_licenses": ["pkg:pypi/l9-graphite-memory"],
            }
        )
        policy = sc.load_policy(self.root)
        self.assertIn("LGPL-3.0-only", policy.allowed)
        self.assertEqual(("pkg:pypi/l9-graphite-memory",), policy.exempt)

    def test_a_repository_can_never_allow_a_denied_licence(self) -> None:
        self.repo_policy(
            {
                "schema": "l9.supply-chain-repo-policy/v1",
                "additional_allowed_licenses": ["GPL-3.0-only"],
            }
        )
        with self.assertRaisesRegex(sc.PolicyError, "Core-denied"):
            sc.load_policy(self.root)

    def test_malformed_repository_policies_are_refused(self) -> None:
        for payload in (
            "{not json",
            {"schema": "other"},
            {"schema": "l9.supply-chain-repo-policy/v1", "deny": []},
            {
                "schema": "l9.supply-chain-repo-policy/v1",
                "allow_dependencies_licenses": ["yamllint"],
            },
            {
                "schema": "l9.supply-chain-repo-policy/v1",
                "additional_allowed_licenses": "MIT",
            },
        ):
            with self.subTest(payload=payload):
                self.repo_policy(payload)
                with self.assertRaises(sc.PolicyError):
                    sc.load_policy(self.root)

    def core_repo(self, payload: object, name: str = "acme__widget.json") -> Path:
        directory = self.root / "core-repositories"
        directory.mkdir(exist_ok=True)
        (directory / name).write_text(
            payload if isinstance(payload, str) else json.dumps(payload),
            encoding="utf-8",
        )
        return directory

    def test_core_records_exemptions_per_repository(self) -> None:
        directory = self.core_repo(
            {
                "schema": "l9.supply-chain-core-repo-policy/v1",
                "repository": "Acme/Widget",
                "additional_allowed_licenses": [
                    {"license": "LGPL-3.0-only", "reason": "dynamic link only"}
                ],
                "exemptions": [
                    {
                        "purl": "pkg:pypi/acme-sdk",
                        "license": "Proprietary",
                        "reason": "first-party",
                    }
                ],
            }
        )
        policy = sc.load_policy(
            self.root, repository="ACME/widget", core_repo_dir=directory
        )
        self.assertIn("LGPL-3.0-only", policy.allowed)
        self.assertEqual(("pkg:pypi/acme-sdk",), policy.exempt)
        other = sc.load_policy(
            self.root, repository="acme/other", core_repo_dir=directory
        )
        self.assertEqual((), other.exempt)
        self.assertEqual((), sc.load_policy(self.root, core_repo_dir=directory).exempt)

    def test_core_and_repository_data_merge_additively(self) -> None:
        directory = self.core_repo(
            {
                "schema": "l9.supply-chain-core-repo-policy/v1",
                "repository": "acme/widget",
                "exemptions": [{"purl": "pkg:pypi/a", "reason": "first-party"}],
            }
        )
        self.repo_policy(
            {
                "schema": "l9.supply-chain-repo-policy/v1",
                "allow_dependencies_licenses": ["pkg:pypi/b", "pkg:pypi/a"],
            }
        )
        policy = sc.load_policy(
            self.root, repository="acme/widget", core_repo_dir=directory
        )
        self.assertEqual(("pkg:pypi/a", "pkg:pypi/b"), policy.exempt)

    def test_malformed_core_repository_data_is_refused(self) -> None:
        good = {
            "schema": "l9.supply-chain-core-repo-policy/v1",
            "repository": "acme/widget",
        }
        for payload in (
            "{not json",
            {**good, "schema": "other"},
            {**good, "repository": "acme/other"},
            {**good, "deny": []},
            {**good, "exemptions": [{"purl": "pkg:pypi/a"}]},
            {**good, "exemptions": [{"purl": "pkg:pypi/a", "reason": " "}]},
            {**good, "exemptions": [{"purl": "acme-sdk", "reason": "x"}]},
            {**good, "exemptions": ["pkg:pypi/a"]},
            {
                **good,
                "additional_allowed_licenses": [
                    {"license": "AGPL-3.0-only", "reason": "x"}
                ],
            },
        ):
            with self.subTest(payload=payload):
                directory = self.core_repo(payload)
                with self.assertRaises(sc.PolicyError):
                    sc.load_policy(
                        self.root, repository="acme/widget", core_repo_dir=directory
                    )

    def test_a_repository_name_cannot_leave_the_data_directory(self) -> None:
        for name in ("../etc", "acme/..", "acme", "acme/widget/x", "a b/c"):
            with self.subTest(name=name), self.assertRaises(sc.PolicyError):
                sc.core_repo_path(name)

    def test_core_shipped_repository_data_is_valid(self) -> None:
        files = sorted(sc.CORE_REPO_DIR.glob("*.json"))
        self.assertTrue(files)
        for path in files:
            with self.subTest(path=path.name):
                repository = json.loads(path.read_text(encoding="utf-8"))["repository"]
                self.assertEqual(path, sc.core_repo_path(repository))
                policy = sc.load_policy(self.root, repository=repository)
                self.assertTrue(policy.exempt)
        expected = {
            "Quantum-L9/Cursor-Governance": "pkg:pypi/l9-graphite-memory",
            "Quantum-L9/cognitive.engine.graphs": "pkg:pypi/constellation-node-sdk",
            "Quantum-L9/enrichment.inference.engine": "pkg:pypi/constellation-node-sdk",
        }
        for repository, purl in expected.items():
            with self.subTest(repository=repository):
                self.assertIn(
                    purl, sc.load_policy(self.root, repository=repository).exempt
                )

    def test_the_policy_command_reads_the_repository_from_github(self) -> None:
        out = io.StringIO()
        env = {"GITHUB_OUTPUT": "", "GITHUB_REPOSITORY": "Quantum-L9/Cursor-Governance"}
        with redirect_stdout(out), mock.patch.dict("os.environ", env):
            self.assertEqual(0, sc.main(["--workspace", str(self.root), "policy"]))
        values = dict(line.split("=", 1) for line in out.getvalue().splitlines())
        self.assertEqual(
            "pkg:pypi/l9-graphite-memory", values["allow-dependencies-licenses"]
        )

    def test_the_policy_command_writes_the_dependency_review_inputs(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.dict("os.environ", {"GITHUB_OUTPUT": ""}):
            self.assertEqual(0, sc.main(["--workspace", str(self.root), "policy"]))
        values = dict(line.split("=", 1) for line in out.getvalue().splitlines())
        self.assertIn("MIT", values["allow-licenses"].split(", "))
        self.assertNotIn("GPL-3.0-only", values["allow-licenses"])
        self.assertEqual("high", values["fail-on-severity"])

    def test_a_malformed_policy_fails_the_policy_command(self) -> None:
        self.repo_policy({"schema": "other"})
        with redirect_stderr(io.StringIO()):
            self.assertEqual(2, sc.main(["--workspace", str(self.root), "policy"]))


class LicenceTests(WorkspaceCase):
    def verdict(self, *entries: str) -> str:
        return sc.classify_licences(entries, sc.load_policy(self.root))[0]

    def test_expressions(self) -> None:
        cases = {
            ("MIT",): "allowed",
            ("MIT License",): "allowed",  # alias
            ("GPL-3.0-or-later",): "denied",
            ("LGPL-3.0-only",): "not_allowed",
            ("MIT OR GPL-3.0-only",): "allowed",  # the consumer picks MIT
            ("MIT AND GPL-3.0-only",): "denied",
            ("(Apache-2.0 OR MIT) AND BSD-3-Clause",): "allowed",
            ("Apache-2.0 WITH LLVM-exception",): "allowed",
            ("GPL-2.0-only OR AGPL-3.0-only",): "denied",
            ("LGPL-3.0-only OR GPL-3.0-only",): "not_allowed",
            ("MIT", "Apache-2.0"): "allowed",
            ("MIT", "GPL-3.0-only"): "denied",
            ("NOASSERTION",): "unknown",
            (): "unknown",
            ("Some Custom Licence Text",): "unknown",
            ("Proprietary",): "not_allowed",
        }
        for entries, expected in cases.items():
            with self.subTest(entries=entries):
                self.assertEqual(expected, self.verdict(*entries))


class EvaluateTests(WorkspaceCase):
    def run_evaluate(self, components: list[dict]) -> tuple[int, dict, str]:
        sbom = self.root / "sbom.json"
        report = self.root / "out" / "licences.json"
        sbom.write_text(
            json.dumps({"bomFormat": "CycloneDX", "components": components}),
            encoding="utf-8",
        )
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            code = sc.main(
                [
                    "--workspace",
                    str(self.root),
                    "evaluate",
                    "--sbom",
                    str(sbom),
                    "--report",
                    str(report),
                ]
            )
        data = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {}
        return code, data, out.getvalue()

    def test_allowed_packages_pass_and_unknown_is_reported_not_failed(self) -> None:
        code, data, out = self.run_evaluate(
            [
                component("a", {"license": {"id": "MIT"}}),
                component("b", {"expression": "Apache-2.0 OR MIT"}),
                component("c", None),
            ]
        )
        self.assertEqual(0, code, out)
        self.assertEqual("pass", data["status"])
        self.assertEqual({"allowed": 2, "unknown": 1}, data["counts"])
        self.assertIn("::warning::c 1.0: licence not determined", out)

    def test_denied_and_not_allowed_fail_with_one_error_each(self) -> None:
        code, data, out = self.run_evaluate(
            [
                component("ok", {"license": {"name": "MIT License"}}),
                component("gpl", {"license": {"id": "GPL-3.0-only"}}),
                component("lgpl", {"license": {"id": "LGPL-2.1-only"}}),
            ]
        )
        self.assertEqual(1, code)
        self.assertEqual("fail", data["status"])
        self.assertIn("::error::gpl 1.0: licence GPL-3.0-only is denied", out)
        self.assertIn("::error::lgpl 1.0: licence LGPL-2.1-only is not allowed", out)

    def test_an_exempt_package_passes_whatever_its_licence(self) -> None:
        self.repo_policy(
            {
                "schema": "l9.supply-chain-repo-policy/v1",
                "allow_dependencies_licenses": ["pkg:pypi/constellation-node-sdk"],
            }
        )
        code, data, _ = self.run_evaluate(
            [component("constellation-node-sdk", {"license": {"name": "Proprietary"}})]
        )
        self.assertEqual(0, code)
        self.assertEqual({"exempt": 1}, data["counts"])

    def test_an_empty_inventory_fails_closed(self) -> None:
        code, data, out = self.run_evaluate([])
        self.assertEqual(1, code)
        self.assertEqual("empty", data["status"])
        self.assertIn("proves nothing", out)

    def test_an_unreadable_sbom_is_invalid_input(self) -> None:
        (self.root / "sbom.json").write_text("[]", encoding="utf-8")
        with redirect_stderr(io.StringIO()):
            code = sc.main(
                [
                    "--workspace",
                    str(self.root),
                    "evaluate",
                    "--sbom",
                    str(self.root / "sbom.json"),
                    "--report",
                    str(self.root / "r.json"),
                ]
            )
        self.assertEqual(2, code)


IDENTITY = ("-c", "user.name=core", "-c", "user.email=core@invalid")


class ChangeDetectionTests(WorkspaceCase):
    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    def commit(self, files: dict[str, str]) -> str:
        for name, text in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            self.git("add", "--", name)
        self.git(*IDENTITY, "commit", "--quiet", "-m", "c")
        return self.git("rev-parse", "HEAD")

    def pull_request_checkout(self, change: dict[str, str]) -> None:
        """main + a feature commit, checked out as GitHub's merge commit."""
        self.git("init", "--quiet", "--initial-branch=main")
        self.commit({"app.py": "x = 1\n", "pyproject.toml": "[project]\n"})
        self.git("checkout", "--quiet", "-b", "feature")
        self.commit(change)
        self.git("checkout", "--quiet", "main")
        self.commit({"unrelated.txt": "main moved\n"})
        self.git(*IDENTITY, "merge", "--quiet", "--no-ff", "-m", "merge", "feature")

    def changes(self, env: dict[str, str]) -> dict[str, str]:
        out = io.StringIO()
        with (
            redirect_stdout(out),
            mock.patch.dict("os.environ", {"GITHUB_OUTPUT": "", **env}),
        ):
            self.assertEqual(0, sc.main(["--workspace", str(self.root), "changes"]))
        return dict(
            line.split("=", 1) for line in out.getvalue().splitlines() if "=" in line
        )

    def test_a_pull_request_with_a_dependency_change_runs(self) -> None:
        self.pull_request_checkout({"services/api/requirements-dev.txt": "ruff\n"})
        found = self.changes({"EVENT_NAME": "pull_request"})
        self.assertEqual("true", found["run"])
        self.assertIn("requirements-dev.txt", found["reason"])

    def test_a_pull_request_without_one_does_not_run(self) -> None:
        self.pull_request_checkout({"app.py": "x = 2\n"})
        found = self.changes({"EVENT_NAME": "pull_request"})
        self.assertEqual("false", found["run"])

    def test_other_events_always_run_and_a_non_merge_checkout_runs(self) -> None:
        for event in ("schedule", "workflow_dispatch", "push"):
            with self.subTest(event=event):
                self.assertEqual("true", self.changes({"EVENT_NAME": event})["run"])
        self.git("init", "--quiet")
        self.commit({"app.py": "x = 1\n"})
        with redirect_stdout(io.StringIO()):
            single = self.changes({"EVENT_NAME": "pull_request"})
        self.assertEqual(
            ("true", "change set unreadable"), (single["run"], single["reason"])
        )

    def test_event_values_never_reach_git(self) -> None:
        self.pull_request_checkout({"uv.lock": "x\n"})
        seen: list[list[str]] = []
        real = sc.subprocess.run

        def spy(argv, *args, **kwargs):  # type: ignore[no-untyped-def]
            seen.append(list(argv))
            return real(argv, *args, **kwargs)

        hostile = {
            "EVENT_NAME": "pull_request",
            "BASE_SHA": "--output=/x",
            "HEAD_SHA": "-p",
        }
        with mock.patch.object(sc.subprocess, "run", side_effect=spy):
            self.changes(hostile)
        flat = [arg for argv in seen for arg in argv]
        self.assertNotIn("--output=/x", flat)
        self.assertNotIn("-p", flat)
        # Neither the event nor the --workspace value reaches argv.
        self.assertEqual(
            [list(sc._GIT_PARENTS), list(sc._GIT_CHANGED)],
            seen,
        )

    def test_dependency_file_patterns(self) -> None:
        for path in (
            "uv.lock",
            "a/b/pyproject.toml",
            "requirements.txt",
            "constraints-ci.txt",
            "package-lock.json",
            ".l9/supply-chain-policy.json",
        ):
            with self.subTest(path=path):
                self.assertTrue(sc.DEPENDENCY_FILES.search(path))
        for path in ("README.md", "src/requirements_parser.py", "lockfile.md"):
            with self.subTest(path=path):
                self.assertFalse(sc.DEPENDENCY_FILES.search(path))


class WiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.action = yaml.safe_load(
            (ACTION / "action.yml").read_text(encoding="utf-8")
        )
        self.steps = self.action["runs"]["steps"]

    def test_third_party_actions_are_pinned_by_sha(self) -> None:
        for step in self.steps:
            uses = step.get("uses")
            if uses:
                with self.subTest(uses=uses):
                    self.assertRegex(
                        uses, r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$"
                    )

    def test_every_heavy_step_waits_on_the_change_decision(self) -> None:
        for step in self.steps[1:]:
            with self.subTest(step=step.get("name")):
                self.assertIn("steps.changes.outputs.run == 'true'", str(step["if"]))
        [review] = [
            s for s in self.steps if "dependency-review-action" in str(s.get("uses"))
        ]
        self.assertIn("github.event_name == 'pull_request'", review["if"])
        self.assertEqual("never", review["with"]["comment-summary-in-pr"])

    def test_the_sbom_is_cyclonedx_of_the_isolated_environment(self) -> None:
        [sbom] = [s for s in self.steps if "anchore/sbom-action" in str(s.get("uses"))]
        self.assertEqual("cyclonedx-json", sbom["with"]["format"])
        self.assertEqual("${{ steps.environment.outputs.path }}", sbom["with"]["path"])
        self.assertIs(False, sbom["with"]["upload-artifact"])
        install = next(s for s in self.steps if s.get("id") == "environment")
        self.assertIn("--locked", install["run"])
        self.assertIn("--no-default-groups", install["run"])
        self.assertIn("--no-build", install["run"])
        self.assertIn(
            "pip install --quiet --disable-pip-version-check --only-binary :all: -r requirements.txt",
            install["run"],
        )

    def test_no_step_hides_a_failure(self) -> None:
        for step in self.steps:
            self.assertNotIn("continue-on-error", step)

    def test_core_dogfoods_the_action(self) -> None:
        workflow = yaml.safe_load(SELF.read_text(encoding="utf-8"))
        triggers = workflow[True]
        self.assertIn("schedule", triggers)
        self.assertIn("uv.lock", triggers["pull_request"]["paths"])
        steps = workflow["jobs"]["supply-chain"]["steps"]
        self.assertEqual("./.github/actions/run-supply-chain", steps[-1]["uses"])
        self.assertEqual({"contents": "read"}, workflow["permissions"])


if __name__ == "__main__":
    unittest.main()
