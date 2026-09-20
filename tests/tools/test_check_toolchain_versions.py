"""The toolchain preflight must be able to fail.

A version check is easy to write so that it can only ever agree with itself:
read the pin, read the pin again, compare. That test passes forever and
discriminates nothing. These tests therefore lead with the negative cases —
a staged mismatch, an absent distribution, an unreadable lock — and assert
that each one is refused and *named*. The positive case is last, because on
its own it would prove nothing.

The property under test is the one the gate depends on: the versions reported
here are the versions ``@python -m ruff`` and ``@python -m mypy`` will import,
because both readings happen in this same interpreter.
"""

from __future__ import annotations

import importlib.metadata
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import check_toolchain_versions  # noqa: E402
from check_toolchain_versions import (  # noqa: E402
    GATE_DISTRIBUTIONS,
    LOCK,
    ToolchainError,
    check,
    load_lock,
    main,
)


def _write_lock(root: pathlib.Path, pins: dict[str, str]) -> pathlib.Path:
    path = root / LOCK
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pins), encoding="utf-8")
    return path


class ToolchainMismatchTests(unittest.TestCase):
    """The cases that must fail. These are the reason the check exists."""

    def test_staged_mismatch_is_refused_and_names_the_tool(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            _write_lock(root, {"ruff": "0.0.1-not-installed", "mypy": "0.0.2"})

            violations, report = check(root)

            self.assertTrue(violations, "a mismatched lock must not pass")
            joined = "\n".join(violations)
            self.assertIn("ruff", joined)
            self.assertIn("0.0.1-not-installed", joined)
            self.assertFalse(any(entry["conforming"] for entry in report))
            self.assertEqual(2, main(["--root", str(root)]))

    def test_partial_mismatch_still_fails(self) -> None:
        """One correct tool must not mask a wrong one."""
        import importlib.metadata

        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            _write_lock(
                root,
                {
                    "ruff": importlib.metadata.version("ruff"),
                    "mypy": "0.0.2-wrong",
                },
            )

            violations, _ = check(root)

            self.assertEqual(1, len(violations), violations)
            self.assertIn("mypy", violations[0])
            self.assertEqual(2, main(["--root", str(root)]))

    def test_near_miss_version_is_refused(self) -> None:
        """A longer string that merely starts with the pin is not the pin.

        Guards the comparison against a substring or prefix match, which would
        accept 0.16.10 for a 0.16.1 pin.
        """
        import importlib.metadata

        installed = importlib.metadata.version("ruff")
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            _write_lock(root, {"ruff": f"{installed}0", "mypy": "0.0.2"})

            violations, _ = check(root)

            self.assertTrue(
                any("ruff" in violation for violation in violations),
                f"{installed}0 must not satisfy a pin of {installed}",
            )

    def test_distribution_absent_from_this_interpreter_is_refused(self) -> None:
        """Not installed is a violation, never a skip.

        Uses a distribution that genuinely is not installed. Staging a wrong
        *version* for an installed tool would exercise the mismatch path again
        and leave the absence path unproven.
        """
        absent = "l9-not-a-real-distribution"
        with self.assertRaises(importlib.metadata.PackageNotFoundError):
            importlib.metadata.version(absent)

        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            _write_lock(root, {absent: "1.0.0"})

            with mock.patch.object(
                check_toolchain_versions, "GATE_DISTRIBUTIONS", (absent,)
            ):
                violations, report = check(root)
                self.assertEqual(2, main(["--root", str(root)]))

            self.assertEqual(1, len(violations), violations)
            self.assertIn(absent, violations[0])
            self.assertIn("not installed", violations[0])
            self.assertIsNone(report[0]["observed"])
            self.assertFalse(report[0]["conforming"])

    def test_asserting_nothing_is_not_a_passing_check(self) -> None:
        """A check with an empty subject list must refuse, not pass vacuously.

        Found by probing the fail-closed claim rather than by reading the
        code: with no distributions declared, `check()` returned no violations
        and `main()` exited 0, so the gate would have kept reporting success
        while verifying nothing at all.
        """
        import importlib.metadata

        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            _write_lock(
                root,
                {name: importlib.metadata.version(name) for name in GATE_DISTRIBUTIONS},
            )
            with mock.patch.object(check_toolchain_versions, "GATE_DISTRIBUTIONS", ()):
                with self.assertRaisesRegex(ToolchainError, "asserts nothing"):
                    check(root)
                self.assertEqual(3, main(["--root", str(root)]))

    def test_missing_lock_is_exit_three_not_a_pass(self) -> None:
        """An undeterminable expectation is blocking, not permissive."""
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            with self.assertRaises(ToolchainError):
                load_lock(root)
            self.assertEqual(3, main(["--root", str(root)]))

    def test_lock_without_a_gate_tool_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            _write_lock(root, {"ruff": "0.16.1"})
            with self.assertRaisesRegex(ToolchainError, "mypy"):
                load_lock(root)
            self.assertEqual(3, main(["--root", str(root)]))

    def test_unreadable_lock_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            path = root / LOCK
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ToolchainError):
                load_lock(root)
            self.assertEqual(3, main(["--root", str(root)]))


class ToolchainConformanceTests(unittest.TestCase):
    """The positive cases, meaningful only because the negatives above fail."""

    def test_matching_lock_passes(self) -> None:
        import importlib.metadata

        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            _write_lock(
                root,
                {name: importlib.metadata.version(name) for name in GATE_DISTRIBUTIONS},
            )

            violations, report = check(root)

            self.assertEqual([], violations)
            self.assertTrue(all(entry["conforming"] for entry in report))
            self.assertEqual(0, main(["--root", str(root)]))

    def test_shipped_tree_conforms(self) -> None:
        """The real repository must satisfy its own gate.

        This is what `make check` runs first, so a failure here means the
        interpreter running the suite is not the one the pins describe.
        """
        violations, _ = check(ROOT)
        self.assertEqual([], violations, "\n".join(violations))

    def test_reported_versions_are_what_the_gate_will_import(self) -> None:
        """The check and the gate must resolve identically.

        Both run under the same interpreter, so `-m ruff --version` must agree
        with what this process reports. If these could differ, the preflight
        would be checking a different toolchain than the one being gated —
        the exact tautology this module was written to avoid.
        """
        import importlib.metadata
        import subprocess

        for name in GATE_DISTRIBUTIONS:
            with self.subTest(distribution=name):
                result = subprocess.run(
                    [sys.executable, "-m", name, "--version"],
                    check=True,
                    capture_output=True,
                    text=True,
                    cwd=ROOT,
                )
                self.assertIn(
                    importlib.metadata.version(name),
                    result.stdout.strip(),
                    f"`-m {name}` reports a different version than "
                    "importlib.metadata sees in the same interpreter",
                )


if __name__ == "__main__":
    unittest.main()
