from __future__ import annotations
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / ".github/actions/resolve-governance/resolve.py"
spec = importlib.util.spec_from_file_location("resolve_governance", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ResolveGovernanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = ROOT / ".github/governance"
        self.documents = module.load_documents(self.root)

    def test_pr_fast_semgrep_is_blocking_and_required(self) -> None:
        profile = module.validate_profile(
            self.documents,
            "pr_fast",
            "semgrep",
            "pull_request",
        )
        mode = module.resolve_mode(
            self.documents,
            "pr_fast",
            "semgrep",
            profile["default_mode"],
        )
        required = module.resolve_requiredness(
            self.documents,
            "pr_fast",
            "semgrep",
        )
        self.assertEqual("blocking", mode)
        self.assertTrue(required)
        self.assertTrue(profile["strict"])

    def test_nightly_semgrep_is_advisory_and_optional(self) -> None:
        profile = module.validate_profile(
            self.documents,
            "nightly",
            "semgrep",
            "schedule",
        )
        mode = module.resolve_mode(
            self.documents,
            "nightly",
            "semgrep",
            profile["default_mode"],
        )
        required = module.resolve_requiredness(
            self.documents,
            "nightly",
            "semgrep",
        )
        self.assertEqual("advisory", mode)
        self.assertFalse(required)

    def test_unknown_profile_fails_closed(self) -> None:
        with self.assertRaises(module.GovernanceError):
            module.validate_profile(
                self.documents,
                "unknown",
                "semgrep",
                "pull_request",
            )

    def test_wrong_event_fails_closed(self) -> None:
        with self.assertRaises(module.GovernanceError):
            module.validate_profile(
                self.documents,
                "release",
                "semgrep",
                "pull_request",
            )

    def test_expired_waiver_is_rejected(self) -> None:
        documents = dict(self.documents)
        documents["waivers.yaml"] = {
            "schema": "l9.waivers/v1",
            "waivers": [
                {
                    "id": "expired-1",
                    "owner": "platform",
                    "reason": "test",
                    "created": "2025-01-01",
                    "expires": "2025-01-02",
                    "scope": {
                        "repositories": ["Quantum-L9/*"],
                        "refs": [],
                        "profiles": [],
                        "providers": [],
                    },
                }
            ],
        }
        with self.assertRaises(module.GovernanceError):
            module.applicable_waivers(
                documents,
                profile="pr_fast",
                provider="semgrep",
                repository="Quantum-L9/l9-ci-core",
                ref="refs/heads/main",
                today=module.dt.date(2026, 7, 17),
            )

    def test_digest_is_deterministic(self) -> None:
        first = module.canonical_digest(self.root)
        second = module.canonical_digest(self.root)
        self.assertEqual(first, second)
        self.assertEqual(64, len(first))


if __name__ == "__main__":
    unittest.main()
