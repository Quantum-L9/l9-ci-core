"""The toolchain preflight must be able to fail.

A version check is easy to write so that it can only ever agree with itself:
read the pin, read the pin again, compare. That test passes forever and
discriminates nothing. These tests therefore lead with the negative cases —
a staged mismatch, an absent distribution, an unreadable lock — and assert
that each one is refused and *named*. The positive case is last, because on
its own it would prove nothing.

The property under test is the one the gate depends on: that what
``@python -m ruff`` and ``@python -m mypy`` will *import* is the pinned
distribution. Matching metadata is not sufficient evidence of that — ``-m``
searches a different ``sys.path`` than this script does — so the shadowing
case has its own test.
"""

from __future__ import annotations

import importlib.metadata
import contextlib
import json
import pathlib
import sys
import tempfile
import unittest
import unittest.mock

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


@contextlib.contextmanager
def _interpreter_reporting(versions: dict[str, str]):
    """Pretend the running interpreter resolves exactly ``versions``.

    Most of these cases are about the comparison, not about this machine.
    Reading the real installed ruff/mypy to build a *matching* lock made them
    depend on the lint toolchain being present, which the stdlib-only unit-test
    job deliberately does not install — so they errored there while passing
    locally. Faking the reading keeps each case testing the logic it names.
    """

    def fake_version(name: str) -> str:
        try:
            return versions[name]
        except KeyError:
            raise importlib.metadata.PackageNotFoundError(name) from None

    with unittest.mock.patch.object(importlib.metadata, "version", fake_version):
        yield


def _toolchain_installed() -> bool:
    try:
        for name in GATE_DISTRIBUTIONS:
            importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return False
    return True


# A real ruff/mypy must be importable for the two cases below to mean anything.
# The stdlib-only unit-test job installs neither; the lint job and `make check`
# both do, and `make check` runs this very module as its first command, so the
# property stays enforced unconditionally there. Remove this guard if the
# unit-test job ever installs requirements-repo-runtime.txt.
REQUIRES_REAL_TOOLCHAIN = unittest.skipUnless(
    _toolchain_installed(),
    "ruff/mypy are not installed in this interpreter; "
    "`make check` enforces this property where they are",
)


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
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            _write_lock(root, {"ruff": "1.2.3", "mypy": "0.0.2-wrong"})

            with _interpreter_reporting({"ruff": "1.2.3", "mypy": "9.9.9"}):
                violations, _ = check(root)
                self.assertEqual(2, main(["--root", str(root)]))

            self.assertEqual(1, len(violations), violations)
            self.assertIn("mypy", violations[0])

    def test_near_miss_version_is_refused(self) -> None:
        """A longer string that merely starts with the pin is not the pin.

        Guards the comparison against a substring or prefix match, which would
        accept 0.16.10 for a 0.16.1 pin.
        """
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            _write_lock(root, {"ruff": "0.16.10", "mypy": "2.3.0"})

            with _interpreter_reporting({"ruff": "0.16.1", "mypy": "2.3.0"}):
                violations, _ = check(root)

            self.assertTrue(
                any("ruff" in violation for violation in violations),
                "0.16.1 must not satisfy a pin of 0.16.10",
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

            with unittest.mock.patch.object(
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
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            _write_lock(root, {name: "1.2.3" for name in GATE_DISTRIBUTIONS})
            with unittest.mock.patch.object(
                check_toolchain_versions, "GATE_DISTRIBUTIONS", ()
            ):
                with self.assertRaisesRegex(ToolchainError, "asserts nothing"):
                    check(root)
                self.assertEqual(3, main(["--root", str(root)]))

    @REQUIRES_REAL_TOOLCHAIN
    def test_module_shadowed_under_the_gate_search_path_is_refused(self) -> None:
        """Metadata alone proves what is installed, not what `-m` imports.

        The gate runs `@python -m <tool>` with the repository root at the front
        of sys.path; this check runs as a script from tools/, a different
        search path. A plain `ruff/` package at the root therefore wins for the
        gate while distribution metadata still reports the pinned version.

        That is this check's own defect class — a shadow silently changing what
        runs — relocated from PATH to sys.path. Before the resolution step was
        added, this exact layout reported `ok ruff 0.16.1` and exited 0 while
        `-m ruff` executed the shadow.
        """
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            _write_lock(
                root,
                {name: importlib.metadata.version(name) for name in GATE_DISTRIBUTIONS},
            )
            shadow = root / "ruff"
            shadow.mkdir()
            (shadow / "__init__.py").write_text("", encoding="utf-8")

            violations, _ = check(root)

            self.assertTrue(violations, "a shadowing module must not pass")
            joined = "\n".join(violations)
            self.assertIn("-m ruff", joined)
            self.assertIn(str(shadow), joined)
            self.assertEqual(2, main(["--root", str(root)]))

    @REQUIRES_REAL_TOOLCHAIN
    def test_unshadowed_tree_is_not_falsely_refused(self) -> None:
        """The shadow check must not fire on a normal repository."""
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            _write_lock(
                root,
                {name: importlib.metadata.version(name) for name in GATE_DISTRIBUTIONS},
            )
            violations, _ = check(root)
            self.assertEqual([], violations)

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
        pins = {name: "1.2.3" for name in GATE_DISTRIBUTIONS}
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            _write_lock(root, pins)

            with _interpreter_reporting(pins):
                violations, report = check(root)
                self.assertEqual(0, main(["--root", str(root)]))

            self.assertEqual([], violations)
            self.assertTrue(all(entry["conforming"] for entry in report))

    @REQUIRES_REAL_TOOLCHAIN
    def test_shipped_tree_conforms(self) -> None:
        """The real repository must satisfy its own gate.

        This is what `make check` runs first, so a failure here means the
        interpreter running the suite is not the one the pins describe.
        """
        violations, _ = check(ROOT)
        self.assertEqual([], violations, "\n".join(violations))

    @REQUIRES_REAL_TOOLCHAIN
    def test_reported_versions_are_what_the_gate_will_import(self) -> None:
        """The check and the gate must resolve identically.

        Both run under the same interpreter, so `-m ruff --version` must agree
        with what this process reports. If these could differ, the preflight
        would be checking a different toolchain than the one being gated —
        the exact tautology this module was written to avoid.
        """
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
