"""Signal law (Quantum-L9/.github L9-SIGNAL-001): a failure names its cause.

A required check that fails must name its subject so a remediation agent can
act without re-running it: the failing command, its phase and the tail of its
output; each instance that did not pass, its reasons, and each blocking
finding by rule and file:line. Never only a status, a count or an exit code.
These are the live counter-examples the operator named:

- CEG #532 (job 136944955939): "repository verification did not pass (mode
  required; status fail; failure command)": no command, phase or output.
- CEG #529 (job 136941291412): "Required instances: fail (exit 1; accounted
  true)": no instance, rule or finding.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
ORG_CI = ROOT / ".github" / "workflows" / "org-ci.yml"
CENTRAL_SEMGREP = ROOT / ".github" / "workflows" / "central-semgrep.yml"
RUNNER = ROOT / ".github" / "actions" / "run-repository-verification" / "run.py"
REGISTRY_FIXTURE = (
    ROOT / "tests" / "fixtures" / "repository-registry" / "repository_registry.yaml"
)


def _load_runner() -> Any:
    spec = importlib.util.spec_from_file_location("l9_verify_signal", RUNNER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.fetch_registry = REGISTRY_FIXTURE.read_text
    return module


runner = _load_runner()


def _step(name: str, workflow: Path = ORG_CI) -> dict[str, Any]:
    steps = yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"]["analyze"][
        "steps"
    ]
    [step] = [s for s in steps if s.get("name") == name]
    return step


def _bash(
    script: str, cwd: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script],
        cwd=cwd,
        env={"PATH": os.environ["PATH"], **env},
        capture_output=True,
        text=True,
        check=False,
    )


class PhaseFailureTests(unittest.TestCase):
    """run.py names the failed make phase, its command, exit code and tail."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.temp = self.dir / "runner-temp"
        self.temp.mkdir()
        (self.dir / "Makefile").write_text(
            "validate:\n"
            '\t@for n in $$(seq 1 60); do echo "validate line $$n"; done\n'
            "\t@echo 'FAILED tests/test_x.py::test_y - assert 1 == 2' >&2\n"
            "\t@exit 3\n",
            encoding="utf-8",
        )

    def failed_phase(self) -> Any:
        with self.assertRaises(runner.PhaseError) as raised:
            runner.run_phase("validate", ["make", "validate"], self.dir)
        return raised.exception

    def test_a_failing_phase_keeps_its_command_exit_and_last_40_lines(self) -> None:
        error = self.failed_phase()
        # make reports a failed recipe as its own exit 2.
        self.assertEqual(("validate", 2), (error.phase, error.returncode))
        self.assertEqual(["make", "validate"], error.cmd)
        self.assertEqual(40, len(error.tail))
        # make may add "Entering/Leaving directory" lines when nested.
        self.assertTrue(
            any("FAILED tests/test_x.py::test_y" in line for line in error.tail)
        )
        self.assertNotIn("validate line 1\n", error.tail)

    def test_the_failure_is_named_in_an_error_and_a_sidecar(self) -> None:
        error = self.failed_phase()
        out = io.StringIO()
        with (
            unittest.mock.patch.dict(os.environ, {"RUNNER_TEMP": str(self.temp)}),
            redirect_stdout(out),
        ):
            runner.report_phase_failure(error, self.dir)
        text = out.getvalue()
        self.assertIn(
            "::error title=Repository verification::phase validate: `make validate` exited 2",
            text,
        )
        self.assertIn("FAILED tests/test_x.py::test_y", text)
        sidecar = json.loads((self.temp / runner.FAILURE_SIDECAR).read_text())
        self.assertEqual(
            {
                "phase": "validate",
                "command": "make validate",
                "exit": 2,
                "workspace": str(self.dir.resolve()),
            },
            sidecar,
        )
        self.assertIn("assert 1 == 2", (self.temp / runner.FAILURE_TAIL).read_text())

    def test_a_passing_phase_raises_nothing_and_still_streams(self) -> None:
        (self.dir / "Makefile").write_text("validate:\n\t@echo ok\n", encoding="utf-8")
        runner.run_phase("validate", ["make", "validate"], self.dir)


