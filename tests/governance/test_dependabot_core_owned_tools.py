"""Core's Dependabot never proposes a lock-owned tool.

Tool versions move only through ``toolchain-lock.json`` and its test-bound
copies (docs/consumer-lint-test.md "Bumping a tool version"). Core is
``self_governed`` in ``Quantum-L9/.github`` ``policies/repo-classes.yml``, so
``ops/render-dependabot.js`` does not render Core's ``.github/dependabot.yml``;
this test binds Core's hand-maintained file to the same name list the renderer
reads: ``.github/actions/install-consumer-ci/core-owned-tools.json``.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEPENDABOT = ROOT / ".github" / "dependabot.yml"
TOOLS = ROOT / ".github" / "actions" / "install-consumer-ci" / "core-owned-tools.json"

# core-owned-tools.json ecosystem key -> Dependabot package-ecosystem.
ECOSYSTEMS = {"pip": "pip", "uv": "uv", "npm": "npm", "pre-commit": "pre-commit"}


def owned(ecosystem: str) -> set[str]:
    payload = json.loads(TOOLS.read_text(encoding="utf-8"))
    return {
        name
        for tool in payload["tools"]
        for name in tool["ecosystems"].get(ecosystem, [])
    }


def whole_ignores(update: dict[str, object]) -> set[str]:
    """Names ignored for every version (no update-types/versions narrowing)."""
    names: set[str] = set()
    for rule in update.get("ignore", []) or []:  # type: ignore[union-attr]
        if set(rule) == {"dependency-name"}:
            names.add(rule["dependency-name"])
    return names


class DependabotCoreOwnedToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.updates = yaml.safe_load(DEPENDABOT.read_text(encoding="utf-8"))["updates"]

    def test_every_ecosystem_ignores_every_lock_owned_tool(self) -> None:
        for update in self.updates:
            ecosystem = update["package-ecosystem"]
            key = next((k for k, v in ECOSYSTEMS.items() if v == ecosystem), None)
            if key is None:
                continue
            with self.subTest(ecosystem=ecosystem):
                names = owned(key)
                self.assertTrue(names, f"no Core-owned {key} identifiers")
                self.assertEqual(set(), names - whole_ignores(update))

    def test_the_uv_ecosystem_ignores_exactly_the_lock_owned_names(self) -> None:
        [uv] = [u for u in self.updates if u["package-ecosystem"] == "uv"]
        self.assertEqual(owned("uv"), whole_ignores(uv))

    def test_ecosystems_are_known(self) -> None:
        # A new ecosystem must be classified here, so its ignores are checked.
        known = set(ECOSYSTEMS.values()) | {"github-actions"}
        for update in self.updates:
            with self.subTest(update=update["package-ecosystem"]):
                self.assertIn(update["package-ecosystem"], known)

    def test_github_actions_never_moves_core_or_sdk_pins(self) -> None:
        [actions] = [
            u for u in self.updates if u["package-ecosystem"] == "github-actions"
        ]
        self.assertLessEqual(
            {"Quantum-L9/l9-ci-core*", "Quantum-L9/l9-ci-sdk*"},
            whole_ignores(actions),
        )


if __name__ == "__main__":
    unittest.main()
