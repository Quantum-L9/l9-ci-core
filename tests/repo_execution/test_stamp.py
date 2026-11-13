"""``tools.l9_repo stamp`` / ``validate``: Core-owned consumer files.

A stamp writes every Core-owned file for a profile plus the stamp record, is
idempotent, never touches a product-owned file, and ``validate`` proves each
stamped file byte-identical to this Core checkout.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from l9_repo.__main__ import (  # noqa: E402
    WorkflowError,
    core_stamp_revision,
    main,
    validate_stamp,
)
from l9_repo.contract import (  # noqa: E402
    ContractError,
    RepositoryExecution,
    render_facade,
    validate_v2_contract_data,
)
from l9_repo.stamp import (  # noqa: E402
    PROFILE_FILES,
    PROFILES,
    REQUIREMENTS_TOOLS,
    STAMP_RECORD_PATH,
    load_stamp_record,
    render_file,
    stamp,
    verify_stamp,
)

REVISION = "0123456789abcdef0123456789abcdef01234567"
LOCK = ROOT / ".github" / "actions" / "install-consumer-ci" / "toolchain-lock.json"
CONSUMER_REQUIREMENTS = (
    ROOT
    / ".github"
    / "actions"
    / "install-consumer-ci"
    / "requirements-consumer-ci.txt"
)
PRODUCT_FILES = {
    "pyproject.toml": b'[project]\nname = "product"\n\n[tool.ruff]\nline-length = 79\n',
    "Repo.mk": (
        b".PHONY: repo-setup repo-validate repo-check repo-test\n"
        b"repo-setup repo-validate repo-check repo-test:\n\t@true\n"
    ),
    "package.json": b'{"name": "product"}\n',
    "src/product/__init__.py": b"",
}


def _snapshot(root: pathlib.Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class StampTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        for relative, data in PRODUCT_FILES.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_profile_file_sets_are_exact(self) -> None:
        common = [".l9/repo-workflow.json", "Makefile"]
        python = ["requirements-ci.txt", "ruff.toml", "mypy.ini", "pytest.ini"]
        typescript = ["biome.json", ".biomeignore", ".editorconfig"]
        self.assertEqual(("python", "typescript", "python+typescript"), PROFILES)
        self.assertEqual(tuple(common + python), PROFILE_FILES["python"])
        self.assertEqual(tuple(common + typescript), PROFILE_FILES["typescript"])
        self.assertEqual(
            tuple(common + python + typescript), PROFILE_FILES["python+typescript"]
        )
        for files in PROFILE_FILES.values():
            self.assertNotIn("pyproject.toml", files)
            self.assertNotIn("Repo.mk", files)
            self.assertNotIn("package.json", files)

    def test_stamp_writes_profile_bytes_and_is_idempotent(self) -> None:
        for profile in PROFILES:
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as tmp:
                root = pathlib.Path(tmp)
                changed = stamp(root, profile, REVISION)
                self.assertEqual(
                    [*PROFILE_FILES[profile], STAMP_RECORD_PATH.as_posix()],
                    changed,
                )
                for relative in PROFILE_FILES[profile]:
                    self.assertEqual(
                        render_file(relative), (root / relative).read_bytes()
                    )
                before = _snapshot(root)
                self.assertEqual([], stamp(root, profile, REVISION))
                self.assertEqual(before, _snapshot(root))
                self.assertEqual([], verify_stamp(root)[1])

    def test_stamp_never_touches_product_owned_files(self) -> None:
        stamp(self.root, "python+typescript", REVISION)
        for relative, data in PRODUCT_FILES.items():
            self.assertEqual(data, (self.root / relative).read_bytes(), relative)

    def test_stamp_overwrites_an_edited_core_owned_file(self) -> None:
        (self.root / "ruff.toml").write_text("line-length = 79\n", encoding="utf-8")
        changed = stamp(self.root, "python", REVISION)
        self.assertIn("ruff.toml", changed)
        self.assertEqual(
            render_file("ruff.toml"), (self.root / "ruff.toml").read_bytes()
        )

    def test_stamp_record_names_revision_profile_and_digests(self) -> None:
        stamp(self.root, "python", REVISION)
        record = json.loads((self.root / STAMP_RECORD_PATH).read_text())
        self.assertEqual("l9.core-stamp/v1", record["schema"])
        self.assertEqual(
            {"repository": "Quantum-L9/l9-ci-core", "revision": REVISION},
            record["core"],
        )
        self.assertEqual("python", record["profile"])
        self.assertEqual(set(PROFILE_FILES["python"]), set(record["files"]))
        for relative, digest in record["files"].items():
            data = (self.root / relative).read_bytes()
            self.assertEqual("sha256:" + hashlib.sha256(data).hexdigest(), digest)
        self.assertEqual(record, load_stamp_record(self.root))

    def test_stamped_consumer_passes_full_validate(self) -> None:
        stamp(self.root, "python", REVISION)
        RepositoryExecution(self.root).validate()
        validate_stamp(self.root)

    def test_one_edited_byte_fails_validate(self) -> None:
        for relative in PROFILE_FILES["python+typescript"]:
            with self.subTest(file=relative), tempfile.TemporaryDirectory() as tmp:
                root = pathlib.Path(tmp)
                (root / "Repo.mk").write_bytes(PRODUCT_FILES["Repo.mk"])
                stamp(root, "python+typescript", REVISION)
                path = root / relative
                data = bytearray(path.read_bytes())
                data[-1] ^= 0x01
                path.write_bytes(bytes(data))
                _, findings = verify_stamp(root)
                self.assertEqual(1, len(findings))
                self.assertTrue(findings[0].startswith(f"{relative}:"))
                stderr = io.StringIO()
                with (
                    contextlib.redirect_stdout(io.StringIO()),
                    contextlib.redirect_stderr(stderr),
                ):
                    code = main(["--workspace", str(root), "validate"])
                self.assertEqual(2, code)
                self.assertIn(relative, stderr.getvalue())

    def test_missing_stamped_file_and_record_digest_mismatch_are_findings(self) -> None:
        stamp(self.root, "python", REVISION)
        (self.root / "pytest.ini").unlink()
        record_path = self.root / STAMP_RECORD_PATH
        record = json.loads(record_path.read_text())
        record["files"]["mypy.ini"] = "sha256:" + "0" * 64
        record_path.write_text(json.dumps(record), encoding="utf-8")
        findings = verify_stamp(self.root)[1]
        self.assertEqual(2, len(findings))
        self.assertTrue(
            any(item.startswith("pytest.ini: missing") for item in findings)
        )
        self.assertTrue(
            any(item.startswith("mypy.ini: stamp record") for item in findings)
        )

    def test_malformed_records_fail_closed(self) -> None:
        stamp(self.root, "python", REVISION)
        good = json.loads((self.root / STAMP_RECORD_PATH).read_text())
        cases = {
            "unknown key": {**good, "extra": 1},
            "wrong schema": {**good, "schema": "l9.core-stamp/v2"},
            "short revision": {**good, "core": {**good["core"], "revision": "abc"}},
            "foreign core": {**good, "core": {**good["core"], "repository": "x/y"}},
            "unknown profile": {**good, "profile": "go"},
            "file set mismatch": {**good, "profile": "typescript"},
            "bad digest": {**good, "files": {**good["files"], "ruff.toml": "md5:x"}},
        }
        for name, record in cases.items():
            with self.subTest(case=name):
                (self.root / STAMP_RECORD_PATH).write_text(json.dumps(record))
                with self.assertRaises(ContractError):
                    verify_stamp(self.root)

    def test_absent_record_verifies_nothing(self) -> None:
        self.assertEqual((None, []), verify_stamp(self.root))

    def test_stamp_refuses_short_revision_and_symlinked_destination(self) -> None:
        with self.assertRaises(ContractError):
            stamp(self.root, "python", "main")
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        (self.root / ".l9").symlink_to(outside.name)
        with self.assertRaises(ContractError):
            stamp(self.root, "python", REVISION)
        self.assertEqual([], list(pathlib.Path(outside.name).iterdir()))


class RenderedContentTests(unittest.TestCase):
    def test_contract_and_makefile_are_the_v2_contract_and_facade(self) -> None:
        validate_v2_contract_data(json.loads(render_file(".l9/repo-workflow.json")))
        self.assertEqual(render_facade("make-v1"), render_file("Makefile"))

    def test_requirements_pin_exactly_the_core_lock(self) -> None:
        lock = json.loads(LOCK.read_text(encoding="utf-8"))
        text = render_file("requirements-ci.txt").decode()
        pins = [line for line in text.splitlines() if line and not line.startswith("#")]
        self.assertEqual([f"{tool}=={lock[tool]}" for tool in REQUIREMENTS_TOOLS], pins)
        canonical = [
            line
            for line in CONSUMER_REQUIREMENTS.read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#")
        ]
        self.assertEqual(canonical, pins)

    def test_python_preset_is_strict_and_matches_the_locked_interpreter(self) -> None:
        lock = json.loads(LOCK.read_text(encoding="utf-8"))
        major_minor = ".".join(lock["python"].split(".")[:2])
        mypy = render_file("mypy.ini").decode()
        self.assertIn("[mypy]\n", mypy)
        self.assertIn("strict = True\n", mypy)
        self.assertIn(f"python_version = {major_minor}\n", mypy)
        ruff = render_file("ruff.toml").decode()
        self.assertIn(f'target-version = "py{major_minor.replace(".", "")}"\n', ruff)
        self.assertIn("[pytest]\n", render_file("pytest.ini").decode())


class CliTests(unittest.TestCase):
    def test_stamp_requires_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = main(["--workspace", tmp, "stamp"])
            self.assertEqual(2, code)
            self.assertIn("--profile", stderr.getvalue())
            self.assertEqual([], list(pathlib.Path(tmp).iterdir()))

    def test_core_revision_is_head_and_dirty_sources_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            core = pathlib.Path(tmp)

            def git(*args: str) -> str:
                return subprocess.run(
                    ["git", "-C", tmp, *args],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()

            git("init", "-q")
            git("config", "user.email", "test@example.invalid")
            git("config", "user.name", "test")
            (core / "presets").mkdir()
            (core / "presets" / "ruff.toml").write_text("x\n", encoding="utf-8")
            (core / "README").write_text("r\n", encoding="utf-8")
            git("add", "-A")
            git("commit", "-q", "-m", "init")
            self.assertEqual(git("rev-parse", "HEAD"), core_stamp_revision(core))
            (core / "README").write_text("unrelated edit\n", encoding="utf-8")
            self.assertEqual(git("rev-parse", "HEAD"), core_stamp_revision(core))
            (core / "presets" / "ruff.toml").write_text("y\n", encoding="utf-8")
            with self.assertRaises(WorkflowError):
                core_stamp_revision(core)


if __name__ == "__main__":
    unittest.main()