class EnforcementNamesTheCommandTests(unittest.TestCase):
    def test_the_enforcement_error_names_phase_command_exit_and_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            (temp / runner.FAILURE_SIDECAR).write_text(
                json.dumps(
                    {
                        "phase": "test",
                        "command": "make test",
                        "exit": 2,
                        "workspace": str(temp.resolve()),
                    }
                )
            )
            (temp / runner.FAILURE_TAIL).write_text("E   AssertionError: boom\n")
            summary = temp / "summary"
            completed = _bash(
                _step("Enforce repository verification")["run"],
                temp,
                {
                    "RUNNER_TEMP": str(temp),
                    "GITHUB_WORKSPACE": str(temp),
                    "GITHUB_STEP_SUMMARY": str(summary),
                    "REPOSITORY_VERIFY_STATUS": "fail",
                    "REPOSITORY_VERIFY_MODE": "required",
                    "REPOSITORY_VERIFY_FAILURE": "command",
                },
            )
        self.assertEqual(1, completed.returncode)
        self.assertIn(
            "::error title=Repository verification::phase test: `make test` exited 2",
            completed.stdout,
        )
        self.assertIn("E   AssertionError: boom", completed.stdout)

    def test_a_sidecar_for_another_workspace_is_never_trusted(self) -> None:
        """Core's own tests run the verifier on temporary trees inside its
        verification; their sidecar must not name the wrong command."""
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as other,
        ):
            temp = Path(tmp)
            (temp / runner.FAILURE_SIDECAR).write_text(
                json.dumps(
                    {
                        "phase": "",
                        "command": "python3 -c raise SystemExit(1)",
                        "exit": 1,
                        "workspace": other,
                    }
                )
            )
            (temp / runner.FAILURE_TAIL).write_text("from a test\n")
            completed = _bash(
                _step("Enforce repository verification")["run"],
                temp,
                {
                    "RUNNER_TEMP": tmp,
                    "GITHUB_WORKSPACE": tmp,
                    "REPOSITORY_VERIFY_STATUS": "fail",
                    "REPOSITORY_VERIFY_MODE": "required",
                    "REPOSITORY_VERIFY_FAILURE": "command",
                },
            )
        self.assertEqual(1, completed.returncode)
        self.assertNotIn("SystemExit", completed.stdout)
        self.assertNotIn("from a test", completed.stdout)
        self.assertIn("Run repository verification contract", completed.stdout)

    def test_a_new_verification_clears_a_stale_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for name in (runner.FAILURE_SIDECAR, runner.FAILURE_TAIL):
                Path(tmp, name).write_text("stale")
            with unittest.mock.patch.dict(os.environ, {"RUNNER_TEMP": tmp}):
                runner.clear_failure_sidecar()
            self.assertEqual([], list(Path(tmp).iterdir()))

    def test_without_a_sidecar_the_error_still_points_at_the_cause(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed = _bash(
                _step("Enforce repository verification")["run"],
                Path(tmp),
                {
                    "RUNNER_TEMP": tmp,
                    "REPOSITORY_VERIFY_STATUS": "fail",
                    "REPOSITORY_VERIFY_MODE": "required",
                    "REPOSITORY_VERIFY_FAILURE": "contract",
                },
            )
        self.assertEqual(1, completed.returncode)
        self.assertIn("::error title=Repository verification::", completed.stdout)
        self.assertIn("Run repository verification contract", completed.stdout)

    def test_a_passing_verification_is_silent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed = _bash(
                _step("Enforce repository verification")["run"],
                Path(tmp),
                {
                    "RUNNER_TEMP": tmp,
                    "REPOSITORY_VERIFY_STATUS": "pass",
                    "REPOSITORY_VERIFY_MODE": "required",
                },
            )
        self.assertEqual(0, completed.returncode)
        self.assertNotIn("::error", completed.stdout)


class RequiredInstancesAreNamedTests(unittest.TestCase):
    STEP = "Enforce central mode on SDK technical gate"

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        runtime = self.dir / ".l9" / "runtime" / "m"
        instances = runtime / "required-instances"
        (instances / "typescript").mkdir(parents=True)
        (instances / "aggregate.json").write_text(
            json.dumps(
                {
                    "instances": [
                        {
                            "id": "semgrep:language:python",
                            "status": "pass",
                            "reason": "",
                        },
                        {
                            "id": "semgrep:language:typescript",
                            "status": "fail",
                            "reason": "blocking findings",
                        },
                    ]
                }
            )
        )
        findings = [
            {
                "finding_id": f"f{n}",
                "provider_rule_id": "javascript.eval",
                "canonical_rule_id": "L9-JS-EVAL",
                "message": f"eval of untrusted input {n}",
                "locations": [{"normalized_path": "src/app.ts", "start_line": 10 + n}],
            }
            for n in range(25)
        ]
        (instances / "typescript" / "finding-bundle.json").write_text(
            json.dumps({"findings": findings, "provider_failures": []})
        )
        scan = self.dir / ".l9" / "runtime" / "m" / "scan-tree" / "src"
        scan.mkdir(parents=True)
        (scan / "app.ts").write_text(
            "".join(f"const line{n} = {n};\n" for n in range(1, 60)), encoding="utf-8"
        )
        (instances / "typescript" / "gate-result.json").write_text(
            json.dumps(
                {
                    "blocking_finding_ids": [f["finding_id"] for f in findings],
                    "unresolved_finding_ids": [],
                    "reasons": ["25 blocking finding(s)"],
                }
            )
        )

    def run_step(self, mode: str = "blocking") -> subprocess.CompletedProcess[str]:
        return _bash(
            _step(self.STEP, CENTRAL_SEMGREP)["run"],
            self.dir,
            {
                "GITHUB_STEP_SUMMARY": str(self.dir / "summary"),
                "GATE_STATUS": "pass",
                "AGGREGATE_STATUS": "fail",
                "AGGREGATE_EXIT": "1",
                "ACCOUNTED": "true",
                "MODE": mode,
                "L9_MATRIX_ID": "m",
                "L9_PRIMARY_LANGUAGE": "python",
                "L9_SCAN_ROOT": ".l9/runtime/m/scan-tree",
            },
        )

    def test_a_located_finding_carries_its_source_line_and_context(self) -> None:
        out = self.run_step().stdout
        self.assertIn("::group::src/app.ts:10 L9-JS-EVAL", out)
        block = out.split("::group::src/app.ts:10 L9-JS-EVAL", 1)[1].split(
            "::endgroup::", 1
        )[0]
        self.assertEqual(
            [
                "      8 | const line8 = 8;",
                "      9 | const line9 = 9;",
                ">    10 | const line10 = 10;",
                "     11 | const line11 = 11;",
                "     12 | const line12 = 12;",
            ],
            block.strip("\n").splitlines(),
        )
        self.assertIn("endLine=10", out)
        summary = (self.dir / "summary").read_text()
        self.assertIn(">    10 | const line10 = 10;", summary)

    def test_a_secret_finding_never_prints_its_source(self) -> None:
        secret = "AKIA" + "Z" * 16
        scan = self.dir / ".l9/runtime/m/scan-tree"
        (scan / "config.env").write_text(f"A=1\nKEY={secret}\nB=2\n", encoding="utf-8")
        bundle_path = (
            self.dir / ".l9/runtime/m/required-instances/typescript/finding-bundle.json"
        )
        bundle = json.loads(bundle_path.read_text())
        bundle["findings"][0].update(
            provider_id="gitleaks",
            locations=[{"normalized_path": "config.env", "start_line": 2}],
        )
        bundle_path.write_text(json.dumps(bundle))
        completed = self.run_step()
        everything = (
            completed.stdout + completed.stderr + (self.dir / "summary").read_text()
        )
        self.assertNotIn(secret, everything)
        self.assertNotIn("AKIA", everything)
        self.assertIn("::group::config.env:2 L9-JS-EVAL", completed.stdout)
        self.assertIn("(masked: secret finding)", completed.stdout)
        self.assertNotIn(
            "A=1",
            completed.stdout.split("::group::config.env:2", 1)[1].split("::endgroup::")[
                0
            ],
        )

    def test_the_failing_instance_and_its_findings_are_named(self) -> None:
        completed = self.run_step()
        out = completed.stdout
        self.assertEqual(1, completed.returncode)
        self.assertIn(
            "::error title=Required instance semgrep%3Alanguage%3Atypescript::fail: blocking findings",
            out,
        )
        self.assertIn("25 blocking finding(s)", out)
        self.assertIn(
            "::error file=src/app.ts,line=10,endLine=10,title=L9-JS-EVAL (blocking%2C semgrep%3Alanguage%3Atypescript)::eval of untrusted input 0",
            out,
        )
        self.assertNotIn(
            "semgrep%3Alanguage%3Apython", out, "a passing instance is not named"
        )

    def test_findings_are_capped_at_20_with_the_rest_counted(self) -> None:
        out = self.run_step().stdout
        self.assertEqual(20, out.count("title=L9-JS-EVAL"))
        self.assertIn("+5 more finding(s) not listed", out)

    def test_the_summary_carries_the_same_cause(self) -> None:
        self.run_step()
        summary = (self.dir / "summary").read_text()
        self.assertIn("### Why the required instances did not pass", summary)
        self.assertIn("eval of untrusted input 0", summary)

    def test_advisory_mode_names_the_cause_without_failing(self) -> None:
        completed = self.run_step(mode="advisory")
        self.assertEqual(0, completed.returncode)
        self.assertIn("title=L9-JS-EVAL", completed.stdout)

    def test_no_aggregate_is_named_too(self) -> None:
        (self.dir / ".l9/runtime/m/required-instances/aggregate.json").unlink()
        out = self.run_step().stdout
        self.assertIn("no aggregate was written", out)


if __name__ == "__main__":
    unittest.main()
