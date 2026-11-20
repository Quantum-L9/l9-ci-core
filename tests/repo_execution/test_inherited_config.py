"""Inherited configuration: resolved by Core at runtime, bound to a digest.

A stamped repository may declare tool configuration as inherited
(``l9.core-stamp/v2``). It then carries no copy, a Core preset change is not
drift there, and a local file that would change version, mode or ignore scope
is refused with both digests. ``L9-CI-CORE-002``.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import unittest
import unittest.mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from l9_repo import inherited_config, stamp as stamp_module  # noqa: E402
from l9_repo.__main__ import WorkflowError, main  # noqa: E402
from l9_repo.contract import ContractError, RepositoryExecution  # noqa: E402
from l9_repo.stamp import (  # noqa: E402
    NEVER_INHERITED,
    PROFILE_FILES,
    STAMP_RECORD_PATH,
    STAMP_SCHEMA,
    STAMP_SCHEMA_V2,
    inheritable_files,
    load_stamp_record,
    render_file,
    stamp,
    verify_stamp,
)

BRIDGE = ROOT / ".github" / "actions" / "run-repository-verification" / "run.py"
bridge_spec = importlib.util.spec_from_file_location("l9_bridge_inherited", BRIDGE)
assert bridge_spec and bridge_spec.loader
bridge = importlib.util.module_from_spec(bridge_spec)
bridge_spec.loader.exec_module(bridge)

REVISION = "0123456789abcdef0123456789abcdef01234567"
PRODUCT_FILES = {
    "pyproject.toml": b'[project]\nname = "product"\n',
    "Repo.mk": (
        b".PHONY: repo-setup repo-validate repo-check repo-test\n"
        b"repo-setup repo-validate repo-check repo-test:\n\t@true\n"
    ),
    "package.json": b'{"name": "product"}\n',
}
PROFILE = "python+typescript"


class InheritedConfigCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        for relative, data in PRODUCT_FILES.items():
            (self.root / relative).write_bytes(data)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def record(self) -> dict[str, object]:
        return json.loads((self.root / STAMP_RECORD_PATH).read_text(encoding="utf-8"))

    def write_record(self, record: dict[str, object]) -> None:
        (self.root / STAMP_RECORD_PATH).write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8"
        )


class StampInheritanceTests(InheritedConfigCase):
    def test_contract_and_facade_are_never_inheritable(self) -> None:
        self.assertEqual({".l9/repo-workflow.json", "Makefile"}, set(NEVER_INHERITED))
        for profile, files in PROFILE_FILES.items():
            with self.subTest(profile=profile):
                self.assertEqual(
                    [f for f in files if f not in NEVER_INHERITED],
                    list(inheritable_files(profile)),
                )
        for relative in sorted(NEVER_INHERITED):
            with self.subTest(path=relative), self.assertRaises(ContractError):
                stamp(self.root, PROFILE, REVISION, [relative])

    def test_known_good_repository_without_copied_configs_validates(self) -> None:
        """A repository carrying no central tool config is fully admitted."""
        changed = stamp(self.root, PROFILE, REVISION, ["all"])
        inherited = set(inheritable_files(PROFILE))
        for relative in inherited:
            self.assertFalse((self.root / relative).exists(), relative)
        self.assertEqual(
            {".l9/repo-workflow.json", "Makefile", STAMP_RECORD_PATH.as_posix()},
            set(changed),
        )
        record = self.record()
        self.assertEqual(STAMP_SCHEMA_V2, record["schema"])
        self.assertEqual(sorted(inherited), record["inherited"])
        self.assertEqual(sorted(NEVER_INHERITED), sorted(record["files"]))
        RepositoryExecution(self.root).validate()
        self.assertEqual([], verify_stamp(self.root)[1])
        self.assertEqual([], stamp(self.root, PROFILE, REVISION, ["all"]))

    def test_stamp_without_inheritance_still_writes_a_v1_record(self) -> None:
        stamp(self.root, "python", REVISION)
        self.assertEqual(STAMP_SCHEMA, self.record()["schema"])
        self.assertNotIn("inherited", self.record())
        self.assertIsNone(inherited_config.resolve(self.root))

    def test_partial_inheritance_keeps_the_rest_byte_checked(self) -> None:
        stamp(self.root, "python", REVISION, ["ruff.toml", "mypy.ini"])
        self.assertFalse((self.root / "ruff.toml").exists())
        self.assertTrue((self.root / "pytest.ini").is_file())
        self.assertEqual([], verify_stamp(self.root)[1])
        (self.root / "pytest.ini").write_bytes(b"[pytest]\naddopts = -x\n")
        findings = verify_stamp(self.root)[1]
        self.assertEqual(1, len(findings))
        self.assertIn("pytest.ini: differs from Core-owned bytes", findings[0])

    def test_known_good_core_preset_change_is_not_drift(self) -> None:
        """The self-triggered failure this removes: Core moves, nobody re-stamps."""
        stamp(self.root, "python", REVISION, ["all"])
        original = stamp_module.render_file

        def moved(relative: str) -> bytes:
            data = original(relative)
            return data + b"# changed centrally\n" if relative == "ruff.toml" else data

        before = inherited_config.resolve(self.root)
        with unittest.mock.patch.object(stamp_module, "render_file", moved):
            with unittest.mock.patch.object(inherited_config, "render_file", moved):
                self.assertEqual([], verify_stamp(self.root)[1])
                after = inherited_config.resolve(self.root)
        assert before is not None and after is not None
        self.assertNotEqual(before.digests["ruff.toml"], after.digests["ruff.toml"])
        self.assertNotEqual(before.configuration_digest, after.configuration_digest)

    def test_known_bad_the_same_change_is_drift_for_a_carried_copy(self) -> None:
        """Contrast: a v1 repository that carries the file does drift."""
        stamp(self.root, "python", REVISION)
        original = stamp_module.render_file

        def moved(relative: str) -> bytes:
            data = original(relative)
            return data + b"# changed centrally\n" if relative == "ruff.toml" else data

        with unittest.mock.patch.object(stamp_module, "render_file", moved):
            findings = verify_stamp(self.root)[1]
        self.assertEqual(1, len(findings))
        self.assertIn("ruff.toml: differs from Core-owned bytes", findings[0])

    def test_known_bad_consumer_override_is_refused_with_both_digests(self) -> None:
        overrides = {
            "version (requirements-ci.txt)": ("requirements-ci.txt", b"ruff==0.0.1\n"),
            "version (uv.toml)": ("uv.toml", b'required-version = "==0.1.0"\n'),
            "version (.python-version)": (".python-version", b"3.9.0\n"),
            "mode (mypy.ini)": ("mypy.ini", b"[mypy]\nstrict = False\n"),
            "mode (ruff.toml)": ("ruff.toml", b"[lint]\nselect = []\n"),
            "ignore scope (.biomeignore)": (".biomeignore", b"**\n"),
            "ignore scope (.gitleaks.toml)": (
                ".gitleaks.toml",
                b'[allowlist]\npaths = [".*"]\n',
            ),
            "ignore scope (ruff.toml)": ("ruff.toml", b'exclude = ["**"]\n'),
        }
        for name, (relative, data) in overrides.items():
            with self.subTest(override=name), tempfile.TemporaryDirectory() as tmp:
                root = pathlib.Path(tmp)
                for product, content in PRODUCT_FILES.items():
                    (root / product).write_bytes(content)
                stamp(root, PROFILE, REVISION, ["all"])
                (root / relative).write_bytes(data)
                findings = verify_stamp(root)[1]
                self.assertEqual(1, len(findings), findings)
                self.assertIn(f"{relative}: local copy overrides", findings[0])
                central = inherited_config._digest(render_file(relative))
                local = inherited_config._digest(data)
                self.assertIn(f"central {central}", findings[0])
                self.assertIn(f"local {local}", findings[0])
                resolution = inherited_config.resolve(root)
                assert resolution is not None
                self.assertEqual(central, resolution.digests[relative])
                with self.assertRaises(ContractError):
                    inherited_config.materialize(root, resolution)
                self.assertEqual(data, (root / relative).read_bytes())

    def test_identical_local_copy_is_accepted_and_symlink_is_not(self) -> None:
        stamp(self.root, "python", REVISION, ["all"])
        (self.root / "ruff.toml").write_bytes(render_file("ruff.toml"))
        self.assertEqual([], verify_stamp(self.root)[1])
        (self.root / "mypy.ini").symlink_to(self.root / "pyproject.toml")
        findings = verify_stamp(self.root)[1]
        self.assertEqual(1, len(findings))
        self.assertIn("mypy.ini: inherited from Core but is a symlink", findings[0])

    def test_known_bad_malformed_inheritance_claims_fail_closed(self) -> None:
        stamp(self.root, "python", REVISION, ["ruff.toml"])
        good = self.record()

        def changed(**changes: object) -> dict[str, object]:
            return {**good, **changes}

        files = dict(good["files"])  # type: ignore[call-overload]
        cases: dict[str, dict[str, object]] = {
            "v1 schema with an inherited key": changed(schema=STAMP_SCHEMA),
            "v2 schema without inherited": {
                key: value for key, value in good.items() if key != "inherited"
            },
            "empty inherited": changed(inherited=[]),
            "inherited not a list": changed(inherited="ruff.toml"),
            "duplicate inherited": changed(inherited=["ruff.toml", "ruff.toml"]),
            "inherits the contract": changed(inherited=[".l9/repo-workflow.json"]),
            "inherits the facade": changed(inherited=["Makefile"]),
            "inherits another profile's file": changed(inherited=["biome.json"]),
            "inherits a product file": changed(inherited=["pyproject.toml"]),
            "carries and inherits one file": changed(
                files={**files, "ruff.toml": "sha256:" + "0" * 64}
            ),
            "unknown schema": changed(schema="l9.core-stamp/v3"),
        }
        for name, record in cases.items():
            with self.subTest(case=name):
                self.write_record(record)
                with self.assertRaises(ContractError):
                    load_stamp_record(self.root)

    def test_profile_file_neither_carried_nor_inherited_is_still_drift(self) -> None:
        stamp(self.root, "python", REVISION, ["ruff.toml"])
        record = self.record()
        del record["files"]["mypy.ini"]  # type: ignore[attr-defined]
        self.write_record(record)
        findings = verify_stamp(self.root)[1]
        self.assertEqual(1, len(findings))
        self.assertIn("mypy.ini", findings[0])
        self.assertIn("absent from the stamp record", findings[0])


class ResolutionTests(InheritedConfigCase):
    def test_resolution_is_core_bytes_with_a_stable_digest(self) -> None:
        stamp(self.root, PROFILE, REVISION, ["all"])
        first = inherited_config.resolve(self.root)
        second = inherited_config.resolve(self.root)
        assert first is not None and second is not None
        self.assertEqual(set(inheritable_files(PROFILE)), set(first.files))
        for relative, data in first.files.items():
            self.assertEqual(render_file(relative), data)
        self.assertEqual(first.configuration_digest, second.configuration_digest)
        self.assertRegex(first.configuration_digest, r"^sha256:[0-9a-f]{64}$")
        evidence = first.evidence(REVISION)
        self.assertEqual("l9.core-inherited-config/v1", evidence["schema"])
        self.assertEqual(REVISION, evidence["core"]["revision"])
        self.assertEqual(first.digests, evidence["files"])

    def test_digest_covers_the_set_not_only_the_bytes(self) -> None:
        stamp(self.root, "python", REVISION, ["ruff.toml", "mypy.ini"])
        both = inherited_config.resolve(self.root)
        stamp(self.root, "python", REVISION, ["ruff.toml"])
        one = inherited_config.resolve(self.root)
        assert both is not None and one is not None
        self.assertNotEqual(both.configuration_digest, one.configuration_digest)

    def test_materialize_writes_absent_files_once(self) -> None:
        stamp(self.root, "python", REVISION, ["all"])
        resolution = inherited_config.resolve(self.root)
        assert resolution is not None
        written = inherited_config.materialize(self.root, resolution)
        self.assertEqual(sorted(inheritable_files("python")), written)
        for relative in written:
            self.assertEqual(render_file(relative), (self.root / relative).read_bytes())
        self.assertEqual([], inherited_config.materialize(self.root, resolution))
        self.assertEqual([], verify_stamp(self.root)[1])

    def test_unstamped_repository_resolves_nothing(self) -> None:
        self.assertIsNone(inherited_config.resolve(self.root))


class CliAndBridgeTests(InheritedConfigCase):
    def run_cli(self, *arguments: str) -> tuple[int | None, str]:
        stream = io.StringIO()
        code: int | None = None
        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
            try:
                code = main(["--workspace", str(self.root), *arguments])
            except (WorkflowError, ContractError, SystemExit) as error:
                stream.write(str(error))
        return code, stream.getvalue()

    def test_cli_materialize_and_validate_report_the_digest(self) -> None:
        stamp(self.root, "python", REVISION, ["all"])
        resolution = inherited_config.resolve(self.root)
        assert resolution is not None
        code, text = self.run_cli("validate")
        self.assertEqual(0, code, text)
        self.assertIn(resolution.configuration_digest, text)
        code, text = self.run_cli("materialize")
        self.assertEqual(0, code, text)
        self.assertIn("materialized ruff.toml", text)
        self.assertIn(resolution.configuration_digest, text)
        self.assertTrue((self.root / "ruff.toml").is_file())

    def test_cli_refuses_an_override_and_an_unstamped_repository(self) -> None:
        code, text = self.run_cli("materialize")
        self.assertNotEqual(0, code)
        self.assertIn("requires a stamp record", text)
        stamp(self.root, "python", REVISION, ["all"])
        (self.root / "ruff.toml").write_bytes(b"[lint]\nselect = []\n")
        for command in ("validate", "materialize"):
            with self.subTest(command=command):
                code, text = self.run_cli(command)
                self.assertNotEqual(0, code)
                self.assertIn("ruff.toml: local copy overrides", text)

    def test_bridge_admits_reports_and_materializes_before_phases(self) -> None:
        stamp(self.root, "python", REVISION, ["all"])
        resolution = inherited_config.resolve(self.root)
        assert resolution is not None
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            bridge.verify_v2(self.root, "required")
        self.assertIn("Core stamp: aligned", stream.getvalue())
        self.assertIn(resolution.configuration_digest, stream.getvalue())
        self.assertFalse((self.root / "ruff.toml").exists(), "verify must not write")

        seen: list[bool] = []

        def fake_run(argv: list[str], **_: object) -> None:
            seen.append((self.root / "ruff.toml").is_file())

        with unittest.mock.patch.object(bridge.subprocess, "run", fake_run):
            with contextlib.redirect_stdout(io.StringIO()):
                bridge.run_v2(self.root)
        self.assertEqual([True, True, True, True], seen)

    def test_bridge_refuses_a_consumer_override_in_every_mode(self) -> None:
        stamp(self.root, "python", REVISION, ["all"])
        (self.root / "mypy.ini").write_bytes(b"[mypy]\nstrict = False\n")
        for mode in ("migration", "required"):
            with self.subTest(mode=mode), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(ContractError) as raised:
                    bridge.verify_v2(self.root, mode)
                self.assertIn("mypy.ini: local copy overrides", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
