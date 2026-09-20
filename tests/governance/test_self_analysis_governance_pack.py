"""A strict profile must name the policy that resolves its findings.

`resolve-governance` reads two documents that only make sense together:
`execution-profiles.yaml` decides whether the SDK runs strict, and
`quality-thresholds.yaml` names the finding policy passed as `--policy`.
Strict means *every finding must be resolved by a policy*, so a profile that
declares `strict: true` while selecting `sdk_policy: ""` is not a strict gate
— it is a gate that aborts with `unresolved_strict_contract` on the first
finding of any severity, INFO included.

That is not hypothetical. The repository's own dogfood pack shipped exactly
that combination alongside a `semgrep-policy.yaml` nothing selected, and it
read as healthy for as long as the finding set happened to be empty. The
first INFO-level advisory finding failed `self-analysis.yml` while
`org-ci.yml` — same tree, same provider, policy correctly wired — passed.

An empty finding set is what hides this, so the test is written against the
declarations rather than against a scan result.

Scope is the two packs that govern live execution. `docs/templates/` and
`presets/` carry the same class of defect — the templates declare strict with
no policy at all, and both presets name `.github/governance/semgrep-policy.yaml`
where `_bundled_policy_path` accepts only a bare filename — but AGENTS.md
section 7 freezes those copy-first surfaces, so they are reported rather than
repaired here. Widen `PACKS` if they are ever unfrozen.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKS = {
    "self-analysis dogfood": ROOT / ".github" / "governance",
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

    def test_dogfood_policy_matches_the_central_posture(self) -> None:
        """The dogfood caller must not classify findings differently.

        `self-analysis.yml` exists to run Core through the same path offered
        to consumers. A policy that graded findings more leniently than the
        organization's would make that dogfood a weaker signal than the thing
        it stands in for; one that graded more harshly would fail Core on
        findings the organization accepts. Only the note may differ.
        """
        dogfood = load(PACKS["self-analysis dogfood"], "semgrep-policy.yaml")
        central = json.loads(
            (
                ROOT
                / ".github"
                / "actions"
                / "resolve-governance"
                / "semgrep-policy.yaml"
            ).read_text(encoding="utf-8")
        )
        for document in (dogfood, central):
            document.pop("note", None)
        self.assertEqual(central, dogfood)


if __name__ == "__main__":
    unittest.main()
