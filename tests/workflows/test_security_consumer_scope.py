"""security.yml as the organization runs it for a consumer repository.

Three behaviours the organization workflow relies on, each executed here
rather than read from the YAML text:

* gitleaks writes the Core preset (gitleaks' defaults via ``useDefault``,
  ``presets/security/.gitleaks.toml``) plus the consumer's ``[allowlist]``;
  a consumer's ``[[rules]]`` are not copied;
* gitleaks scans the commit range the caller names, so a consumer is judged
  on what an event changes;
* pip-audit audits the project's own dependencies, not the runner's
  environment.
"""

from __future__ import annotations

import hashlib
import os
import re
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


#: A stand-in for gitleaks' default rules. The composer reads the file;
#: tests/workflows/test_gitleaks_core_rules.py covers repository rules.
DEFAULTS = '[[rules]]\nid = "openai-api-key"\nregex = "x"\n'


def _core_rules() -> str:
    """The Core rules the step embeds (its TOML heredoc)."""
    run = str(_step("gitleaks")["run"])
    start = run.index("<<'TOML'\n") + len("<<'TOML'\n")
    return run[start : run.index("\nTOML\n", start)] + "\n"


class GitleaksConfigLayeringTests(unittest.TestCase):
    def compose(self, text: str) -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / ".gitleaks.toml"
            target = Path(temp) / "runtime.toml"
            defaults = Path(temp) / "defaults.toml"
            core = Path(temp) / "core.toml"
            source.write_text(text, encoding="utf-8")
            defaults.write_text(DEFAULTS, encoding="utf-8")
            core.write_text(_core_rules(), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    _compose_source(),
                    str(source),
                    str(defaults),
                    str(core),
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
        core = [rule["id"] for rule in tomllib.loads(_core_rules())["rules"]]
        self.assertEqual(core, [rule["id"] for rule in parsed["rules"]])
        self.assertNotIn("consumer-token", [rule["id"] for rule in parsed["rules"]])
        self.assertEqual(["fixtures/"], parsed["allowlist"]["paths"])

    def test_the_core_preset_itself_scans_as_core_rules_only(self) -> None:
        preset = PRESET.read_text(encoding="utf-8")
        code, produced, output = self.compose(preset)
        self.assertEqual(0, code, output)
        parsed = tomllib.loads(produced)
        self.assertIs(True, parsed["extend"]["useDefault"])
        core = [rule["id"] for rule in tomllib.loads(_core_rules())["rules"]]
        self.assertEqual(core, [rule["id"] for rule in parsed["rules"]])
        self.assertNotIn("allowlist", parsed)

    def test_an_extend_path_is_not_copied(self) -> None:
        code, produced, output = self.compose('[extend]\npath = "base.toml"\n')
        self.assertEqual(0, code, output)
        parsed = tomllib.loads(produced)
        self.assertEqual({"useDefault": True}, parsed["extend"])

    def test_an_extend_that_drops_the_defaults_is_replaced(self) -> None:
        code, produced, output = self.compose("[extend]\nuseDefault = false\n")
        self.assertEqual(0, code, output)
        self.assertIs(True, tomllib.loads(produced)["extend"]["useDefault"])

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

    def test_a_project_without_a_lock_is_incomplete_not_audited_by_path(
        self,
    ) -> None:
        # SEC-LOCKSET: neither the bare project (`pip-audit .`) nor the
        # runner's environment is the consumer's locked set; with no lock the
        # audit is incomplete and fails (tests/workflows/
        # test_security_pip_audit_lockset.py covers the locked paths).
        for files in ({"pyproject.toml": ""}, {}):
            with (
                self.subTest(files=sorted(files)),
                tempfile.TemporaryDirectory() as temp,
            ):
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
                self.assertEqual(1, code, output)
                self.assertNotIn("pip-audit ", calls)
                self.assertIn("outcome=not_executed (no lock file)", output)


class GitleaksInvocationTests(unittest.TestCase):
    def scan(
        self, files: dict[str, str], log_opts: str, **event: str
    ) -> tuple[int, str, str]:
        script = str(_step("gitleaks")["run"])
        acquisition, scan = script.split("tar -xzf", 1)
        # Replace the binary's acquisition (download, digest) with a stub
        # binary; keep the config composition and the scan exactly as written.
        # The default rules' download is served a stand-in by a curl stub,
        # its pinned checksum replaced by the stand-in's.
        composition = acquisition.split("# HTTPS only, on every redirect too", 1)[0]
        composition = re.sub(
            r'GITLEAKS_DEFAULTS_SHA256="[0-9a-f]{64}"',
            'GITLEAKS_DEFAULTS_SHA256="'
            + hashlib.sha256(DEFAULTS.encode("utf-8")).hexdigest()
            + '"',
            composition,
        )
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
            stand_in = Path(temp) / "defaults.toml"
            stand_in.write_text(DEFAULTS, encoding="utf-8")
            curl = stub_dir / "curl"
            curl.write_text(
                "#!/usr/bin/env bash\n"
                'while [ $# -gt 0 ]; do [ "$1" = -o ] && out="$2"; shift; done\n'
                f'cp "{stand_in}" "$out"\n',
                encoding="utf-8",
            )
            curl.chmod(0o755)
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

    def test_no_range_and_no_config_scan_the_revisions_history_with_core_rules(
        self,
    ) -> None:
        # A repository without a .gitleaks.toml still gets the Core rules
        # (docs/gitleaks-core-rules.md): the composed config is always passed.
        # With no range the scan is the evaluated revision's own history
        # (HEAD), never gitleaks' default `git log --all`: a fetch-depth 0
        # checkout holds every branch, and an unrelated one must not decide
        # this revision's verdict.
        code, argv, output = self.scan({}, "")
        self.assertEqual(0, code, output)
        lines = argv.splitlines()
        self.assertTrue(lines[lines.index("--config") + 1].endswith("l9-gitleaks.toml"))
        self.assertIn("--log-opts=HEAD", lines)

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
        self.assertEqual("HEAD", self.log_opts(EVENT_NAME="workflow_dispatch"))

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

    def test_a_path_extend_still_scans_the_core_config(self) -> None:
        code, argv, output = self.scan({".gitleaks.toml": '[extend]\npath = "x"\n'}, "")
        self.assertEqual(0, code, output)
        self.assertIn("--config", argv)
        self.assertIn("outcome=executed_pass", output)
        self.assertIn("0 repository rule(s) not copied", output)


if __name__ == "__main__":
    unittest.main()
