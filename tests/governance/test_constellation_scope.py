"""Gate routing rules bind only declared Constellation nodes."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ACTION_ROOT = ROOT / ".github" / "actions" / "resolve-governance"
MODULE_PATH = ACTION_ROOT / "resolve.py"
ACTION_PATH = ACTION_ROOT / "action.yml"
DEFAULTS_ROOT = ACTION_ROOT / "defaults"
SCOPE_PATH = ACTION_ROOT / "constellation-node-rules.json"
BASE_POLICY_PATH = ACTION_ROOT / "semgrep-policy.yaml"
ROUTING_PREFIX = "l9.runtime.sdk.source.l9_ci.rulesets.semgrep.python.l9.routing."

spec = importlib.util.spec_from_file_location("resolve_governance_scope", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def run_main(extra: dict[str, str]) -> tuple[int, dict[str, str], Path]:
    temp = tempfile.mkdtemp()
    env = {
        "L9_PROFILE": "pr_fast",
        "L9_PROVIDER": "semgrep",
        "L9_EVENT_NAME": "pull_request",
        "L9_REPOSITORY": "Quantum-L9/example",
        "L9_REF": "refs/heads/main",
        "GITHUB_WORKSPACE": temp,
        **extra,
    }
    captured = io.StringIO()
    with unittest.mock.patch.dict(os.environ, env, clear=False):
        os.environ.pop("GITHUB_OUTPUT", None)
        if "L9_PRODUCT_KIND" not in extra:
            os.environ.pop("L9_PRODUCT_KIND", None)
        with contextlib.redirect_stdout(captured):
            code = module.main()
    outputs = dict(
        line.split("=", 1) for line in captured.getvalue().splitlines() if "=" in line
    )
    return code, outputs, Path(temp)


class ConstellationScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rules = module.load_constellation_rules()
        self.documents = module.load_documents(DEFAULTS_ROOT)
        self.bundled = module.select_policy(self.documents, "pr_fast", DEFAULTS_ROOT)
        assert self.bundled is not None

    def test_scope_document_names_exactly_the_sdk_routing_rules(self) -> None:
        self.assertEqual(4, len(self.rules["rules"]))
        for rule_id in self.rules["rules"]:
            self.assertTrue(rule_id.startswith(ROUTING_PREFIX), rule_id)
        self.assertEqual("node", self.rules["gate_bound"])
        self.assertEqual({"node", "dependency"}, set(self.rules["admitted"]))
        self.assertEqual("disabled", self.rules["mode"])

    def test_scope_document_pins_its_github_source_revision(self) -> None:
        document = json.loads(SCOPE_PATH.read_text(encoding="utf-8"))
        source = document["product_kinds"]["source"]
        self.assertEqual("Quantum-L9/.github", source["repository"])
        self.assertEqual("semantics/product_kinds.yaml", source["path"])
        self.assertEqual("l9.product-kinds/global@1", source["artifact_id"])
        self.assertRegex(source["commit"], r"^[0-9a-f]{40}$")

    def test_bundled_policy_leaves_routing_rules_blocking_by_default(self) -> None:
        base = json.loads(BASE_POLICY_PATH.read_text(encoding="utf-8"))
        self.assertEqual("blocking", base["defaults"]["mode"])
        for rule_id in self.rules["rules"]:
            self.assertNotIn(rule_id, base["rules"])

    def test_declared_kind_maps_to_scope(self) -> None:
        self.assertEqual("non-node", module.constellation_scope("", self.rules))
        self.assertEqual("node", module.constellation_scope("node", self.rules))
        self.assertEqual(
            "non-node", module.constellation_scope("dependency", self.rules)
        )

    def test_unadmitted_kind_fails_closed(self) -> None:
        for kind in ("sdk", "gate", "Node"):
            with self.subTest(kind=kind), self.assertRaises(module.GovernanceError):
                module.constellation_scope(kind, self.rules)

    def test_node_receives_the_bundled_policy_unchanged(self) -> None:
        self.assertEqual(
            self.bundled, module.scope_policy(self.bundled, "node", self.rules)
        )

    def test_non_node_policy_disables_only_the_routing_rules(self) -> None:
        scoped = module.scope_policy(self.bundled, "non-node", self.rules)
        assert scoped is not None
        self.assertEqual(self.bundled[0], scoped[0])
        base = json.loads(self.bundled[1])
        derived = json.loads(scoped[1])
        self.assertEqual(module.SDK_POLICY_SCHEMA, derived["schema"])
        self.assertEqual(base["schema"], derived["schema"])
        self.assertEqual(base["defaults"], derived["defaults"])
        self.assertEqual(base["metadata"]["version"], derived["metadata"]["version"])
        self.assertEqual("non-node", derived["metadata"]["constellation_scope"])
        changed = {
            rule_id
            for rule_id in set(base["rules"]) | set(derived["rules"])
            if base["rules"].get(rule_id) != derived["rules"].get(rule_id)
        }
        self.assertEqual(set(self.rules["rules"]), changed)
        for rule_id in self.rules["rules"]:
            self.assertEqual(
                {"policy_key": "routing.constellation-node-only", "mode": "disabled"},
                derived["rules"][rule_id],
            )

    def test_no_policy_stays_no_policy(self) -> None:
        self.assertIsNone(module.scope_policy(None, "non-node", self.rules))

    def test_main_stages_bundled_bytes_for_a_declared_node(self) -> None:
        code, outputs, workspace = run_main({"L9_PRODUCT_KIND": "node"})
        self.assertEqual(0, code)
        self.assertEqual("node", outputs["constellation-scope"])
        staged = workspace / outputs["sdk-policy"]
        self.assertEqual(BASE_POLICY_PATH.read_bytes(), staged.read_bytes())

    def test_main_stages_scoped_policy_when_undeclared(self) -> None:
        code, outputs, workspace = run_main({})
        self.assertEqual(0, code)
        self.assertEqual("non-node", outputs["constellation-scope"])
        self.assertEqual(
            ".l9/runtime/org-governance/semgrep-policy.yaml", outputs["sdk-policy"]
        )
        staged = json.loads((workspace / outputs["sdk-policy"]).read_text())
        for rule_id in self.rules["rules"]:
            self.assertEqual("disabled", staged["rules"][rule_id]["mode"])

    def test_scope_is_bound_into_the_governance_digest(self) -> None:
        _, node, _ = run_main({"L9_PRODUCT_KIND": "node"})
        _, undeclared, _ = run_main({})
        _, dependency, _ = run_main({"L9_PRODUCT_KIND": "dependency"})
        self.assertNotEqual(node["governance-digest"], undeclared["governance-digest"])
        self.assertEqual(
            undeclared["governance-digest"], dependency["governance-digest"]
        )

    def test_main_refuses_an_unadmitted_kind(self) -> None:
        code, _, _ = run_main({"L9_PRODUCT_KIND": "sdk"})
        self.assertEqual(2, code)

    def test_scoped_policy_is_written_outside_a_workspace(self) -> None:
        scoped = module.scope_policy(self.bundled, "non-node", self.rules)
        with unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GITHUB_WORKSPACE", None)
            path = Path(module.stage_selected_policy(scoped, DEFAULTS_ROOT))
        assert scoped is not None
        self.assertEqual(scoped[1], path.read_bytes())
        self.assertNotEqual(BASE_POLICY_PATH.resolve(), path.resolve())

    def test_invalid_scope_documents_fail_closed(self) -> None:
        valid = json.loads(SCOPE_PATH.read_text(encoding="utf-8"))
        mutations = {
            "schema": lambda d: d.update(schema="l9.other/v1"),
            "blocking-mode": lambda d: d.update(undeclared_mode="blocking"),
            "unknown-mode": lambda d: d.update(undeclared_mode="ignore"),
            "empty-rules": lambda d: d.update(gate_routing_rules=[]),
            "duplicate-rules": lambda d: d.update(
                gate_routing_rules=[ROUTING_PREFIX + "x", ROUTING_PREFIX + "x"]
            ),
            "gate-bound-not-admitted": lambda d: d["product_kinds"].update(
                gate_bound="sdk"
            ),
            "empty-policy-key": lambda d: d.update(undeclared_policy_key=" "),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                document = json.loads(json.dumps(valid))
                mutate(document)
                path = Path(temp) / "scope.json"
                path.write_text(json.dumps(document), encoding="utf-8")
                with self.assertRaises(module.GovernanceError):
                    module.load_constellation_rules(path)

    def test_action_wires_product_kind_in_and_scope_out(self) -> None:
        action = yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))
        product_kind = action["inputs"]["product-kind"]
        self.assertFalse(product_kind["required"])
        self.assertEqual("", product_kind["default"])
        env = action["runs"]["steps"][0]["env"]
        self.assertEqual("${{ inputs.product-kind }}", env["L9_PRODUCT_KIND"])
        self.assertIn("constellation-scope", action["outputs"])


if __name__ == "__main__":
    unittest.main()
