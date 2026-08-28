"""Native push is a first-class canonical event class.

The organization entrypoint's `allowed` event set has always contained
`push`, and the `merge` execution profile has always declared `push` in its
`allowed_events` — but nothing could *reach* that class, because the workflow
declared no native `push` trigger and the runtime resolver had no `push`
branch. A governance event that is valid but unreachable is a latent
inconsistency, not a feature.

These tests re-derive the mapping by **executing the workflow's own resolver**
rather than pattern-matching its text, so a future edit that changes the
resolver's behavior fails here even if it keeps the surrounding wording.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT_PATH = ROOT / ".github" / "workflows" / "org-ci.yml"
PROFILES_PATH = (
    ROOT
    / ".github"
    / "actions"
    / "resolve-governance"
    / "defaults"
    / "execution-profiles.yaml"
)
CONTRACT_PATH = ROOT / ".l9" / "org-runtime-contract.yaml"

HEREDOC = re.compile(r"python3 - <<'PY'\n(?P<body>.*?)\n\s*PY\s*$", re.DOTALL)
# `${{ ... }}` expression inside a workflow `env:` value.
EXPRESSION = re.compile(r"^\$\{\{\s*(?P<body>.*?)\s*\}\}$")


def load_workflow() -> dict:
    document = yaml.safe_load(ENTRYPOINT_PATH.read_text(encoding="utf-8"))
    # PyYAML resolves the bare `on:` key to the boolean True.
    if True in document:
        document["on"] = document.pop(True)
    return document


def analyze_steps() -> list[dict]:
    return load_workflow()["jobs"]["analyze"]["steps"]


def step_by_id(step_id: str) -> dict:
    for step in analyze_steps():
        if step.get("id") == step_id:
            return step
    raise AssertionError(f"no step with id {step_id!r} in the analyze job")


def resolver_source() -> str:
    """The runtime resolver exactly as the workflow runs it."""
    run = step_by_id("runtime")["run"]
    match = HEREDOC.search(run.strip())
    assert match is not None, "runtime step no longer runs a python3 heredoc"
    body = match.group("body")
    # The heredoc body is indented to sit inside the YAML block scalar.
    indent = min(
        len(line) - len(line.lstrip()) for line in body.splitlines() if line.strip()
    )
    return "\n".join(
        line[indent:] if line.strip() else "" for line in body.splitlines()
    )


GATE_TERM = re.compile(
    r"^(?P<left>[A-Za-z0-9_.]+|'[^']*')\s*(?P<op>==|!=)\s*(?P<right>[A-Za-z0-9_.]+|'[^']*')$"
)


def evaluate_gate(
    expression: str,
    *,
    event_name: str,
    ref_name: str,
    inputs_event: str,
    default_branch: str,
) -> bool:
    """Evaluate the job gate against a modelled event context.

    A deliberately small evaluator for the one expression shape this gate uses:
    `||`-joined `==` / `!=` comparisons over context paths and string literals.
    It is a model of GitHub's evaluation, not GitHub's evaluator, so it is kept
    narrow enough to read: an expression it cannot parse raises rather than
    quietly returning a passing result. Ground truth for the gate remains a
    real run — a non-default-branch push must show the job skipped.

    An absent `inputs.event` is modelled as GitHub does: a missing context
    property is null, and null compares equal to the empty string.
    """
    context = {
        "github.event_name": event_name,
        "github.ref_name": ref_name,
        "github.event.repository.default_branch": default_branch,
        # A native push has no inputs context; the property resolves to null.
        "inputs.event": inputs_event if inputs_event else None,
    }

    def operand(token: str) -> str | None:
        if token.startswith("'") and token.endswith("'"):
            return token[1:-1]
        if token not in context:
            raise AssertionError(f"gate references unmodelled context {token!r}")
        return context[token]

    for term in expression.split("||"):
        match = GATE_TERM.match(term.strip())
        if match is None:
            raise AssertionError(f"unparsable gate term: {term.strip()!r}")
        left = operand(match.group("left"))
        right = operand(match.group("right"))
        # GitHub coerces null and '' to the same value in a comparison.
        left = "" if left is None else left
        right = "" if right is None else right
        if (left == right) if match.group("op") == "==" else (left != right):
            return True
    return False


def resolve(
    *,
    event_name: str,
    inputs: dict[str, str] | None = None,
) -> dict[str, str] | str:
    """Run the resolver for one trigger; return outputs, or its exit message.

    `inputs` models the `inputs.*` context. Anything omitted takes the same
    default the workflow's `env:` block applies, so a native `push` — which
    has no `inputs` context at all — is exercised exactly as GitHub runs it.
    """
    supplied = inputs or {}
    environment = {
        "GH_EVENT": event_name,
        "INPUT_EVENT": supplied.get("event", ""),
        "INPUT_PROFILE": supplied.get("profile", ""),
        "INPUT_MATRIX_ID": supplied.get("matrix-id", "org-semgrep"),
        "INPUT_SDK_REVISION": supplied.get("sdk-revision", ""),
        "INPUT_SEMGREP_VERSION": supplied.get("semgrep-version", "1.171.0"),
        "INPUT_RETENTION": supplied.get("artifact-retention-days", "14"),
    }
    with tempfile.TemporaryDirectory() as directory:
        output_path = Path(directory) / "github_output"
        output_path.touch()
        environment["GITHUB_OUTPUT"] = str(output_path)
        completed = subprocess.run(
            [sys.executable, "-c", resolver_source()],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            return (completed.stderr or completed.stdout).strip()
        return dict(
            line.split("=", 1)
            for line in output_path.read_text(encoding="utf-8").splitlines()
            if line
        )


class NativePushEventClassTests(unittest.TestCase):
    """The four mappings the canonical analysis engine must honor."""

    def test_pull_request_resolves_to_pr_fast(self) -> None:
        outputs = resolve(event_name="pull_request")
        assert isinstance(outputs, dict), outputs
        self.assertEqual("pull_request", outputs["event-class"])
        self.assertEqual("pr_fast", outputs["profile"])

    def test_merge_group_resolves_to_merge(self) -> None:
        outputs = resolve(event_name="merge_group")
        assert isinstance(outputs, dict), outputs
        self.assertEqual("merge", outputs["event-class"])
        self.assertEqual("merge", outputs["profile"])

    def test_native_push_resolves_to_push_on_the_merge_profile(self) -> None:
        outputs = resolve(event_name="push")
        assert isinstance(outputs, dict), outputs
        self.assertEqual("push", outputs["event-class"])
        self.assertEqual("merge", outputs["profile"])

    def test_workflow_call_push_resolves_identically_to_native_push(self) -> None:
        """A called `push` and a native `push` are the same evaluation.

        The compatibility path pins `push` to `merge` through its `fixed`
        table; the native path pins it in the trigger branch. Both must land on
        the same engine, or the same revision would be judged differently
        depending on how CI was reached.
        """
        called = resolve(event_name="workflow_call", inputs={"event": "push"})
        native = resolve(event_name="push")
        assert isinstance(called, dict), called
        assert isinstance(native, dict), native
        self.assertEqual("push", called["event-class"])
        self.assertEqual("merge", called["profile"])
        self.assertEqual(
            (native["event-class"], native["profile"]),
            (called["event-class"], called["profile"]),
        )

    def test_a_caller_cannot_override_the_push_profile(self) -> None:
        """`fixed` wins over a caller-supplied profile for `push`.

        Profile selection is central governance. A caller that asks for `push`
        analysis under `nightly` (advisory) would otherwise downgrade a
        blocking evaluation by naming a different profile.
        """
        outputs = resolve(
            event_name="workflow_call",
            inputs={"event": "push", "profile": "nightly"},
        )
        assert isinstance(outputs, dict), outputs
        self.assertEqual("merge", outputs["profile"])

    def test_push_class_is_allowed_by_the_profile_it_resolves_to(self) -> None:
        outputs = resolve(event_name="push")
        assert isinstance(outputs, dict), outputs
        profiles = yaml.safe_load(PROFILES_PATH.read_text(encoding="utf-8"))["profiles"]
        profile = profiles[outputs["profile"]]
        self.assertIn(outputs["event-class"], profile["allowed_events"])
        self.assertEqual("blocking", profile["default_mode"])

    def test_unsupported_triggers_still_fail_closed(self) -> None:
        for event_name in ("pull_request_target", "schedule", "issue_comment"):
            with self.subTest(event=event_name):
                result = resolve(event_name=event_name)
                self.assertIsInstance(result, str)
                assert isinstance(result, str)
                self.assertIn("unsupported trigger", result)


class PushTargetProvenanceTests(unittest.TestCase):
    """The push target comes only from the GitHub event context."""

    def test_checkout_pins_the_immutable_event_revision(self) -> None:
        checkout = analyze_steps()[0]
        self.assertEqual("Checkout immutable event revision", checkout["name"])
        self.assertEqual("${{ github.repository }}", checkout["env"]["REPOSITORY"])
        self.assertEqual("${{ github.sha }}", checkout["env"]["REVISION"])
        self.assertIn("git checkout --detach FETCH_HEAD", checkout["run"])

    def test_no_input_can_supply_a_repository_ref_or_target_sha(self) -> None:
        """`sdk-revision` pins the SDK, never the repository under analysis.

        Without this, a `workflow_call` caller could aim the canonical
        evaluation at a revision other than the one the event carried, and a
        birth attestation would no longer be correlated to the pushed SHA.
        """
        triggers = load_workflow()["on"]
        for trigger in ("workflow_call", "workflow_dispatch"):
            declared = set(triggers[trigger]["inputs"])
            self.assertEqual(
                set(),
                declared & {"repository", "repo", "ref", "sha", "revision", "head-sha"},
                f"{trigger} must not accept a target selector",
            )

    def test_the_analysis_snapshot_is_the_event_sha(self) -> None:
        invoke = None
        for step in analyze_steps():
            if step.get("name") == "Run + normalize Semgrep (SDK)":
                invoke = step
        self.assertIsNotNone(invoke)
        assert invoke is not None
        self.assertEqual("${{ github.sha }}", invoke["with"]["snapshot-id"])
        self.assertEqual("${{ github.sha }}", invoke["with"]["revision"])

    def test_the_resolver_reads_the_trigger_from_the_event_context(self) -> None:
        runtime = step_by_id("runtime")
        match = EXPRESSION.match(str(runtime["env"]["GH_EVENT"]))
        self.assertIsNotNone(match, runtime["env"]["GH_EVENT"])
        assert match is not None
        self.assertEqual("github.event_name", match.group("body"))

    def test_push_validates_its_event_context_before_analysis(self) -> None:
        steps = analyze_steps()
        names = [step.get("name") for step in steps]
        guard = names.index("Validate native push event context")
        self.assertLess(names.index("Resolve central runtime class"), guard)
        self.assertLess(guard, names.index("Resolve central governance"))
        step = steps[guard]
        self.assertEqual("github.event_name == 'push'", step["if"])
        for message in (
            "push execution has no repository identity",
            "push execution has no immutable SHA",
            "push SHA is not a full 40-character revision",
        ):
            self.assertIn(message, step["run"])

    def test_the_push_path_requests_no_write_scope(self) -> None:
        document = load_workflow()
        self.assertEqual({"contents": "read"}, document["permissions"])
        self.assertEqual(
            {"contents": "read"}, document["jobs"]["analyze"]["permissions"]
        )


class PushRefGateTests(unittest.TestCase):
    """A native push is evaluated only on the repository's own default branch.

    An unfiltered `on: push` fires for every branch and tag, which across every
    governed repository means a full canonical evaluation on each feature-branch
    push *in addition to* that branch's pull_request evaluation. The ref
    decision therefore moves to runtime, where the repository's own
    `default_branch` is readable from the event payload — the symbolic value
    `on.push.branches` cannot express.
    """

    def gate(self) -> str:
        return " ".join(load_workflow()["jobs"]["analyze"]["if"].split())

    def test_the_gate_compares_against_the_repositorys_declared_default(self) -> None:
        self.assertIn(
            "github.ref_name == github.event.repository.default_branch", self.gate()
        )

    def test_the_gate_names_no_literal_branch(self) -> None:
        """A branch name in Core would silently exclude repositories.

        This is the whole reason the selector is not `branches: [main]`. A
        literal here would reintroduce the same defect one layer down, where it
        is harder to see.
        """
        gate = self.gate()
        for branch in ("'main'", '"main"', "'master'", "'trunk'", "'develop'"):
            self.assertNotIn(branch, gate)

    def test_the_gate_only_narrows_push(self) -> None:
        """pull_request and merge_group evaluation must be untouched."""
        self.assertIn("github.event_name != 'push'", self.gate())

    def test_a_reusable_invocation_is_exempt(self) -> None:
        """`github.event_name` is the *caller's* event inside a called workflow.

        A caller triggered by its own push would otherwise inherit this ref
        gate and skip the analysis it explicitly asked for. `workflow_call`
        declares `event` as required, so a non-empty `inputs.event` is exactly
        the signal that this run was invoked rather than natively triggered.
        """
        self.assertIn("inputs.event != ''", self.gate())
        triggers = load_workflow()["on"]
        self.assertTrue(triggers["workflow_call"]["inputs"]["event"]["required"])

    def test_the_gate_is_job_level_so_a_skip_claims_no_runner(self) -> None:
        """Gating inside a step would still pay for a runner on every push."""
        document = load_workflow()
        self.assertIn("if", document["jobs"]["analyze"])
        for step in document["jobs"]["analyze"]["steps"]:
            self.assertNotIn(
                "default_branch",
                str(step.get("if", "")),
                "the ref decision belongs on the job, not on a step",
            )

    def test_the_gate_admits_a_default_branch_push_and_rejects_a_feature_push(
        self,
    ) -> None:
        """Evaluate the real expression against modelled event contexts."""
        cases = (
            # (event_name, ref_name, inputs.event, default_branch, expected)
            ("push", "main", "", "main", True),
            ("push", "trunk", "", "trunk", True),
            ("push", "feature/x", "", "main", False),
            ("push", "v1.2.3", "", "main", False),
            ("push", "main", "", "trunk", False),
            ("pull_request", "feature/x", "", "main", True),
            ("merge_group", "gh-readonly-queue/main/x", "", "main", True),
            ("workflow_dispatch", "feature/x", "nightly", "main", True),
            # A reusable caller that is itself on a feature-branch push.
            ("push", "feature/x", "push", "main", True),
        )
        for event_name, ref_name, called_event, default_branch, expected in cases:
            with self.subTest(event=event_name, ref=ref_name, input=called_event):
                self.assertEqual(
                    expected,
                    evaluate_gate(
                        self.gate(),
                        event_name=event_name,
                        ref_name=ref_name,
                        inputs_event=called_event,
                        default_branch=default_branch,
                    ),
                )


class PushCarriesNoLifecycleAuthorityTests(unittest.TestCase):
    """Core evaluates revisions; it does not classify repository lifecycle.

    Genesis is decided by the birth control plane, from the run's head SHA and
    the expected zero-parent root SHA. Teaching Core to infer it — from
    repository age, a custom property, or a `genesis` event class — would move
    birth policy into the execution engine and break the separation where the
    ruleset decides *who* gets CI and Core decides *how* CI behaves.
    """

    def test_no_genesis_or_lifecycle_event_class_exists(self) -> None:
        text = ENTRYPOINT_PATH.read_text(encoding="utf-8")
        allowed = set(
            re.search(r"allowed = \{([^}]*)\}", text)
            .group(1)
            .replace('"', "")
            .split(", ")
        )
        self.assertEqual(
            {"pull_request", "push", "merge", "nightly", "release", "supply_chain"},
            allowed,
        )
        # Executable surface only. Round-tripping through PyYAML drops the
        # header comments, which *say* Core holds no genesis logic and would
        # otherwise trip a bare substring search on their own explanation.
        executable = yaml.safe_dump(load_workflow(), default_flow_style=False).lower()
        for token in ("genesis", "lifecycle", "provisional", "repository_age"):
            self.assertNotIn(
                token,
                executable,
                f"{token!r} is birth-policy vocabulary; it belongs to the "
                "birth control plane, not to the CI execution engine",
            )

    def test_the_contract_records_that_core_holds_no_lifecycle_logic(self) -> None:
        contract = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
        entrypoint = contract["entrypoint"]
        self.assertFalse(entrypoint["lifecycle_logic_in_core"])
        self.assertIn("push", entrypoint["native_events"])
        self.assertEqual("none", entrypoint["push_branch_selector"])

    def test_the_interface_keeps_ruleset_push_instantiation_unknown(self) -> None:
        """Declaring `on: push` does not prove a ruleset instantiates it.

        The organization required-workflow mechanism and ordinary workflow
        triggers are related but not proven identical execution surfaces, and
        no repository-local test can settle it. The claim stays UNKNOWN until
        a real run in a targeted repository shows otherwise.
        """
        interface = yaml.safe_load(
            (ROOT / ".l9" / "org-runtime-interface.yaml").read_text(encoding="utf-8")
        )
        claims = {claim["id"]: claim for claim in interface["claims"]}
        claim = claims["ruleset-instantiates-workflow-on-native-push"]
        self.assertEqual("UNKNOWN", claim["status"])
        self.assertEqual([], claim["evidence"])


if __name__ == "__main__":
    unittest.main()
