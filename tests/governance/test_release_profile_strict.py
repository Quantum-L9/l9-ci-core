"""The `release` execution profile is strict, blocking and Semgrep-required.

A release admits an artifact, so its analysis must not be weaker than a pull
request's: findings block (the central policy defaults to `blocking`), the
provider is required, and the SDK runs strict — `semgrep run --strict` and
`gate evaluate --strict-unresolved` — against the Core-staged identity map, so
an unresolved finding identity makes the gate `incomplete` (non-pass) instead
of passing silently. Each assertion below fails if one of those properties is
relaxed.
"""

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
MODULE_PATH = ROOT / ".github" / "actions" / "resolve-governance" / "resolve.py"
DEFAULTS_ROOT = ROOT / ".github" / "actions" / "resolve-governance" / "defaults"
ANALYZE = ROOT / ".github" / "workflows" / "analyze-semgrep.yml"
ORG_CI = ROOT / ".github" / "workflows" / "org-ci.yml"

spec = importlib.util.spec_from_file_location("resolve_governance", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def _resolve(profile: str, event: str, workspace: str) -> tuple[int, str, str]:
    env = {
        "L9_PROFILE": profile,
        "L9_PROVIDER": "semgrep",
        "L9_EVENT_NAME": event,
        "L9_REPOSITORY": "Quantum-L9/example",
        "L9_REF": "refs/heads/main",
        "GITHUB_WORKSPACE": workspace,
    }
    stdout, stderr = io.StringIO(), io.StringIO()
    with unittest.mock.patch.dict(os.environ, env, clear=False):
        os.environ.pop("GITHUB_OUTPUT", None)
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = module.main()
    return exit_code, stdout.getvalue(), stderr.getvalue()


def _analyze_steps() -> list[dict]:
    document = yaml.safe_load(ANALYZE.read_text(encoding="utf-8"))
    return list(document["jobs"]["analyze"]["steps"])


class ReleaseProfileStrictTests(unittest.TestCase):
    def setUp(self) -> None:
        self.documents = module.load_documents(DEFAULTS_ROOT)
        self.release = self.documents["execution-profiles.yaml"]["profiles"]["release"]

    def test_release_profile_declares_strict_blocking_semgrep(self) -> None:
        self.assertIs(True, self.release["strict"])
        self.assertEqual("blocking", self.release["default_mode"])
        self.assertEqual(["semgrep"], self.release["providers"])
        self.assertEqual(["release"], self.release["allowed_events"])

    def test_release_resolves_blocking_and_required(self) -> None:
        validated = module.validate_profile(
            self.documents, "release", "semgrep", "release"
        )
        self.assertEqual(
            "blocking",
            module.resolve_mode(
                self.documents, "release", "semgrep", validated["default_mode"]
            ),
        )
        self.assertIs(
            True, module.resolve_requiredness(self.documents, "release", "semgrep")
        )

    def test_release_profile_is_refused_for_any_other_event(self) -> None:
        for event in ("pull_request", "push", "merge", "nightly", "supply_chain"):
            with self.subTest(event=event):
                with self.assertRaises(module.GovernanceError):
                    module.validate_profile(self.documents, "release", "semgrep", event)

    def test_resolver_emits_strict_blocking_policy_and_identity_maps(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            exit_code, output, error = _resolve("release", "release", temp)
            self.assertEqual(0, exit_code, error)
            for line in (
                "strict=true",
                "mode=blocking",
                "enabled=true",
                "required-provider=true",
                "sdk-policy=.l9/runtime/org-governance/semgrep-policy.yaml",
                "identity-map-directory=.l9/runtime/org-governance/semgrep-identity-maps",
            ):
                self.assertIn(line, output.splitlines())
            policy = json.loads(
                (
                    Path(temp) / ".l9/runtime/org-governance/semgrep-policy.yaml"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual("blocking", policy["defaults"]["mode"])
            maps = Path(temp) / ".l9/runtime/org-governance/semgrep-identity-maps"
            self.assertEqual(
                set(module.IDENTITY_MAP_FILENAMES),
                {path.name for path in maps.iterdir() if path.is_file()},
            )

    def test_resolver_refuses_release_outside_the_release_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            exit_code, output, _ = _resolve("release", "nightly", temp)
        self.assertNotEqual(0, exit_code)
        self.assertNotIn("strict=", output)

    def test_analyze_semgrep_transports_strict_to_every_sdk_decision(self) -> None:
        steps = _analyze_steps()
        semgrep_run = next(
            step
            for step in steps
            if step.get("with", {}).get("operation") == "semgrep-run"
        )
        self.assertEqual(
            "${{ steps.gov.outputs.strict }}", semgrep_run["with"]["strict"]
        )
        self.assertEqual(
            "${{ steps.gov.outputs['sdk-policy'] }}", semgrep_run["with"]["policy"]
        )
        gate = next(step for step in steps if step.get("id") == "gate")
        self.assertEqual("${{ steps.gov.outputs.strict }}", gate["env"]["L9_STRICT"])
        script = " ".join(gate["run"].split())
        self.assertIn(
            'if [ "${L9_STRICT:-}" = "true" ]; then args+=(--strict-unresolved)', script
        )
        enforce = next(
            step for step in steps if step.get("name") == "Enforce SDK gate verdict"
        )
        self.assertIn(
            '[ "${GATE_STATUS}" != "pass" ] && [ "${MODE}" = "blocking" ]',
            enforce["run"],
        )

    def test_org_ci_gives_strict_runs_the_staged_identity_map(self) -> None:
        # org-ci.yml admits `release` by manual dispatch. A strict run without
        # the identity map fails in the SDK (`unresolved_strict_contract`)
        # instead of producing a policy verdict, so both entrypoints must pass
        # the same staged map.
        document = yaml.safe_load(ORG_CI.read_text(encoding="utf-8"))
        runs = [
            step
            for job in document["jobs"].values()
            for step in job.get("steps", [])
            if step.get("with", {}).get("operation") == "semgrep-run"
        ]
        self.assertEqual(1, len(runs))
        self.assertEqual("${{ steps.gov.outputs.strict }}", runs[0]["with"]["strict"])
        self.assertEqual(
            "${{ steps.gov.outputs['identity-map-directory'] }}/"
            "${{ steps.language.outputs.language }}.yaml",
            runs[0]["with"]["identity-map"],
        )


if __name__ == "__main__":
    unittest.main()
