# ruff: noqa: E402
from __future__ import annotations

import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from l9_repo.__main__ import (
    CoreRepositoryWorkflow,
    WorkflowError,
    validate_core_policy_data,
)  # noqa: E402


class CoreLocalPolicyTests(unittest.TestCase):
    def test_policy_is_valid_and_explicitly_core_local(self) -> None:
        policy = json.loads(
            (ROOT / ".l9/core-repo-policy.json").read_text(encoding="utf-8")
        )
        self.assertIs(validate_core_policy_data(policy), policy)
        self.assertIn("change_policy", policy)
        self.assertIn("agent_contracts", policy)

    def test_core_policy_unknown_fields_fail_closed(self) -> None:
        policy = json.loads(
            (ROOT / ".l9/core-repo-policy.json").read_text(encoding="utf-8")
        )
        policy["surprise"] = True
        with self.assertRaisesRegex(WorkflowError, "unsupported keys"):
            validate_core_policy_data(policy)

    def test_core_self_host_structural_validation_uses_v2_and_local_policy(
        self,
    ) -> None:
        workflow = CoreRepositoryWorkflow(ROOT)
        with self.subTest("v2"):
            workflow.verify_generated()
        with self.subTest("core-policy"):
            self.assertEqual(
                "l9.core-repository-policy/v1", workflow.policy()["schema"]
            )


if __name__ == "__main__":
    unittest.main()
