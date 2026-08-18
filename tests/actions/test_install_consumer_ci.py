"""Identity tests for the consumer CI installer action.

Pin strings must stay identical to Core's declared lock. Do not invent versions.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / ".github" / "actions" / "install-consumer-ci"
PINS = ACTION / "requirements-consumer-ci.txt"
LOCK = ACTION / "toolchain-lock.json"
INSTALL = ACTION / "install.sh"
PRECOMMIT = ROOT / ".pre-commit-config.yaml"
BIOME = ROOT / "presets" / "typescript" / "biome.json"
ROOT_INCLUDE = ROOT / "requirements-consumer-ci.txt"

PIN_RE = re.compile(r"^(ruff|mypy|pytest)==([0-9][^\s]+)$", re.M)


def _pins() -> dict[str, str]:
    text = PINS.read_text(encoding="utf-8")
    found = dict(PIN_RE.findall(text))
    if set(found) != {"ruff", "mypy", "pytest"}:
        raise AssertionError(f"pin file missing exact toolchain lines: {found}")
    return found


class InstallConsumerCiTests(unittest.TestCase):
    def test_pin_file_and_lock_json_match(self) -> None:
        pins = _pins()
        lock = json.loads(LOCK.read_text(encoding="utf-8"))
        self.assertEqual(lock["ruff"], pins["ruff"])
        self.assertEqual(lock["mypy"], pins["mypy"])
        self.assertEqual(lock["pytest"], pins["pytest"])

    def test_biome_lock_matches_preset_schema(self) -> None:
        lock = json.loads(LOCK.read_text(encoding="utf-8"))
        schema = json.loads(BIOME.read_text(encoding="utf-8"))["$schema"]
        match = re.search(r"/schemas/([0-9.]+)/", schema)
        self.assertIsNotNone(match, f"biome schema unreadable: {schema}")
        self.assertEqual(lock["biome"], match.group(1))

    def test_precommit_rev_equals_ruff_pin(self) -> None:
        pins = _pins()
        text = PRECOMMIT.read_text(encoding="utf-8")
        match = re.search(r"(?m)^\s+rev:\s+(v[0-9.]+)\s*$", text)
        self.assertIsNotNone(match, "pre-commit ruff rev missing")
        self.assertEqual(match.group(1), f"v{pins['ruff']}")

    def test_root_file_is_include_only(self) -> None:
        text = ROOT_INCLUDE.read_text(encoding="utf-8")
        self.assertIn(
            "-r .github/actions/install-consumer-ci/requirements-consumer-ci.txt",
            text,
        )
        self.assertIsNone(PIN_RE.search(text))

    def test_install_sh_rejects_unpinned_ruff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            (dest / "install.sh").write_text(
                INSTALL.read_text(encoding="utf-8"), encoding="utf-8"
            )
            (dest / "requirements-consumer-ci.txt").write_text(
                "ruff\nmypy==2.3.0\npytest==9.1.1\n", encoding="utf-8"
            )
            proc = subprocess.run(
                ["bash", str(dest / "install.sh")],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertIn("unpinned", proc.stderr)


if __name__ == "__main__":
    unittest.main()
