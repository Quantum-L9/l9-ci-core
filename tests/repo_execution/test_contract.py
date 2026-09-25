"""The V2 repository-execution contract is exactly three declarative fields."""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from v2_fixtures import ROOT, v2_contract  # noqa: E402

from l9_repo.contract import (  # noqa: E402
    FACADE_TEMPLATES,
    PHASES,
    ContractError,
    load_contract,
    validate_v2_contract_data,
)

BRIDGE = ROOT / ".github" / "actions" / "run-repository-verification" / "run.py"


class ContractShapeTests(unittest.TestCase):
    def test_exact_three_field_declaration_is_accepted(self) -> None:
        document = v2_contract()
        self.assertIs(document, validate_v2_contract_data(document))

    def test_additional_fields_fail(self) -> None:
        for extra in ("commands", "schema_version", "$schema", "metadata"):
            with self.subTest(extra=extra):
                with self.assertRaisesRegex(ContractError, "unsupported keys"):
                    validate_v2_contract_data(v2_contract(**{extra: 1}))

    def test_missing_fields_fail(self) -> None:
        for missing in ("schema", "facade", "required_phases"):
            with self.subTest(missing=missing):
                document = v2_contract()
                del document[missing]
                with self.assertRaisesRegex(ContractError, "missing keys"):
                    validate_v2_contract_data(document)

    def test_wrong_schema_fails(self) -> None:
        for schema in ("l9.repo-execution/v1", "l9.repo-execution/v3", "", 2):
            with self.subTest(schema=schema):
                with self.assertRaisesRegex(ContractError, "schema must be"):
                    validate_v2_contract_data(v2_contract(schema=schema))

    def test_wrong_or_unsupported_facade_fails(self) -> None:
        for facade in ("make-v2", "MAKE-V1", "", None, ["make-v1"]):
            with self.subTest(facade=facade):
                with self.assertRaisesRegex(ContractError, "unsupported facade"):
                    validate_v2_contract_data(v2_contract(facade=facade))

    def test_wrong_phase_list_fails(self) -> None:
        cases: dict[str, object] = {
            "missing phase": ["setup", "validate", "check"],
            "extra phase": [*PHASES, "lint"],
            "duplicate phase": [*PHASES, "test"],
            "reordered": ["setup", "check", "validate", "test"],
            "not a list": "setup validate check test",
            "tuple-like object": {"0": "setup"},
        }
        for label, phases in cases.items():
            with self.subTest(case=label):
                with self.assertRaisesRegex(ContractError, "required_phases"):
                    validate_v2_contract_data(v2_contract(required_phases=phases))

    def test_non_object_root_fails(self) -> None:
        for document in ([], "make-v1", None, 3):
            with self.subTest(document=document):
                with self.assertRaises(ContractError):
                    validate_v2_contract_data(document)

    def test_released_facades_are_an_explicit_registry(self) -> None:
        self.assertEqual({"make-v1"}, set(FACADE_TEMPLATES))
        for facade, template in FACADE_TEMPLATES.items():
            with self.subTest(facade=facade):
                self.assertTrue(template.is_file(), template)
                self.assertEqual(f"{facade}.mk", template.name)


class ContractFileTests(unittest.TestCase):
    def test_load_contract_rejects_missing_broken_and_non_object_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            path = root / "repo-workflow.json"
            with self.assertRaisesRegex(ContractError, "missing"):
                load_contract(path)
            for text in ("{", "[]", '"make-v1"'):
                with self.subTest(text=text):
                    path.write_text(text, encoding="utf-8")
                    with self.assertRaises(ContractError):
                        load_contract(path)
            path.write_text(json.dumps(v2_contract()), encoding="utf-8")
            self.assertEqual(v2_contract(), load_contract(path))


class BridgeAgreementTests(unittest.TestCase):
    """The admission bridge and the runtime must accept the same documents."""

    def bridge(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("l9_bridge_for_contract", BRIDGE)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_bridge_constants_match_the_runtime(self) -> None:
        bridge = self.bridge()
        self.assertEqual(tuple(PHASES), bridge.V2_REQUIRED_PHASES)
        self.assertEqual("make-v1", bridge.V2_FACADE)
        self.assertIn(bridge.V2_FACADE, FACADE_TEMPLATES)

    def test_bridge_and_runtime_agree_on_rejections(self) -> None:
        bridge = self.bridge()
        rejected: list[dict[str, object]] = [
            v2_contract(commands={}),
            v2_contract(facade="make-v2"),
            v2_contract(required_phases=["setup", "check", "validate", "test"]),
            v2_contract(schema="l9.repo-execution/v3"),
        ]
        for document in rejected:
            with self.subTest(document=document):
                with self.assertRaises(ContractError):
                    validate_v2_contract_data(document)
                with self.assertRaises(bridge.ContractError):
                    bridge.classify_contract(document)
        self.assertEqual("v2", bridge.classify_contract(v2_contract()))


if __name__ == "__main__":
    unittest.main()
