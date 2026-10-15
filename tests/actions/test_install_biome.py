"""Digest-locked Biome installer (``.github/actions/install-biome``).

Core owns the consumer-facing Biome check: the version is the ``biome`` key of
``toolchain-lock.json`` and the bytes are a per-version SHA-256 lock shipped in
the action. These tests keep the shipped lock honest for the locked version and
prove, without network, that no path makes an unverified binary executable:
``curl`` is replaced by a shim that writes chosen bytes.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / ".github" / "actions" / "install-biome"
INSTALL = ACTION / "install.sh"
LOCKS = ACTION / "locks"
CORE_LOCK = ROOT / ".github" / "actions" / "install-consumer-ci" / "toolchain-lock.json"


def _lock_document(version: str, digest: str) -> dict[str, object]:
    return {
        "platforms": {"linux-x64": {"asset": "biome-linux-x64", "sha256": digest}},
        "release": "https://example.invalid/biome",
        "schema": "l9.biome-binary-lock/v1",
        "version": version,
    }


class ShippedLockTests(unittest.TestCase):
    def test_locked_biome_version_has_a_digest_lock(self) -> None:
        version = json.loads(CORE_LOCK.read_text(encoding="utf-8"))["biome"]
        lock = LOCKS / f"biome-{version}.json"
        self.assertTrue(lock.is_file(), f"missing {lock.relative_to(ROOT)}")
        data = json.loads(lock.read_text(encoding="utf-8"))
        self.assertEqual("l9.biome-binary-lock/v1", data["schema"])
        self.assertEqual(version, data["version"])
        entry = data["platforms"]["linux-x64"]
        self.assertEqual("biome-linux-x64", entry["asset"])
        self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$")

    def test_every_lock_names_its_own_version(self) -> None:
        locks = sorted(LOCKS.glob("biome-*.json"))
        self.assertTrue(locks)
        for lock in locks:
            with self.subTest(lock=lock.name):
                version = lock.name[len("biome-") : -len(".json")]
                self.assertRegex(version, r"^[0-9]+\.[0-9]+\.[0-9]+$")
                data = json.loads(lock.read_text(encoding="utf-8"))
                self.assertEqual(version, data["version"])
                for platform, entry in data["platforms"].items():
                    self.assertRegex(platform, r"^linux-(x64|arm64)$")
                    self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$")


class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # Mirror the Core revision layout: the installer reads the sibling
        # install-consumer-ci/toolchain-lock.json when no version is given.
        self.action = self.tmp / "actions" / "install-biome"
        (self.action / "locks").mkdir(parents=True)
        shutil.copy(INSTALL, self.action / "install.sh")
        self.core = self.tmp / "actions" / "install-consumer-ci"
        self.core.mkdir(parents=True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.trace = self.tmp / "curl-trace"
        self.payload = self.tmp / "payload"
        (self.bin / "curl").write_text(
            "#!/usr/bin/env bash\n"
            'echo "$@" >> "$CURL_TRACE"\n'
            'out=""\n'
            "while [[ $# -gt 0 ]]; do\n"
            '  if [[ "$1" == "-o" ]]; then out="$2"; shift; fi\n'
            "  shift\n"
            "done\n"
            'cp "$CURL_PAYLOAD" "$out"\n',
            encoding="utf-8",
        )
        (self.bin / "curl").chmod(0o755)
        self.runner_temp = self.tmp / "runner-temp"
        self.runner_temp.mkdir()

    def _fake_binary(self, version: str) -> str:
        self.payload.write_text(
            f'#!/usr/bin/env bash\necho "Version: {version}"\n', encoding="utf-8"
        )
        return hashlib.sha256(self.payload.read_bytes()).hexdigest()

    def _write_lock(self, version: str, digest: str) -> None:
        (self.action / "locks" / f"biome-{version}.json").write_text(
            json.dumps(_lock_document(version, digest)), encoding="utf-8"
        )

    def _run(
        self, version: str = "", output: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["L9_BIOME_VERSION"] = version
        env["PATH"] = f"{self.bin}:{env['PATH']}"
        env["CURL_TRACE"] = str(self.trace)
        env["CURL_PAYLOAD"] = str(self.payload)
        env["RUNNER_TEMP"] = str(self.runner_temp)
        env.pop("GITHUB_OUTPUT", None)
        if output is not None:
            env["GITHUB_OUTPUT"] = str(output)
        return subprocess.run(
            ["bash", str(self.action / "install.sh")],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )

    def _installed(self) -> list[Path]:
        return sorted(self.runner_temp.rglob("biome"))

    def test_inexact_version_is_refused_before_download(self) -> None:
        for version in ("2.5", "latest", "2.5.8;rm", "v2.5.8"):
            with self.subTest(version=version):
                proc = self._run(version)
                self.assertEqual(2, proc.returncode, proc.stderr)
                self.assertIn("exact x.y.z", proc.stderr)
        self.assertFalse(self.trace.exists(), "downloaded for an inexact version")

    def test_unlisted_version_is_refused_before_download(self) -> None:
        proc = self._run("9.9.9")
        self.assertEqual(2, proc.returncode, proc.stderr)
        self.assertIn("no digest lock for biome 9.9.9", proc.stderr)
        self.assertFalse(self.trace.exists())

    def test_missing_core_lock_fails_closed_when_no_version_is_given(self) -> None:
        proc = self._run("")
        self.assertEqual(2, proc.returncode, proc.stderr)
        self.assertIn("Core toolchain lock missing", proc.stderr)
        self.assertFalse(self.trace.exists())

    def test_digest_mismatch_is_refused_and_nothing_is_made_executable(self) -> None:
        self._fake_binary("1.0.0")
        self._write_lock("1.0.0", "0" * 64)
        proc = self._run("1.0.0")
        self.assertEqual(2, proc.returncode, proc.stderr)
        self.assertIn("digest does not match", proc.stderr)
        self.assertEqual([], self._installed())
        self.assertEqual([], sorted(self.runner_temp.rglob("*.download")))

    def test_platform_without_a_digest_is_refused(self) -> None:
        digest = self._fake_binary("1.0.0")
        document = _lock_document("1.0.0", digest)
        document["platforms"] = {"linux-arm64": {"asset": "biome-x", "sha256": digest}}
        (self.action / "locks" / "biome-1.0.0.json").write_text(
            json.dumps(document), encoding="utf-8"
        )
        proc = self._run("1.0.0")
        self.assertEqual(2, proc.returncode, proc.stderr)
        self.assertFalse(self.trace.exists())

    def test_binary_reporting_another_version_is_refused(self) -> None:
        digest = self._fake_binary("1.0.1")
        self._write_lock("1.0.0", digest)
        proc = self._run("1.0.0")
        self.assertEqual(2, proc.returncode, proc.stderr)
        self.assertIn("expected Version:1.0.0", proc.stderr)

    def test_lock_selected_version_installs_and_reports_provenance(self) -> None:
        digest = self._fake_binary("1.0.0")
        self._write_lock("1.0.0", digest)
        (self.core / "toolchain-lock.json").write_text(
            json.dumps({"biome": "1.0.0"}), encoding="utf-8"
        )
        output = self.tmp / "github-output"
        proc = self._run("", output=output)
        self.assertEqual(0, proc.returncode, proc.stderr)
        values = dict(
            line.split("=", 1)
            for line in output.read_text(encoding="utf-8").splitlines()
        )
        executable = Path(values["executable"])
        self.assertTrue(executable.is_absolute())
        self.assertTrue(os.access(executable, os.X_OK))
        self.assertEqual("1.0.0", values["provider-version"])
        self.assertEqual(digest, values["binary-sha256"])
        self.assertIn(
            "releases/download/%40biomejs%2Fbiome%401.0.0/biome-linux-x64",
            self.trace.read_text(encoding="utf-8"),
        )


class ActionContractTests(unittest.TestCase):
    def test_action_defaults_to_the_lock_and_runs_the_installer(self) -> None:
        action = yaml.safe_load((ACTION / "action.yml").read_text(encoding="utf-8"))
        self.assertEqual("composite", action["runs"]["using"])
        self.assertEqual("", action["inputs"]["biome-version"]["default"])
        for name in ("executable", "provider-version", "binary-sha256"):
            with self.subTest(output=name):
                self.assertEqual(
                    f"${{{{ steps.install.outputs.{name} }}}}",
                    action["outputs"][name]["value"],
                )
        (step,) = action["runs"]["steps"]
        self.assertEqual('bash "${GITHUB_ACTION_PATH}/install.sh"', step["run"].strip())
        self.assertNotIn("uses", step)

    def test_installer_verifies_before_it_makes_anything_executable(self) -> None:
        text = INSTALL.read_text(encoding="utf-8")
        verify = text.index("sha256sum --check")
        chmod = text.index("chmod 0755")
        self.assertLess(verify, chmod)
        self.assertIn("curl -fsSL --retry 3", text)


if __name__ == "__main__":
    unittest.main()
