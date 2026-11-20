"""Core holds itself to the alignment standard it reports on consumers.

``check-toolchain-alignment`` reports, for every governed repository, each
declaration of a lock-owned tool that differs from
``.github/actions/install-consumer-ci/toolchain-lock.json``. Running the same
scanner over Core's own tree turns that advisory consumer report into a
blocking self-check: any Core-internal pin of a lock-owned tool (pyproject,
``uv.lock``, ``requirements*.txt``, ``.pre-commit-config.yaml``, Biome,
``requires-python``, workflow interpreter, ``pip install``, setup-uv and
gitleaks literals) that differs from the lock fails here.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github" / "actions" / "check-toolchain-alignment" / "align.py"

# A Core declaration may name the locked version exactly, or (for the
# interpreter) the locked minor; nothing else.
ALIGNED = {"exact_match", "range_admits_lock"}


def load_align():
    spec = importlib.util.spec_from_file_location("core_align_self", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class CoreToolchainAlignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.align = load_align()
        lock, tools = cls.align.load_core()
        cls.lock = lock
        cls.findings = cls.align.scan(ROOT, lock, tools)

    def test_every_core_pin_of_a_lock_owned_tool_matches_the_lock(self) -> None:
        off = [
            "{path}:{line} {tool} {declared} vs lock {lock} ({kind})".format(
                path=item["path"],
                line=item["line"],
                tool=item["tool"],
                declared=item["declared"] or "(no version)",
                lock=item["lock"],
                kind=item["class"],
            )
            for item in self.findings
            if item["class"] not in ALIGNED
        ]
        self.assertEqual([], off)

    def test_the_scan_is_not_vacuous(self) -> None:
        # The self-check proves nothing if the scanner stops seeing Core.
        seen = {(item["path"], item["tool"]) for item in self.findings}
        for expected in (
            ("pyproject.toml", "python"),
            ("requirements-repo-runtime.txt", "ruff"),
            ("requirements-repo-runtime.txt", "mypy"),
            (".pre-commit-config.yaml", "ruff"),
            (".github/workflows/security.yml", "bandit"),
            (".github/workflows/security.yml", "pip-audit"),
            (".github/workflows/release-runner.yml", "uv"),
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, seen)

    def test_core_requires_the_locked_python_minor(self) -> None:
        [item] = [
            f
            for f in self.findings
            if f["path"] == "pyproject.toml" and f["tool"] == "python"
        ]
        self.assertEqual("range_admits_lock", item["class"])
        major, minor, _ = self.lock["python"].split(".")
        self.assertTrue(item["declared"].startswith(f">={major}.{minor}"))


if __name__ == "__main__":
    unittest.main()
