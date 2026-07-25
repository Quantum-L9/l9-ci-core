from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tools" / "regenerate_identity_maps.py"
spec = importlib.util.spec_from_file_location("regenerate_identity_maps", MODULE_PATH)
assert spec is not None and spec.loader is not None
regen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(regen)


def _base() -> dict:
    return {
        "schema": regen.IDENTITY_MAP_SCHEMA,
        "metadata": {"provider_id": "semgrep", "version": "3"},
        "rules": {
            "python.lang.rule-a": {
                "canonical_rule_id": "l9:semgrep:python.lang.rule-a"
            },
            "python.lang.curated": {"canonical_rule_id": "l9:custom:curated"},
        },
    }


class BuildUpdatedDocumentTests(unittest.TestCase):
    def test_new_rule_gets_default_mapping(self) -> None:
        document, added, removed = regen.build_updated_document(
            _base(), {"python.lang.rule-a", "python.lang.curated", "python.lang.new"}
        )
        self.assertEqual(["python.lang.new"], added)
        self.assertEqual([], removed)
        self.assertEqual(
            "l9:semgrep:python.lang.new",
            document["rules"]["python.lang.new"]["canonical_rule_id"],
        )

    def test_curated_override_is_never_clobbered(self) -> None:
        document, _added, _removed = regen.build_updated_document(
            _base(), {"python.lang.rule-a", "python.lang.curated"}
        )
        self.assertEqual(
            "l9:custom:curated",
            document["rules"]["python.lang.curated"]["canonical_rule_id"],
        )

    def test_upstream_removed_rule_is_kept_and_reported(self) -> None:
        document, added, removed = regen.build_updated_document(
            _base(), {"python.lang.rule-a"}
        )
        self.assertEqual([], added)
        self.assertEqual(["python.lang.curated"], removed)
        # Kept for historical resolution — present, not deleted.
        self.assertIn("python.lang.curated", document["rules"])

    def test_version_bumps_only_when_rules_added(self) -> None:
        added_doc, _a, _r = regen.build_updated_document(
            _base(), {"python.lang.rule-a", "python.lang.curated", "python.lang.new"}
        )
        self.assertEqual("4", added_doc["metadata"]["version"])
        noop_doc, _a2, _r2 = regen.build_updated_document(
            _base(), {"python.lang.rule-a", "python.lang.curated"}
        )
        self.assertEqual("3", noop_doc["metadata"]["version"])

    def test_render_is_deterministic_and_rules_sorted(self) -> None:
        document, _a, _r = regen.build_updated_document(
            _base(), {"python.lang.rule-a", "python.lang.curated", "aaa.first"}
        )
        rendered = regen.render(document)
        self.assertEqual(rendered, regen.render(document))
        self.assertTrue(rendered.endswith("\n"))
        self.assertEqual(sorted(document["rules"]), list(document["rules"]))

    def test_load_existing_returns_empty_scaffold_when_absent(self) -> None:
        result = regen.load_existing(ROOT / "does-not-exist.yaml")
        self.assertEqual(regen.IDENTITY_MAP_SCHEMA, result["schema"])
        self.assertEqual({}, result["rules"])


if __name__ == "__main__":
    unittest.main()
