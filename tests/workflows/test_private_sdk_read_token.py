"""Every SDK provisioning step reads the private l9-ci-sdk with the org token.

l9-ci-sdk went private on 2026-11-16 (AGENTS.md §27). A provision-sdk step
without ``github-read-token`` fetches anonymously and fails with "could not
read Username"; one was missed in publish-analysis.yml until 2026-11-17 because
nothing enumerated them. A reusable workflow must also declare the secret, or
``secrets.L9_GITHUB_READ_TOKEN`` is empty inside it.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
TOKEN = "${{ secrets.L9_GITHUB_READ_TOKEN }}"


def _workflows() -> dict[str, dict]:
    return {
        path.name: yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])
    }


def _triggers(doc: dict) -> dict:
    # PyYAML reads the bare key ``on`` as boolean True.
    on = doc.get("on", doc.get(True)) or {}
    return on if isinstance(on, dict) else {name: None for name in on}


class PrivateSdkReadTokenTests(unittest.TestCase):
    def test_every_provision_sdk_step_passes_the_read_token(self) -> None:
        found = 0
        for name, doc in _workflows().items():
            for job_id, job in (doc.get("jobs") or {}).items():
                for step in job.get("steps") or []:
                    if "provision-sdk" not in str(step.get("uses", "")):
                        continue
                    found += 1
                    with self.subTest(workflow=name, job=job_id):
                        self.assertEqual(
                            (step.get("with") or {}).get("github-read-token"), TOKEN
                        )
        self.assertGreater(found, 0, "no provision-sdk step found; the scan is broken")

    def test_reusable_workflows_that_use_the_token_declare_it(self) -> None:
        for name, doc in _workflows().items():
            if TOKEN not in yaml.safe_dump(doc.get("jobs") or {}):
                continue
            call = _triggers(doc).get("workflow_call")
            if call is None and "workflow_call" not in _triggers(doc):
                continue
            with self.subTest(workflow=name):
                secrets = (call or {}).get("secrets") or {}
                self.assertIn("L9_GITHUB_READ_TOKEN", secrets)
                self.assertFalse(secrets["L9_GITHUB_READ_TOKEN"].get("required", False))


if __name__ == "__main__":
    unittest.main()
