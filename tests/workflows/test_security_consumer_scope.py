"""security.yml as the organization runs it for a consumer repository.

Three behaviours the organization workflow relies on, each executed here
rather than read from the YAML text:

* gitleaks honours the consumer's own ``.gitleaks.toml`` on top of the Core
  preset rules (gitleaks' defaults, ``presets/security/.gitleaks.toml``),
  never instead of them;
* gitleaks scans the commit range the caller names, so a consumer is judged
  on what an event changes;
* pip-audit audits the project's own dependencies, not the runner's
  environment.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
SECURITY = ROOT / ".github" / "workflows" / "security.yml"
PRESET = ROOT / "presets" / "security" / ".gitleaks.toml"


def _step(step_id: str) -> dict[str, Any]:
    document = yaml.safe_load(SECURITY.read_text(encoding="utf-8"))
    [step] = [
        s for s in document["jobs"]["security"]["steps"] if s.get("id") == step_id
    ]
    return step


def _compose_source() -> str:
    run = str(_step("gitleaks")["run"])
    start = run.index("<<'PY'\n") + len("<<'PY'\n")
    return run[start : run.index("\nPY\n", start)] + "\n"


class GitleaksConfigLayeringTests(unittest.TestCase):
    def compose(self, text: str) -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / ".gitleaks.toml"
            target = Path(temp) / "runtime.toml"
            source.write_text(text, encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    _compose_source(),
                    str(source),
                    str(target),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            produced = target.read_text(encoding="utf-8") if target.exists() else ""
            return completed.returncode, produced, completed.stdout + completed.stderr

    def test_a_config_without_extend_gains_the_core_preset_rules(self) -> None:
        consumer = (
            'title = "consumer"\n\n[[rules]]\nid = "consumer-token"\n'
            'regex = """ct_[a-z0-9]{32}"""\n\n[allowlist]\npaths = ["fixtures/"]\n'
        )
        code, produced, output = self.compose(consumer)
        self.assertEqual(0, code, output)
        parsed = tomllib.loads(produced)
        self.assertIs(True, parsed["extend"]["useDefault"])
        self.assertEqual("consumer-token", parsed["rules"][0]["id"])
        self.assertEqual(["fixtures/"], parsed["allowlist"]["paths"])

    def test_the_core_preset_itself_passes_through_unchanged(self) -> None:
        preset = PRESET.read_text(encoding="utf-8")
        code, produced, output = self.compose(preset)
        self.assertEqual(0, code, output)
        self.assertEqual(preset, produced)

    def test_an_extend_path_is_refused_not_scanned_with_fewer_rules(self) -> None:
        code, produced, output = self.compose('[extend]\npath = "base.toml"\n')
        self.assertNotEqual(0, code)
        self.assertEqual("", produced)
        self.assertIn("cannot also extend the Core preset rules", output)

    def test_an_extend_that_drops_the_defaults_is_refused(self) -> None:
        code, _, output = self.compose("[extend]\nuseDefault = false\n")
        self.assertNotEqual(0, code)
        self.assertIn("must set useDefault = true", output)

    def test_invalid_toml_is_refused(self) -> None:
        code, _, output = self.compose("[extend\n")
        self.assertNotEqual(0, code)
        self.assertIn("not valid TOML", output)


def _run_with_recording_stubs(
    script: str, workdir: Path, env: dict[str, str], stubs: dict[str, int]
) -> tuple[int, str, str]:
    """Run a step script; every stub appends its argv to calls.log."""
    bin_dir = workdir.parent / "bin"
    bin_dir.mkdir(exist_ok=True)
    calls = workdir.parent / "calls.log"
    calls.touch()
    for name, code in stubs.items():
        stub = bin_dir / name
        stub.write_text(
            f'#!/usr/bin/env bash\necho "{name} $*" >> "{calls}"\nexit {code}\n',
            encoding="utf-8",
        )
        stub.chmod(0o755)
    output = workdir.parent / "output"
    output.touch()
    completed = subprocess.run(
        ["bash", "-c", script],
        cwd=workdir,
        env={
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "GITHUB_OUTPUT": str(output),
            "RUNNER_TEMP": str(workdir.parent),
            **env,
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    return (
        completed.returncode,
        calls.read_text(encoding="utf-8"),
        completed.stdout + completed.stderr + output.read_text(encoding="utf-8"),
    )


def _two_commit_repository(workdir: Path) -> tuple[str, str]:
    """Two empty commits with fixed dates: the same SHAs in every repository.

    Tests compare ranges taken from separately built repositories; without
    fixed dates a commit made in a later clock second gets another SHA.
    """
    dated = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
        "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
    }

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(workdir), *args],
            capture_output=True,
            text=True,
            check=True,
            env=dated,
        ).stdout.strip()

    git("init", "--quiet")
    shas = []
    for message in ("first", "second"):
        git(
            "-c",
            "user.name=core",
            "-c",
            "user.email=core@invalid",
            "commit",
            "--quiet",
            "--allow-empty",
            "-m",
            message,
        )
        shas.append(git("rev-parse", "HEAD"))
    return shas[0], shas[1]


class PipAuditTargetTests(unittest.TestCase):
    STUBS = {"python": 0, "pip": 0, "pip-audit": 0}

    def audit(self, files: dict[str, str]) -> str:
        with tempfile.TemporaryDirectory() as temp:
            workdir = Path(temp) / "repo"
            workdir.mkdir()
            for name, text in files.items():
                (workdir / name).write_text(text, encoding="utf-8")
            code, calls, output = _run_with_recording_stubs(
                str(_step("pip_audit")["run"]),
                workdir,
                {"L9_CI_INSTALL_COMMAND": ""},
                self.STUBS,
            )
            self.assertEqual(0, code, output)
            return [
                line for line in calls.splitlines() if line.startswith("pip-audit")
            ][0]

    def test_requirements_txt_is_audited_when_present(self) -> None:
        self.assertEqual(
            "pip-audit --desc on -r requirements.txt",
            self.audit({"requirements.txt": "x==1\n", "pyproject.toml": ""}),
        )

    def test_a_pyproject_project_is_audited_by_path(self) -> None:
        self.assertEqual("pip-audit --desc on .", self.audit({"pyproject.toml": ""}))

    def test_the_environment_is_audited_only_without_project_files(self) -> None:
        self.assertEqual("pip-audit --desc on", self.audit({}))


class GitleaksInvocationTests(unittest.TestCase):
    def scan(
        self, files: dict[str, str], log_opts: str, **event: str
    ) -> tuple[int, str, str]:
        script = str(_step("gitleaks")["run"])
        acquisition, scan = script.split("tar -xzf", 1)
        # Replace acquisition (download, digest) with a stub binary; keep the
        # config composition and the scan exactly as written.
        composition = acquisition.split('GITLEAKS_VERSION="', 1)[0]
        scan_part = scan.split("\n", 1)[1]
        with tempfile.TemporaryDirectory() as temp:
            workdir = Path(temp) / "repo"
            workdir.mkdir()
            for name, text in files.items():
                (workdir / name).write_text(text, encoding="utf-8")
            first, second = _two_commit_repository(workdir)
            log_opts = log_opts.replace("FIRST", first).replace("SECOND", second)
            event = {
                key: value.replace("FIRST", first).replace("SECOND", second)
                for key, value in event.items()
            }
            stub_dir = Path(temp) / "bin"
            stub_dir.mkdir()
            calls = Path(temp) / "calls.log"
            gitleaks = stub_dir / "gitleaks"
            gitleaks.write_text(
                f'#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "{calls}"\nexit 0\n',
                encoding="utf-8",
            )
            gitleaks.chmod(0o755)
            body = (composition + scan_part).replace("/tmp/gitleaks", str(gitleaks))
            code, _, output = _run_with_recording_stubs(
                body, workdir, {"GITLEAKS_LOG_OPTS": log_opts, **event}, {}
            )
            return code, calls.read_text(encoding="utf-8"), output

    def test_the_named_range_and_the_layered_config_reach_gitleaks(self) -> None:
        code, argv, output = self.scan(
            {".gitleaks.toml": 'title = "c"\n'}, "FIRST..SECOND"
        )
        self.assertEqual(0, code, output)
        lines = argv.splitlines()
        self.assertTrue(
            any(line.startswith("--log-opts=") and ".." in line for line in lines),
            lines,
        )
        config = lines[lines.index("--config") + 1]
        self.assertTrue(config.endswith("l9-gitleaks.toml"), config)
        self.assertIn("outcome=executed_pass", output)

    def test_no_range_and_no_config_scan_everything_with_the_defaults(self) -> None:
        code, argv, output = self.scan({}, "")
        self.assertEqual(0, code, output)
        self.assertNotIn("--config", argv)
        self.assertFalse(
            any(line.startswith("--log-opts") for line in argv.splitlines())
        )

    def log_opts(self, **event: str) -> str:
        code, argv, output = self.scan({}, "", GITLEAKS_SCOPE="event", **event)
        self.assertEqual(0, code, output)
        found = [line for line in argv.splitlines() if line.startswith("--log-opts=")]
        return found[0].removeprefix("--log-opts=") if found else ""

    def test_event_scope_scans_what_each_event_adds(self) -> None:
        pull = self.log_opts(
            EVENT_NAME="pull_request", PR_BASE="FIRST", PR_HEAD="SECOND"
        )
        self.assertRegex(pull, r"^[0-9a-f]{40}\.\.[0-9a-f]{40}$")
        queue = self.log_opts(
            EVENT_NAME="merge_group",
            MERGE_GROUP_BASE="FIRST",
            MERGE_GROUP_HEAD="SECOND",
        )
        self.assertEqual(pull, queue)
        push = self.log_opts(EVENT_NAME="push", PUSH_BEFORE="FIRST", EVENT_SHA="SECOND")
        self.assertEqual(pull, push)
        new_branch = self.log_opts(
            EVENT_NAME="push", PUSH_BEFORE="0" * 40, EVENT_SHA="SECOND"
        )
        self.assertRegex(new_branch, r"^-1 [0-9a-f]{40}$")
        self.assertEqual("", self.log_opts(EVENT_NAME="workflow_dispatch"))

    def test_an_unknown_scope_is_refused(self) -> None:
        code, argv, output = self.scan({}, "", GITLEAKS_SCOPE="everything")
        self.assertEqual(2, code, output)
        self.assertEqual("", argv)

    def test_an_unresolvable_range_never_scans_and_states_no_outcome(self) -> None:
        code, argv, output = self.scan({}, "0000000..1111111")
        self.assertEqual(2, code, output)
        self.assertEqual("", argv)
        self.assertIn("does not resolve in this clone", output)
        self.assertNotIn("outcome=", output)

    def test_a_refused_config_never_scans_and_states_no_outcome(self) -> None:
        code, argv, output = self.scan({".gitleaks.toml": '[extend]\npath = "x"\n'}, "")
        self.assertNotEqual(0, code)
        self.assertEqual("", argv)
        self.assertNotIn("outcome=", output)


if __name__ == "__main__":
    unittest.main()
