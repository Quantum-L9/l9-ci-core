"""A strict profile must name the policy that resolves its findings.

`resolve-governance` reads two documents that only make sense together:
`execution-profiles.yaml` decides whether the SDK runs strict, and
`quality-thresholds.yaml` names the finding policy passed as `--policy`.
Strict means *every finding must be resolved by a policy*, so a profile that
declares `strict: true` while selecting `sdk_policy: ""` is not a strict gate
— it is a gate that aborts with `unresolved_strict_contract` on the first
finding of any severity, INFO included.

That is not hypothetical. A former dogfood pack shipped exactly that
combination alongside a `semgrep-policy.yaml` nothing selected, and it read
as healthy for as long as the finding set happened to be empty.

An empty finding set is what hides this, so the test is written against the
declarations rather than against a scan result. There is exactly one pack
now: the Core defaults bundled with the pinned `resolve-governance` action.
The former `.github/governance/` dogfood copy was retired on 2026-10-22
(`AGENTS.md` §15); `self-analysis.yml` resolves the same `@core-defaults`
every governed repository gets, so it cannot grade findings differently.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKS = {
    "core defaults": ROOT / ".github" / "actions" / "resolve-governance" / "defaults",
}


def load(pack: Path, name: str) -> dict:
    return json.loads((pack / name).read_text(encoding="utf-8"))


class StrictProfilesSelectAPolicyTests(unittest.TestCase):
    def test_every_strict_profile_names_a_policy(self) -> None:
        for label, pack in PACKS.items():
            profiles = load(pack, "execution-profiles.yaml")["profiles"]
            thresholds = load(pack, "quality-thresholds.yaml")["profiles"]
            for name, profile in sorted(profiles.items()):
                if not profile.get("strict"):
                    continue
                with self.subTest(pack=label, profile=name):
                    selected = thresholds.get(name, {}).get("sdk_policy", "")
                    self.assertTrue(
                        selected,
                        f"{label}: profile {name!r} runs strict but selects no "
                        "sdk_policy, so the SDK aborts with "
                        "unresolved_strict_contract on the first finding",
                    )

    def test_every_selected_policy_resolves_to_a_file(self) -> None:
        """`_bundled_policy_path` looks in the pack, then beside it."""
        for label, pack in PACKS.items():
            thresholds = load(pack, "quality-thresholds.yaml")["profiles"]
            for name, selection in sorted(thresholds.items()):
                selected = selection.get("sdk_policy", "")
                if not selected:
                    continue
                with self.subTest(pack=label, profile=name):
                    self.assertEqual(
                        selected,
                        Path(selected).name,
                        "sdk_policy must be a bare filename",
                    )
                    self.assertTrue(
                        (pack / selected).is_file()
                        or (pack.parent / selected).is_file(),
                        f"{label}: profile {name!r} selects {selected!r}, which "
                        "is neither in the pack nor beside it",
                    )

    def test_no_second_governance_pack_is_shipped(self) -> None:
        """One pack, bundled with the pinned action (AGENTS.md §4, §15)."""
        self.assertFalse((ROOT / ".github" / "governance").exists())


if __name__ == "__main__":
    unittest.main()
