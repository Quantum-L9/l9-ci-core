"""The SDK contract check exercises the compatibility default (release plane).

``sdk-contract-check.yml`` provisions the SDK through the in-tree
``./.github/actions/provision-sdk`` action, so it is the one gate that proves a
promoted revision against the manifest and runtime lock in the same tree. It
must therefore pin exactly ``default.revision`` of ``.l9/sdk-compatibility.yaml``
and assert that revision is what the provisioner emitted; a promotion that left
it on the previous revision would pass the gate without ever provisioning the
SDK it promotes.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "sdk-contract-check.yml"
MANIFEST = ROOT / ".l9" / "sdk-compatibility.yaml"


def provision_step() -> dict:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = [
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if str(step.get("uses", "")).endswith("/provision-sdk")
    ]
    if len(steps) != 1:
        msg = f"expected one provision-sdk step, found {len(steps)}"
        raise AssertionError(msg)
    return steps[0]


class SdkContractCheckRevisionTests(unittest.TestCase):
    def setUp(self) -> None:
        manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
        self.default = manifest["default"]["revision"]

    def test_gate_provisions_through_the_in_tree_action(self) -> None:
        self.assertEqual("./.github/actions/provision-sdk", provision_step()["uses"])

    def test_gate_provisions_the_compatibility_default(self) -> None:
        self.assertEqual(self.default, provision_step()["with"]["sdk-revision"])

    def test_gate_asserts_the_emitted_revision_is_the_default(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            r'test "\$\{SDK_REVISION\}" =\s+"' + self.default + '"',
        )


if __name__ == "__main__":
    unittest.main()
