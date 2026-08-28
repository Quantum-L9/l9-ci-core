"""A caller's profile must admit every event the caller triggers on.

`resolve-governance` fails closed when a profile does not declare the incoming
event: `event 'push' is not allowed for profile 'pr_fast'`, exit 2. That is
correct behavior — but nothing checked the pairing at authoring time, so a
workflow could declare triggers its hardcoded profile could never serve, and
the mismatch surfaced only as a red check on the branch it was pushed to.

`self-analysis.yml` was exactly that: it declares `pull_request`, `push` to
main, and `workflow_dispatch`, but passed a literal `profile: pr_fast`, whose
`allowed_events` is `["pull_request"]` alone. Every push to main failed
governance resolution from 2026-08-18 until this test was written, and every
manual dispatch would have failed the same way. The PR path stayed green, so
the failure never blocked a merge and simply accumulated on main.

A second, deeper defect surfaced while fixing the first: `resolve-governance`
wants a governance event *class*, but the kernel passed the raw trigger name.
Every class but `nightly` happens to share its trigger's name, so the mismatch
was invisible until a caller was dispatched manually — `workflow_dispatch` is
not a class any profile declares, so no manual run could ever have resolved.

This test derives both halves from the workflows themselves — the event class
from the kernel's own `event-name` expression, the profile from the caller's —
and checks them against the profile SSOT rather than restating any of it. So
adding a trigger to a caller, changing the kernel's class mapping, or narrowing
a profile's `allowed_events` fails here at authoring time instead of after the
push.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
PROFILES_PATH = (
    ROOT
    / ".github"
    / "actions"
    / "resolve-governance"
    / "defaults"
    / "execution-profiles.yaml"
)

# `${{ a == 'x' && 'p' || b == 'y' && 'q' || 'r' }}` — GitHub's ternary chain.
# Used for both caller `profile` and kernel `event-name`, which share the shape:
# `value` is the arm selected when `github.event_name` equals `event`.
CONDITIONAL_PROFILE = re.compile(
    r"github\.event_name\s*==\s*'(?P<event>[a-z_]+)'\s*&&\s*'(?P<profile>[a-z_]+)'"
)
FALLBACK_PROFILE = re.compile(r"\|\|\s*'(?P<profile>[a-z_]+)'\s*\}\}")


def profiles() -> dict:
    return yaml.safe_load(PROFILES_PATH.read_text(encoding="utf-8"))["profiles"]


def load(path: Path) -> dict:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if True in document:
        document["on"] = document.pop(True)
    return document


def triggering_events(document: dict) -> set[str]:
    """The `github.event_name` values this workflow can actually run under."""
    triggers = document.get("on")
    if isinstance(triggers, str):
        return {triggers}
    if isinstance(triggers, list):
        return set(triggers)
    return set(triggers or {})


def resolve_profile(expression: object, event: str) -> str | None:
    """The profile this caller passes for `event`, or None if undetermined."""
    text = str(expression)
    if "${{" not in text:
        return text.strip()
    for match in CONDITIONAL_PROFILE.finditer(text):
        if match.group("event") == event:
            return match.group("profile")
    fallback = FALLBACK_PROFILE.search(text)
    return fallback.group("profile") if fallback else None


def kernel_event_class(trigger: str) -> str:
    """The event *class* the analysis kernel passes for a given trigger.

    Derived from `analyze-semgrep.yml` rather than restated here, so a change
    to the kernel's mapping is reflected in this check instead of silently
    diverging from it. Every class but `nightly` shares its trigger's name;
    the fallback arm is the identity `github.event_name`.
    """
    document = load(WORKFLOWS / "analyze-semgrep.yml")
    for job in document["jobs"].values():
        for step in job.get("steps") or []:
            if step.get("id") == "gov":
                expression = str(step["with"]["event-name"])
                for match in CONDITIONAL_PROFILE.finditer(expression):
                    if match.group("event") == trigger:
                        return match.group("profile")
                return trigger
    raise AssertionError("analyze-semgrep.yml no longer has a `gov` step")


def analysis_callers() -> list[tuple[Path, dict, dict]]:
    """Workflows that call the analysis kernel with a governance profile."""
    found = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        document = load(path)
        for job in (document.get("jobs") or {}).values():
            if not isinstance(job, dict):
                continue
            uses = str(job.get("uses", ""))
            given = job.get("with") or {}
            if "analyze-semgrep.yml" in uses and "profile" in given:
                found.append((path, document, given))
    return found


class CallerProfileEventCompatibilityTests(unittest.TestCase):
    def test_at_least_one_analysis_caller_is_examined(self) -> None:
        """Guard against the suite silently covering nothing."""
        self.assertTrue(analysis_callers(), "no analysis callers found to check")

    def test_every_caller_profile_admits_every_event_it_triggers_on(self) -> None:
        declared = profiles()
        for path, document, given in analysis_callers():
            for trigger in sorted(triggering_events(document)):
                event = kernel_event_class(trigger)
                with self.subTest(workflow=path.name, trigger=trigger, event=event):
                    name = resolve_profile(given["profile"], trigger)
                    self.assertIsNotNone(
                        name,
                        f"{path.name} passes a profile expression this test "
                        "cannot resolve; keep it readable or extend the parser",
                    )
                    assert name is not None
                    self.assertIn(
                        name,
                        declared,
                        f"{path.name} on {event} passes unknown profile {name!r}",
                    )
                    self.assertIn(
                        event,
                        declared[name]["allowed_events"],
                        f"{path.name} triggers on {trigger!r} (event class "
                        f"{event!r}) but passes profile {name!r}, whose "
                        f"allowed_events are {declared[name]['allowed_events']}. "
                        f"resolve-governance will abort with 'event {event!r} is "
                        f"not allowed for profile {name!r}'.",
                    )

    def test_self_analysis_maps_events_the_way_org_ci_does(self) -> None:
        """The dogfood caller and the organization surface must agree.

        If the two disagree, the same revision is analysed under different
        profiles depending on which surface reached it — which is exactly the
        inconsistency the central entrypoint exists to prevent.
        """
        document = load(WORKFLOWS / "self-analysis.yml")
        given = document["jobs"]["analyze"]["with"]
        for event, expected in (
            ("pull_request", "pr_fast"),
            ("push", "merge"),
            ("workflow_dispatch", "nightly"),
        ):
            with self.subTest(event=event):
                self.assertEqual(expected, resolve_profile(given["profile"], event))


if __name__ == "__main__":
    unittest.main()
