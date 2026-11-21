"""Core's promotion canary (``tools/promotion_canary.py``, AGENTS.md §40).

The generated workflow is compared byte for byte with what the tool renders from
the current ``org-ci.yml``; the plan, the applicability decision and the verdict
run here against real files, a real git history and recorded legs.
"""

from __future__ import annotations

import importlib.util
import io
import json
import re
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "promotion_canary.py"


def _load():
    spec = importlib.util.spec_from_file_location("core_promotion_canary", TOOL)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pc = _load()
ADMISSION = (ROOT / pc.PROVISION_ACTION).read_text(encoding="utf-8")
COMPATIBILITY = (ROOT / pc.SDK_COMPATIBILITY).read_text(encoding="utf-8")


def write_admission(root: Path) -> None:
    """The provision-sdk action and compatibility manifest a tree carries."""
    for path, text in (
        (pc.PROVISION_ACTION, ADMISSION),
        (pc.SDK_COMPATIBILITY, COMPATIBILITY),
    ):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text, encoding="utf-8")


ORG_CI_TEXT = (ROOT / pc.ORG_CI).read_text(encoding="utf-8")
# org-ci.yml calls security.yml at a pinned revision (#312). Tests read the pin
# from org-ci.yml itself, not through the tool under test.
SECURITY_SHA = re.search(
    r"uses: Quantum-L9/l9-ci-core/\.github/workflows/security\.yml@([0-9a-f]{40})",
    ORG_CI_TEXT,
).group(1)
SECURITY_JOB = f"""\
  # A reusable job ahead of analyze, as #312 adds.
  security:
    if: >-
      github.event_name != 'push'
    uses: Quantum-L9/l9-ci-core/.github/workflows/security.yml@{SECURITY_SHA}
    with:
      gitleaks-scope: event

"""
ENFORCE = """
      - name: Enforce security scan (secrets, dependency audit)
        if: always()
        env:
          SECURITY_RESULT: ${{ needs.security.result }}
        run: |
          test "${SECURITY_RESULT}" = success
"""


def with_security(org_ci: str) -> str:
    """org-ci.yml as #312 shapes it: a security job that analyze needs.

    The committed org-ci.yml already has it; a variant without it gains it.
    """
    if "\n  security:\n" in org_ci:
        return org_ci
    return (
        org_ci.replace("\njobs:\n", "\njobs:\n" + SECURITY_JOB, 1).rstrip("\n")
        + "\n"
        + ENFORCE
    )


def security_provider(name: str, path: str, sha: str, values: dict[str, str]) -> str:
    workflow = (ROOT / path).read_text(encoding="utf-8")
    return pc.inline_job(name, workflow, values, f"{path}@{sha}")


def leg_steps(workflow: str, job: str) -> list[dict[str, object]]:
    return yaml.safe_load(workflow)["jobs"][job]["steps"]


class RenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.org_ci = (ROOT / pc.ORG_CI).read_text(encoding="utf-8")
        self.canary = (ROOT / pc.CANARY).read_text(encoding="utf-8")

    def test_the_committed_canary_is_the_render_of_org_ci(self) -> None:
        """A reusable job is inlined at the revision org-ci.yml pins, not from
        the working tree: security.yml can move past its pin (#329 did) with
        org-ci.yml and the canary unchanged. As in `render --check`, the
        inlined blocks come from the canary itself; the plan job checks them
        against the pinned sources with full history (`head-current`)."""
        base = pc.embedded_base(self.canary)
        head_part = self.canary.split(pc.BASE_BEGIN, 1)[0]
        self.assertEqual(
            pc.render(self.org_ci, base, pc.embedded_provider(head_part), ADMISSION),
            self.canary,
        )
        with redirect_stdout(io.StringIO()):
            self.assertEqual(0, pc.main(["--root", str(ROOT), "render", "--check"]))

    def test_the_head_leg_runs_org_ci_steps_verbatim_apart_from_the_target(
        self,
    ) -> None:
        steps = pc.analyze_steps(self.org_ci)
        for old, new in pc.REPLACEMENTS:
            steps = steps.replace(old, new.replace("@@SIDE@@", "head"))
        for old, new in pc.OPTIONAL_REPLACEMENTS:
            steps = steps.replace(old, new)
        # A reusable job's result is the only other rewrite: analyze reads it
        # from the inlined job's result step instead of `needs`.
        for name, _path, _sha, _values in pc.reusable_jobs(self.org_ci):
            steps = steps.replace(
                f"${{{{ needs.{name}.result }}}}",
                f"${{{{ steps.{name}__result.outputs.result }}}}",
            )
        self.assertIn(steps, self.canary)
        org = yaml.safe_load(self.org_ci)["jobs"]["analyze"]["steps"]
        head = leg_steps(self.canary, "head")
        # Inlined reusable jobs run first; analyze's own steps follow them.
        prefixes = tuple(f"{j[0]}__" for j in pc.reusable_jobs(self.org_ci))
        if prefixes:
            last = max(
                i
                for i, s in enumerate(head)
                if str(s.get("id", "")).startswith(prefixes)
                or str(s.get("name", "")).startswith("Clear the workspace")
            )
            head = head[last + 1 :]
        self.assertEqual(
            [s.get("uses") or s.get("name") for s in org],
            [s.get("uses") or s.get("name") for s in head[: len(org)]],
        )

    def test_the_base_leg_is_the_same_render_for_the_base_org_ci(self) -> None:
        block = pc.base_block(self.org_ci, security_provider)
        self.assertTrue(block.startswith(pc.BASE_BEGIN))
        self.assertTrue(block.endswith(pc.BASE_END))
        rendered = pc.render(self.org_ci, block, security_provider, ADMISSION)
        head = leg_steps(rendered, "head")
        base = leg_steps(rendered, "base")
        self.assertEqual(len(head), len(base))
        dumped = yaml.safe_dump(base)
        self.assertIn("canary-base-${{ matrix.slug }}", dumped)
        self.assertNotIn("canary-head-", dumped)
        record = next(s for s in base if s.get("name") == "Record the canary leg")
        upload = next(
            s for s in base if s.get("name") == "Upload the canary leg record"
        )
        self.assertEqual("base", record["env"]["SIDE"])
        self.assertEqual(pc.steps_digest(self.org_ci), record["env"]["DIGEST"])
        self.assertEqual(
            "promotion-canary-base-${{ matrix.slug }}", upload["with"]["name"]
        )

    def test_no_target_reference_survives(self) -> None:
        for job in ("head", "base"):
            text = yaml.safe_dump(leg_steps(self.canary, job))
            self.assertNotRegex(text, r"github\.(repository|sha|ref)\b")
            self.assertIn("matrix.repository", text)
            self.assertIn("matrix.revision", text)

    def test_a_new_target_reference_is_refused_not_carried(self) -> None:
        changed = self.org_ci.replace(
            "          REVISION: ${{ github.sha }}\n",
            "          REVISION: ${{ github.sha }}\n          BASE: ${{ github.base_ref }}\n",
            1,
        )
        with self.assertRaisesRegex(pc.CanaryError, "cannot retarget"):
            pc.leg_job("head", changed, security_provider)

    def test_a_vanished_target_reference_is_refused(self) -> None:
        changed = self.org_ci.replace("${{ github.ref }}", "refs/heads/main")
        with self.assertRaisesRegex(pc.CanaryError, "no longer contains"):
            pc.leg_job("head", changed, security_provider)

    def test_a_stale_or_baseless_canary_fails_the_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / pc.ORG_CI).parent.mkdir(parents=True)
            (root / pc.ORG_CI).write_text(self.org_ci, encoding="utf-8")
            write_admission(root)
            (root / pc.CANARY).write_text(self.canary + "# drift\n", encoding="utf-8")
            with redirect_stderr(io.StringIO()):
                self.assertEqual(1, pc.main(["--root", tmp, "render", "--check"]))
            without_base = self.canary.replace(pc.embedded_base(self.canary), "")
            (root / pc.CANARY).write_text(without_base, encoding="utf-8")
            with redirect_stderr(io.StringIO()):
                self.assertEqual(2, pc.main(["--root", tmp, "render", "--check"]))

    def test_every_job_is_read_only_and_the_verdict_decides_itself(self) -> None:
        jobs = yaml.safe_load(self.canary)["jobs"]
        # Each reusable org-ci job runs as a sibling job ahead of its leg (#327):
        # head and admitted from this org-ci.yml, base from the base org-ci.yml.
        own = [name for name, *_ in pc.reusable_jobs(self.org_ci)]
        base_siblings = [job for job in jobs if job.startswith("base__")]
        self.assertEqual(
            [
                "plan",
                *[f"head__{name}" for name in own],
                "head",
                *[f"admitted__{name}" for name in own],
                "admitted",
                *base_siblings,
                "base",
                "verdict",
            ],
            list(jobs),
        )
        for name, job in jobs.items():
            with self.subTest(job=name):
                self.assertEqual({"contents": "read"}, job["permissions"])
        self.assertEqual("${{ always() }}", jobs["verdict"]["if"])
        env = next(
            s for s in jobs["verdict"]["steps"] if s.get("name") == "Judge the canary"
        )["env"]
        for leg in ("head", "base", "admitted"):
            self.assertEqual(
                f"${{{{ needs.{leg}.result }}}}",
                env[f"L9_CANARY_{leg.upper()}_RESULT"],
            )
        self.assertEqual("Promotion canary (Core)", jobs["verdict"]["name"])
        self.assertEqual(["plan", "head", "admitted", "base"], jobs["verdict"]["needs"])
        for leg in ("head", "admitted", "base"):
            self.assertIs(False, jobs[leg]["strategy"]["fail-fast"])
        # A leg with sibling jobs needs them and runs whatever they concluded
        # (org-ci.yml enforces their results inside the leg); without
        # siblings it needs only the plan.
        for leg, condition in (
            ("head", pc.HEAD_IF),
            ("base", pc.BASE_IF),
            ("admitted", pc.ADMITTED_IF),
        ):
            with self.subTest(leg=leg):
                siblings = [job for job in jobs if job.startswith(f"{leg}__")]
                if siblings:
                    self.assertEqual(["plan", *siblings], jobs[leg]["needs"])
                    self.assertEqual(
                        f"${{{{ !cancelled() && ({condition}) }}}}", jobs[leg]["if"]
                    )
                else:
                    self.assertEqual("plan", jobs[leg]["needs"])
                    self.assertEqual(condition, jobs[leg]["if"])

    def test_the_verdict_downloads_both_legs_of_one_run(self) -> None:
        """The #312 failure: a differential plan must reach base-<slug> records.

        Both legs run in the pull request's own run and upload
        promotion-canary-<side>-<slug>; the verdict downloads that pattern from
        the same run, and judge() reads <side>-<slug>.json.
        """
        jobs = yaml.safe_load(self.canary)["jobs"]
        download = next(
            s
            for s in jobs["verdict"]["steps"]
            if s.get("name") == "Download the canary leg records"
        )
        self.assertEqual("promotion-canary-*", download["with"]["pattern"])
        self.assertTrue(download["with"]["merge-multiple"])
        self.assertNotIn("run-id", download["with"])
        for side in ("head", "base"):
            upload = next(
                s
                for s in jobs[side]["steps"]
                if s.get("name") == "Upload the canary leg record"
            )
            self.assertEqual(
                f"promotion-canary-{side}-${{{{ matrix.slug }}}}",
                upload["with"]["name"],
            )
            self.assertTrue(
                upload["with"]["path"].endswith(f"/{side}-${{{{ matrix.slug }}}}.json")
            )

    def test_no_paths_filter_so_the_check_always_reports(self) -> None:
        on = yaml.safe_load(self.canary)[True]
        self.assertNotIn("paths", on["pull_request"])
        self.assertNotIn("paths-ignore", on["pull_request"])


class ConsumerEventTests(unittest.TestCase):
    """Row 27: a consumer leg's make never runs under the Core PR's event."""

    def setUp(self) -> None:
        self.org_ci = (ROOT / pc.ORG_CI).read_text(encoding="utf-8")

    def verify_step(self, workflow: str, job: str) -> dict[str, object]:
        return next(
            s
            for s in yaml.safe_load(workflow)["jobs"][job]["steps"]
            if s.get("id") == "repository_verify"
        )

    def test_org_ci_runs_its_own_event_and_carries_every_optional_anchor(self) -> None:
        for old, _ in pc.OPTIONAL_REPLACEMENTS:
            with self.subTest(anchor=old):
                self.assertIn(old, self.org_ci)
        data = yaml.safe_load(self.org_ci)
        step = next(
            s
            for s in data["jobs"]["analyze"]["steps"]
            if s.get("id") == "repository_verify"
        )
        self.assertEqual("", step["with"]["event-name"])
        self.assertEqual("", step["with"]["event-ref"])

    def test_every_leg_verifies_its_consumer_as_a_push_of_the_checked_out_branch(
        self,
    ) -> None:
        committed = (ROOT / pc.CANARY).read_text(encoding="utf-8")
        rendered = pc.render(
            self.org_ci,
            pc.base_block(self.org_ci, security_provider),
            security_provider,
            ADMISSION,
        )
        cases = [(committed, "head"), (committed, "admitted")]
        cases += [(rendered, side) for side in ("head", "admitted", "base")]
        for workflow, side in cases:
            with self.subTest(side=side):
                inputs = self.verify_step(workflow, side)["with"]
                self.assertEqual("push", inputs["event-name"])
                self.assertEqual("refs/heads/${{ matrix.branch }}", inputs["event-ref"])

    def test_no_leg_resolves_a_merge_group_base(self) -> None:
        rendered = pc.render(
            self.org_ci,
            pc.base_block(self.org_ci, security_provider),
            security_provider,
            ADMISSION,
        )
        for side in ("head", "admitted", "base"):
            step = next(
                s
                for s in yaml.safe_load(rendered)["jobs"][side]["steps"]
                if s.get("id") == "baseline"
            )
            self.assertEqual("", step["env"]["MERGE_GROUP_BASE"])


class ReusableJobTests(unittest.TestCase):
    """A job org-ci.yml adds ahead of analyze is canaried with that job (#312)."""

    def setUp(self) -> None:
        self.org_ci = with_security((ROOT / pc.ORG_CI).read_text(encoding="utf-8"))
        self.workflow = pc.render(
            self.org_ci,
            pc.base_block(self.org_ci, security_provider),
            security_provider,
            ADMISSION,
        )
        self.steps = leg_steps(self.workflow, "head")

    def test_the_reusable_job_is_found_with_its_pin_and_inputs(self) -> None:
        self.assertEqual(
            [
                (
                    "security",
                    ".github/workflows/security.yml",
                    SECURITY_SHA,
                    {"gitleaks-scope": "event"},
                )
            ],
            pc.reusable_jobs(self.org_ci),
        )

    def test_each_leg_runs_the_security_job_as_a_sibling_ahead_of_analyze(
        self,
    ) -> None:
        jobs = yaml.safe_load(self.workflow)["jobs"]
        self.assertEqual(
            [
                "plan",
                "head__security",
                "head",
                "admitted__security",
                "admitted",
                "base__security",
                "base",
                "verdict",
            ],
            list(jobs),
        )
        for side, condition in (
            ("head", pc.HEAD_IF),
            ("admitted", pc.ADMITTED_IF),
            ("base", pc.BASE_IF),
        ):
            with self.subTest(side=side):
                sibling = jobs[f"{side}__security"]
                self.assertEqual("plan", sibling["needs"])
                self.assertEqual(condition, sibling["if"])
                self.assertEqual({"contents": "read"}, sibling["permissions"])
                self.assertIs(False, sibling["strategy"]["fail-fast"])
                upload = sibling["steps"][-1]
                self.assertEqual("always()", upload["if"])
                self.assertEqual(
                    f"canary-job-{side}-security-${{{{ matrix.slug }}}}",
                    upload["with"]["name"],
                )
                self.assertEqual("error", upload["with"]["if-no-files-found"])
                self.assertIn("${{ job.status }}", yaml.safe_dump(sibling["steps"][-2]))
                leg = jobs[side]
                self.assertEqual(["plan", f"{side}__security"], leg["needs"])
                self.assertEqual(f"${{{{ !cancelled() && ({condition}) }}}}", leg["if"])
                steps = leg["steps"]
                ids = [s.get("id") for s in steps]
                names = [s.get("name") for s in steps]
                download = steps[0]
                self.assertEqual(
                    f"canary-job-{side}-security-${{{{ matrix.slug }}}}",
                    download["with"]["pattern"],
                )
                self.assertNotIn("name", download["with"])
                result = ids.index("security__result")
                self.assertLess(
                    result, names.index("Checkout immutable event revision")
                )
                enforce = next(
                    s
                    for s in steps
                    if str(s.get("name", "")).startswith("Enforce security")
                )
                self.assertEqual(
                    "${{ steps.security__result.outputs.result }}",
                    enforce["env"]["SECURITY_RESULT"],
                )
                self.assertNotIn("security__gitleaks", ids)

    def test_a_missing_or_unknown_sibling_result_reads_as_failure(self) -> None:
        jobs = yaml.safe_load(self.workflow)["jobs"]
        step = next(
            s for s in jobs["head"]["steps"] if s.get("id") == "security__result"
        )
        script = step["run"]
        with tempfile.TemporaryDirectory() as directory:
            for written, expected in (
                (None, "failure"),
                ("", "failure"),
                ("success", "success"),
                ("failure", "failure"),
                ("cancelled", "cancelled"),
                ("skipped", "failure"),
            ):
                with self.subTest(written=written):
                    temp = Path(directory, str(written))
                    (temp / "canary-job").mkdir(parents=True)
                    if written is not None:
                        (temp / "canary-job" / "security.result").write_text(
                            written + "\n", encoding="utf-8"
                        )
                    output = temp / "output"
                    subprocess.run(
                        ["bash", "-c", script],
                        check=True,
                        capture_output=True,
                        env={
                            "RUNNER_TEMP": str(temp),
                            "GITHUB_OUTPUT": str(output),
                            "PATH": "/usr/bin:/bin",
                        },
                    )
                    self.assertEqual(
                        f"result={expected}\n", output.read_text(encoding="utf-8")
                    )

    def test_the_inlined_job_is_retargeted_and_isolated(self) -> None:
        jobs = yaml.safe_load(self.workflow)["jobs"]
        steps = jobs["head__security"]["steps"]
        by_id = {s.get("id"): s for s in steps}
        checkout = by_id["security__s0"]
        self.assertEqual("${{ matrix.repository }}", checkout["with"]["repository"])
        self.assertEqual("${{ matrix.revision }}", checkout["with"]["ref"])
        gitleaks = by_id["security__gitleaks"]["env"]
        self.assertEqual("push", gitleaks["EVENT_NAME"])
        self.assertEqual("${{ matrix.revision }}", gitleaks["EVENT_SHA"])
        self.assertEqual("", gitleaks["PUSH_BEFORE"])
        self.assertEqual("event", gitleaks["GITLEAKS_SCOPE"])
        text = yaml.safe_dump(steps)
        self.assertNotRegex(text, r"\binputs\.")
        self.assertNotIn("github.event.", text)
        self.assertNotIn("needs.security", yaml.safe_dump(jobs["head"]["steps"]))
        self.assertEqual(pc.SIBLING_TIMEOUT, jobs["head__security"]["timeout-minutes"])
        self.assertEqual(pc.LEG_TIMEOUT, jobs["head"]["timeout-minutes"])

    def test_no_step_or_job_is_allowed_to_fail_open(self) -> None:
        """Core forbids continue-on-error (test_workflow_integrity_clean)."""
        for workflow in (self.workflow, (ROOT / pc.CANARY).read_text("utf-8")):
            self.assertNotIn("continue-on-error", workflow)

    def test_the_check_reads_the_inlined_job_from_the_workflow_itself(self) -> None:
        head_part = self.workflow.split(pc.BASE_BEGIN, 1)[0]
        self.assertEqual(
            self.workflow,
            pc.render(
                self.org_ci,
                pc.embedded_base(self.workflow),
                pc.embedded_provider(head_part),
                ADMISSION,
            ),
        )
        provide = pc.embedded_provider("")
        with self.assertRaisesRegex(pc.CanaryError, "no inlined"):
            provide("security", ".github/workflows/security.yml", SECURITY_SHA, {})

    def test_unsupported_shapes_are_refused_not_left_out(self) -> None:
        for job in (
            "  other:\n    runs-on: ubuntu-24.04\n    steps: []\n",
            "  other:\n    uses: actions/other/.github/workflows/x.yml@v1\n",
            "  other:\n    uses: Quantum-L9/l9-ci-core/.github/workflows/x.yml@main\n",
            "  other:\n    uses: Quantum-L9/l9-ci-core/.github/workflows/x.yml@"
            + "2" * 40
            + "\n    secrets: inherit\n",
        ):
            with self.subTest(job=job.split("\n")[1]):
                org = self.org_ci.replace("\njobs:\n", "\njobs:\n" + job, 1)
                with self.assertRaises(pc.CanaryError):
                    pc.reusable_jobs(org)
        two_jobs = (ROOT / ".github/workflows/security.yml").read_text("utf-8") + (
            "  second:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: true\n"
        )
        with self.assertRaisesRegex(pc.CanaryError, "one-job"):
            pc.inline_job("security", two_jobs, {}, "x")
        unmapped = self.org_ci.replace(
            "${{ needs.security.result }}", "${{ needs.security.outputs.x }}"
        )
        with self.assertRaisesRegex(pc.CanaryError, "needs"):
            pc.leg_job("head", unmapped, security_provider)

    def test_adding_a_job_makes_the_change_differential(self) -> None:
        # org-ci.yml without #312's enforcement step: the base before the job.
        enforce = "\n      - name: Enforce security scan (secrets, dependency audit)\n"
        main = (ROOT / pc.ORG_CI).read_text(encoding="utf-8")
        main = main[: main.index(enforce)].rstrip("\n") + "\n"
        self.assertNotEqual(pc.steps_digest(main), pc.steps_digest(self.org_ci))
        header_only = "# comment\n" + main
        self.assertEqual(pc.steps_digest(main), pc.steps_digest(header_only))


class DeclarationTests(unittest.TestCase):
    def test_the_committed_declaration(self) -> None:
        entries = pc.load_declaration(ROOT)
        names = [e["repository"] for e in entries]
        self.assertEqual(
            [
                "Quantum-L9/Cursor-Governance",
                "Quantum-L9/Cognitive.Engine.Graphs",
                "Quantum-L9/Enrichment.Inference.Engine",
                "Quantum-L9/l9-ci-sdk",
                "Quantum-L9/l9-ci-core",
            ],
            names,
        )
        private = {e["repository"] for e in entries if e["private"]}
        self.assertEqual({"Quantum-L9/l9-ci-sdk"}, private)

    def test_invalid_declarations_are_refused(self) -> None:
        good = {"repository": "Quantum-L9/a", "branch": "main", "private": False}
        for repositories in (
            [],
            [{**good, "repository": "other/a"}],
            [{**good, "repository": "Quantum-L9/../x"}],
            [{**good, "branch": "../x"}],
            [{**good, "private": "no"}],
            [{**good, "extra": 1}],
            [{**good, "known_red": {"reason": "retired"}}],
            [good, {**good, "repository": "quantum-l9/A"}],
        ):
            with (
                self.subTest(repositories=repositories),
                tempfile.TemporaryDirectory() as tmp,
            ):
                path = Path(tmp) / pc.DECLARATION
                path.parent.mkdir(parents=True)
                path.write_text(
                    json.dumps({"schema": pc.SCHEMA, "repositories": repositories}),
                    encoding="utf-8",
                )
                with self.assertRaises(pc.CanaryError):
                    pc.load_declaration(Path(tmp))


def _git(tmp: str, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", tmp, *args], check=True, capture_output=True, text=True
    ).stdout.strip()


class PlanTests(unittest.TestCase):
    """The plan, against a real two-commit history (HEAD^1 is the base tip)."""

    def setUp(self) -> None:
        self.org_ci = (ROOT / pc.ORG_CI).read_text(encoding="utf-8")
        self.changed = self.org_ci + "      # a changed analyze step\n"

    def repo(self, tmp: str, head_org_ci: str, embedded_from: str) -> None:
        root = Path(tmp)
        _git(tmp, "init", "-q")
        _git(tmp, "config", "user.email", "t@example.invalid")
        _git(tmp, "config", "user.name", "t")
        for path in (pc.ORG_CI, pc.DECLARATION):
            (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / pc.DECLARATION).write_text(
            (ROOT / pc.DECLARATION).read_text("utf-8"), encoding="utf-8"
        )
        (root / pc.ORG_CI).write_text(self.org_ci, encoding="utf-8")
        write_admission(root)
        (root / pc.CANARY).write_text(
            pc.render(
                self.org_ci,
                pc.base_block(self.org_ci, security_provider),
                security_provider,
                ADMISSION,
            ),
            encoding="utf-8",
        )
        (root / "README.md").write_text("x\n", encoding="utf-8")
        _git(tmp, "add", ".")
        _git(tmp, "commit", "-qm", "base")
        (root / pc.ORG_CI).write_text(head_org_ci, encoding="utf-8")
        (root / pc.CANARY).write_text(
            pc.render(
                head_org_ci,
                pc.base_block(embedded_from, security_provider),
                security_provider,
                ADMISSION,
            ),
            encoding="utf-8",
        )
        (root / "README.md").write_text("y\n", encoding="utf-8")
        _git(tmp, "commit", "-qam", "change")

    def plan(self, tmp: str) -> dict[str, str]:
        out = io.StringIO()
        with (
            redirect_stdout(out),
            mock.patch.dict("os.environ", {"GITHUB_OUTPUT": ""}),
            mock.patch.object(pc, "_get", lambda url, token: {"sha": "a" * 40}),
            # The temporary history has no commit at org-ci.yml's security.yml
            # pin; the provider is an IO boundary, like _get.
            mock.patch.object(pc, "git_provider", return_value=security_provider),
        ):
            self.assertEqual(0, pc.main(["--root", tmp, "plan"]))
        return dict(
            line.split("=", 1) for line in out.getvalue().splitlines() if "=" in line
        )

    def test_watched_paths_cover_org_ci_and_every_action_it_calls(self) -> None:
        actions = pc.core_action_paths(self.org_ci)
        self.assertIn(".github/actions/run-repository-verification/", actions)
        self.assertIn(".github/actions/provision-sdk/", actions)
        self.assertEqual(
            [
                ".github/actions/provision-sdk/action.yml",
                ".github/workflows/org-ci.yml",
                ".l9/sdk-compatibility.yaml",
            ],
            pc.touched(
                [
                    ".github/workflows/org-ci.yml",
                    ".l9/sdk-compatibility.yaml",
                    ".github/actions/provision-sdk/action.yml",
                    ".github/actions/provision-sdkx/action.yml",
                    "docs/supply-chain.md",
                ],
                [*pc.STATIC_PATHS, *actions],
            ),
        )

    def test_a_change_outside_the_canary_paths_does_not_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # The second commit changes README.md only; the canary renders
            # byte-identically, so no canary path is touched.
            self.repo(tmp, self.org_ci, self.org_ci)
            self.assertEqual(
                "README.md", _git(tmp, "diff", "--name-only", "HEAD^1", "HEAD")
            )
            values = self.plan(tmp)
        self.assertEqual("false", values["applies"])

    def test_an_analyze_change_with_a_current_base_runs_both_legs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.repo(tmp, self.changed, self.org_ci)
            values = self.plan(tmp)
        self.assertEqual("true", values["applies"])
        self.assertEqual("true", values["differential"])
        self.assertEqual("true", values["base-current"])
        self.assertEqual(pc.steps_digest(self.org_ci), values["base-digest"])
        self.assertEqual(pc.steps_digest(self.changed), values["head-digest"])
        legs = json.loads(values["matrix"])["include"]
        self.assertEqual(5, len(legs))

    def test_a_stale_embedded_base_is_reported_not_run(self) -> None:
        stale = self.org_ci + "      # main moved since the render\n"
        with tempfile.TemporaryDirectory() as tmp:
            self.repo(tmp, self.changed, stale)
            values = self.plan(tmp)
        self.assertEqual("true", values["differential"])
        self.assertEqual("false", values["base-current"])

    def test_a_change_outside_the_analyze_steps_is_not_differential(self) -> None:
        header_only = "# header comment\n" + self.org_ci
        with tempfile.TemporaryDirectory() as tmp:
            self.repo(tmp, header_only, self.org_ci)
            values = self.plan(tmp)
        self.assertEqual("true", values["applies"])
        self.assertEqual("false", values["differential"])
        self.assertEqual("false", values["base-current"])
        self.assertEqual(values["head-digest"], values["base-digest"])

    def test_the_private_repository_is_resolved_with_the_private_token(self) -> None:
        seen = []

        def fake(url: str, token: str) -> dict[str, str]:
            seen.append((url.split("/repos/")[1].split("/commits")[0], token))
            return {"sha": "a" * 40}

        env = {"L9_CANARY_TOKEN": "public", "L9_CANARY_PRIVATE_TOKEN": "private"}
        with mock.patch.object(pc, "_get", fake):
            for entry in pc.load_declaration(ROOT):
                pc.resolve(entry, env)
        tokens = dict(seen)
        self.assertEqual("private", tokens["Quantum-L9/l9-ci-sdk"])
        self.assertEqual("public", tokens["Quantum-L9/l9-ci-core"])

    def test_an_unreadable_change_set_is_invalid_not_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _git(tmp, "init", "-q")
            for path in (pc.ORG_CI, pc.DECLARATION):
                (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / pc.DECLARATION).write_text(
                (ROOT / pc.DECLARATION).read_text("utf-8"), encoding="utf-8"
            )
            (root / pc.ORG_CI).write_text(self.org_ci, encoding="utf-8")
            with redirect_stderr(io.StringIO()):
                self.assertEqual(2, pc.main(["--root", tmp, "plan"]))


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


class VerdictTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.records = Path(self._tmp.name)
        self.legs = [
            {"repository": "Quantum-L9/x", "revision": "1" * 40, "slug": "x"},
            {"repository": "Quantum-L9/y", "revision": "2" * 40, "slug": "y"},
        ]

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def record(
        self,
        side: str,
        leg: dict[str, str],
        status: str,
        blocking: tuple[str, ...] = (),
        digest: str | None = None,
    ) -> None:
        if digest is None:
            digest = DIGEST_B if side == "head" else DIGEST_A
        (self.records / f"{side}-{leg['slug']}.json").write_text(
            json.dumps(
                {
                    "side": side,
                    "digest": digest,
                    "revision": leg["revision"],
                    "status": status,
                    "blocking_finding_ids": list(blocking),
                }
            ),
            encoding="utf-8",
        )

    def env(self, differential: bool = True, **extra: str) -> dict[str, str]:
        return {
            "L9_CANARY_PLAN_RESULT": "success",
            "L9_CANARY_APPLIES": "true",
            "L9_CANARY_DIFFERENTIAL": "true" if differential else "false",
            "L9_CANARY_BASE_CURRENT": "true" if differential else "false",
            "L9_CANARY_HEAD_CURRENT": "true",
            "L9_CANARY_HEAD_DIGEST": DIGEST_B if differential else DIGEST_A,
            "L9_CANARY_BASE_DIGEST": DIGEST_A,
            "L9_CANARY_MATRIX": json.dumps({"include": self.legs}),
            "L9_CANARY_RECORDS": str(self.records),
            # A leg job is red exactly when its org-ci run is red; the verdict
            # judges either from the records.
            "L9_CANARY_HEAD_RESULT": "failure",
            "L9_CANARY_BASE_RESULT": "success",
            "L9_CANARY_ADMITTED_RESULT": "success",
            **extra,
        }

    def both(self, base: str, head: str, **kwargs: tuple[str, ...]) -> None:
        for leg in self.legs:
            self.record("base", leg, base, kwargs.get("base_blocking", ()))
            self.record("head", leg, head, kwargs.get("head_blocking", ()))

    def test_not_applicable_passes_and_a_bad_plan_fails(self) -> None:
        self.assertTrue(pc.judge({"L9_CANARY_PLAN_RESULT": "success"})[0])
        self.assertFalse(pc.judge({"L9_CANARY_PLAN_RESULT": "failure"})[0])
        self.assertFalse(pc.judge(self.env(L9_CANARY_MATRIX="{bad"))[0])
        self.assertFalse(pc.judge(self.env(L9_CANARY_HEAD_DIGEST="nope"))[0])
        contradicting = self.env(L9_CANARY_HEAD_DIGEST=DIGEST_A)
        self.assertFalse(pc.judge(contradicting)[0])

    def test_kept_green_and_kept_red_pass_and_print_the_table(self) -> None:
        self.both("pass", "pass")
        passed, report = pc.judge(self.env())
        self.assertTrue(passed)
        self.assertIn(
            "| Repository | Head | Base pins | Pull request pins | Verdict |", report
        )
        self.both("findings", "findings", base_blocking=("f1",), head_blocking=("f1",))
        passed, report = pc.judge(self.env())
        self.assertTrue(passed)
        self.assertTrue(any("kept: already red" in line for line in report))

    def test_an_improvement_passes(self) -> None:
        self.both("findings", "pass", base_blocking=("f1",))
        passed, report = pc.judge(self.env())
        self.assertTrue(passed)
        self.assertTrue(any("improved" in line for line in report))

    def test_a_regression_fails(self) -> None:
        self.both("pass", "findings", head_blocking=("f1",))
        passed, report = pc.judge(self.env())
        self.assertFalse(passed)
        self.assertTrue(any("regression: pass -> findings" in line for line in report))

    def test_a_new_blocking_finding_fails_even_when_both_are_red(self) -> None:
        self.both(
            "findings", "findings", base_blocking=("f1",), head_blocking=("f1", "f2")
        )
        passed, report = pc.judge(self.env())
        self.assertFalse(passed)
        self.assertTrue(any("1 new blocking finding(s): f2" in line for line in report))

    def test_a_new_verification_failure_fails(self) -> None:
        self.both("findings", "verification", base_blocking=("f1",))
        self.assertFalse(pc.judge(self.env())[0])

    def test_an_enforcement_failure_is_a_real_failure(self) -> None:
        self.both("pass", "enforcement")
        passed, report = pc.judge(self.env())
        self.assertFalse(passed)
        self.assertTrue(any("regression: pass -> enforcement" in r for r in report))
        self.both("findings", "enforcement", base_blocking=("f1",))
        self.assertFalse(pc.judge(self.env())[0])
        self.both("enforcement", "enforcement")
        self.assertTrue(pc.judge(self.env())[0])

    def test_no_verdict_at_the_pull_request_pins_always_fails(self) -> None:
        for status in ("incomplete", "cancelled", "error"):
            with self.subTest(status=status):
                self.both("findings", status)
                passed, report = pc.judge(self.env())
                self.assertFalse(passed)
                self.assertTrue(
                    any(f"{status} at the pull request's pins" in r for r in report)
                )
                self.both(status, status)
                self.assertFalse(pc.judge(self.env(differential=False))[0])

    def test_a_stale_inlined_job_fails_with_the_fix(self) -> None:
        self.both("pass", "pass")
        passed, report = pc.judge(self.env(L9_CANARY_HEAD_CURRENT="false"))
        self.assertFalse(passed)
        self.assertIn("inlined org-ci jobs", report[0])

    def test_a_stale_embedded_base_fails_with_the_fix(self) -> None:
        self.both("pass", "pass")
        passed, report = pc.judge(self.env(L9_CANARY_BASE_CURRENT="false"))
        self.assertFalse(passed)
        self.assertIn("promotion_canary.py render", report[0])

    def test_no_baseline_fails_closed(self) -> None:
        for leg in self.legs:
            self.record("head", leg, "pass")
        passed, report = pc.judge(self.env())
        self.assertFalse(passed)
        self.assertTrue(any("no base-pins leg record" in line for line in report))
        for leg in self.legs:
            self.record("base", leg, "pass", digest=DIGEST_B)
        passed, report = pc.judge(self.env())
        self.assertFalse(passed)
        self.assertTrue(any("did not run the base's steps" in r for r in report))
        for leg in self.legs:
            self.record("base", leg, "incomplete")
        self.assertFalse(pc.judge(self.env())[0])

    def test_unchanged_steps_reuse_the_pull_request_result_as_the_base(self) -> None:
        for leg in self.legs:
            self.record("head", leg, "findings", ("f1",), digest=DIGEST_A)
        passed, report = pc.judge(self.env(differential=False))
        self.assertTrue(passed)
        self.assertTrue(any("unchanged" in line for line in report))

    def test_a_missing_or_foreign_record_fails(self) -> None:
        self.both("pass", "pass")
        (self.records / "head-y.json").unlink()
        self.assertFalse(pc.judge(self.env())[0])
        self.record("head", self.legs[1], "pass")
        self.record("head", {**self.legs[1], "revision": "f" * 40}, "pass")
        self.assertFalse(pc.judge(self.env())[0])
        self.record("head", self.legs[1], "pass", digest=DIGEST_A)
        passed, report = pc.judge(self.env())
        self.assertFalse(passed)
        self.assertTrue(any("other steps than the plan" in line for line in report))

    def test_a_red_org_ci_run_is_a_red_leg_and_the_verdict_blocks(self) -> None:
        """No continue-on-error: a red org-ci run fails its leg job; the record
        says why and the verdict, the only gate, blocks the regression."""
        canary = (ROOT / pc.CANARY).read_text(encoding="utf-8")
        self.assertNotIn("continue-on-error", canary)
        self.both("pass", "findings", head_blocking=("f1",))
        passed, report = pc.judge(self.env(L9_CANARY_HEAD_RESULT="failure"))
        self.assertFalse(passed)
        self.assertTrue(any("regression: pass -> findings" in r for r in report))

    def test_a_leg_without_a_record_blocks(self) -> None:
        canary = (ROOT / pc.CANARY).read_text(encoding="utf-8")
        for side in ("head", "admitted", "base"):
            upload = next(
                s
                for s in yaml.safe_load(canary)["jobs"][side]["steps"]
                if s.get("name") == "Upload the canary leg record"
            )
            self.assertEqual("always()", upload["if"])
            self.assertEqual("error", upload["with"]["if-no-files-found"])
        self.both("pass", "pass")
        (self.records / "head-x.json").unlink()
        passed, report = pc.judge(self.env(L9_CANARY_HEAD_RESULT="failure"))
        self.assertFalse(passed)
        self.assertTrue(any("no pull-request leg record" in r for r in report))

    def test_a_cancelled_or_unrun_leg_is_incomplete_and_blocks(self) -> None:
        self.both("pass", "pass")
        for side in ("HEAD", "BASE"):
            for result in ("cancelled", "skipped", ""):
                with self.subTest(side=side, result=result):
                    passed, report = pc.judge(
                        self.env(**{f"L9_CANARY_{side}_RESULT": result})
                    )
                    self.assertFalse(passed)
                    self.assertIn("incomplete", report[0])
        # A base leg that is not planned is not required.
        for leg in self.legs:
            self.record("head", leg, "pass", digest=DIGEST_A)
        self.assertTrue(
            pc.judge(self.env(differential=False, L9_CANARY_BASE_RESULT="skipped"))[0]
        )

    def test_the_leg_record_classifies_the_failure(self) -> None:
        canary = (ROOT / pc.CANARY).read_text(encoding="utf-8")
        record = canary[canary.index("name: Record the canary leg") :]
        for status in (
            "pass",
            "enforcement",
            "cancelled",
            "incomplete",
            "verification",
            "findings",
            "error",
        ):
            self.assertIn(f'status = "{status}"', record)
        self.assertIn("blocking_finding_ids", record)
        self.assertIn(
            f"DIGEST: {pc.steps_digest((ROOT / pc.ORG_CI).read_text('utf-8'))}", record
        )

    def test_the_verdict_command_exit_codes(self) -> None:
        self.both("pass", "findings", head_blocking=("f1",))
        with (
            redirect_stdout(io.StringIO()),
            mock.patch.dict("os.environ", {**self.env(), "GITHUB_STEP_SUMMARY": ""}),
        ):
            self.assertEqual(1, pc.main(["verdict"]))
            self.both("pass", "pass")
            self.assertEqual(0, pc.main(["verdict"]))


ADMITTED = "f" * 40


def with_default(compatibility: str, revision: str) -> str:
    """The compatibility manifest with another default revision."""
    current = pc.DEFAULT_REVISION.findall(compatibility)[0]
    head, tail = compatibility.split("\ndefault:\n", 1)
    return head + "\ndefault:\n" + tail.replace(current, revision, 1)


class AdmittedLegTests(unittest.TestCase):
    """Row 25: an admission-only pull request runs the revision it admits."""

    def setUp(self) -> None:
        self.org_ci = (ROOT / pc.ORG_CI).read_text(encoding="utf-8")
        self.canary = (ROOT / pc.CANARY).read_text(encoding="utf-8")
        self.jobs = yaml.safe_load(self.canary)["jobs"]

    def fallback(self) -> str:
        return pc.SDK_FALLBACK.findall(self.org_ci)[0]

    def test_nothing_is_admitted_while_org_ci_runs_the_default(self) -> None:
        running = with_default(COMPATIBILITY, self.fallback())
        self.assertEqual("", pc.admitted_revision(self.org_ci, running))

    def test_a_new_default_is_the_admitted_revision(self) -> None:
        changed = with_default(COMPATIBILITY, ADMITTED)
        self.assertEqual(ADMITTED, pc.admitted_revision(self.org_ci, changed))

    def test_an_unreadable_admission_is_refused(self) -> None:
        with self.assertRaisesRegex(pc.CanaryError, "one default revision"):
            pc.admitted_revision(self.org_ci, "schema: x\n")
        no_fallback = pc.SDK_FALLBACK.sub("''", self.org_ci)
        with self.assertRaisesRegex(pc.CanaryError, "one sdk-revision fallback"):
            pc.admitted_revision(no_fallback, COMPATIBILITY)

    def test_the_admitted_leg_is_the_head_leg_but_for_provision_sdk(self) -> None:
        head = self.jobs["head"]["steps"]
        admitted = self.jobs["admitted"]["steps"]
        index = next(i for i, s in enumerate(head) if s.get("id") == "sdk")

        def same(steps: list[dict[str, object]]) -> str:
            # Each leg names its own artifact set; nothing else may differ.
            return (
                yaml.safe_dump(steps)
                .replace("canary-admitted-", "canary-head-")
                .replace("canary-job-admitted-", "canary-job-head-")
            )

        self.assertEqual(same(head[:index]), same(admitted[:index]))
        # The provision-sdk step becomes download + its own composite steps.
        replacement = len(pc._composite_steps(ADMISSION)) + 1
        # The last two steps record and upload the leg under its own side.
        self.assertEqual(
            same(head[index + 1 : -2]), same(admitted[index + replacement : -2])
        )
        self.assertEqual("admitted", admitted[-2]["env"]["SIDE"])
        download, *inner = admitted[index : index + replacement]
        self.assertEqual(pc.ADMISSION_ARTIFACT, download["with"]["name"])
        self.assertEqual(pc.ADMITTED_CORE, download["with"]["path"])
        provision = inner[-1]
        self.assertEqual("sdk", provision["id"])
        self.assertEqual(head[index]["if"], provision["if"])
        self.assertEqual(
            "${{ needs.plan.outputs.admitted }}", provision["env"]["INPUT_SDK_REVISION"]
        )
        self.assertEqual(
            "${{ secrets.L9_GITHUB_READ_TOKEN }}",
            provision["env"]["INPUT_GITHUB_READ_TOKEN"],
        )
        self.assertIn(
            f"{pc.ADMITTED_CORE}/.github/actions/provision-sdk/provision.py",
            provision["run"],
        )
        text = yaml.safe_dump(admitted)
        self.assertNotIn("/.github/actions/provision-sdk@", text)
        self.assertIn("canary-admitted-${{ matrix.slug }}", text)
        self.assertNotIn("inputs.", yaml.safe_dump([download, *inner]))

    def test_the_plan_uploads_the_pull_requests_provisioner(self) -> None:
        plan = self.jobs["plan"]
        self.assertEqual(
            "${{ steps.plan.outputs.admitted }}", plan["outputs"]["admitted"]
        )
        upload = next(
            s for s in plan["steps"] if "upload-artifact" in s.get("uses", "")
        )
        self.assertEqual("steps.plan.outputs.admitted != ''", upload["if"])
        self.assertEqual(pc.ADMISSION_ARTIFACT, upload["with"]["name"])
        self.assertIs(True, upload["with"]["include-hidden-files"])
        self.assertEqual(
            [".github/actions/provision-sdk/", ".l9/sdk-compatibility.yaml"],
            upload["with"]["path"].split(),
        )
        # Not a leg record: the verdict's download pattern never matches it.
        self.assertFalse(pc.ADMISSION_ARTIFACT.startswith("promotion-canary-"))

    def test_an_unmappable_provisioner_is_refused(self) -> None:
        analyze = pc.analyze_steps(self.org_ci)
        renamed = ADMISSION.replace(
            "steps.provision.outputs.contract", "steps.other.outputs.contract"
        )
        with self.assertRaisesRegex(pc.CanaryError, "one-to-one"):
            pc.admit(analyze, renamed)
        extra = ADMISSION.replace("${{ inputs.sdk-source }}", "${{ inputs.missing }}")
        with self.assertRaisesRegex(pc.CanaryError, "cannot map"):
            pc.admit(analyze, extra)
        no_sdk = analyze.replace("      - id: sdk\n", "      - id: other\n")
        with self.assertRaisesRegex(pc.CanaryError, "exactly one `id: sdk`"):
            pc.admit(no_sdk, ADMISSION)

    def test_the_plan_names_the_admitted_revision(self) -> None:
        for compatibility, expected in (
            (with_default(COMPATIBILITY, self.fallback()), ""),
            (with_default(COMPATIBILITY, ADMITTED), ADMITTED),
        ):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as tmp:
                planner = PlanTests()
                planner.org_ci = self.org_ci
                planner.repo(tmp, self.org_ci + "      # changed\n", self.org_ci)
                (Path(tmp) / pc.SDK_COMPATIBILITY).write_text(
                    compatibility, encoding="utf-8"
                )
                values = planner.plan(tmp)
                self.assertEqual("true", values["applies"])
                self.assertEqual(expected, values["admitted"])


class AdmittedVerdictTests(VerdictTests):
    """The admitted leg is judged against the pull request's own pins."""

    def admit(self, status: str, blocking: tuple[str, ...] = ()) -> None:
        for leg in self.legs:
            self.record("admitted", leg, status, blocking, digest=DIGEST_B)

    def test_an_admitted_revision_that_fixes_a_finding_passes(self) -> None:
        self.both("findings", "findings", base_blocking=("f1",), head_blocking=("f1",))
        self.admit("pass")
        passed, report = pc.judge(self.env(L9_CANARY_ADMITTED=ADMITTED))
        self.assertTrue(passed)
        self.assertIn(
            "| Repository | Pull request pins | Admitted SDK | Verdict |", report
        )
        self.assertTrue(any("| findings | pass | pass: improved" in r for r in report))

    def test_an_admitted_revision_that_regresses_fails(self) -> None:
        self.both("pass", "pass")
        self.admit("findings", ("f9",))
        passed, report = pc.judge(self.env(L9_CANARY_ADMITTED=ADMITTED))
        self.assertFalse(passed)
        self.assertTrue(any("regression: pass -> findings" in r for r in report))

    def test_a_new_finding_under_the_admitted_revision_fails(self) -> None:
        self.both("findings", "findings", base_blocking=("f1",), head_blocking=("f1",))
        self.admit("findings", ("f1", "f2"))
        self.assertFalse(pc.judge(self.env(L9_CANARY_ADMITTED=ADMITTED))[0])

    def test_a_missing_or_incomplete_admitted_leg_fails(self) -> None:
        self.both("pass", "pass")
        self.assertFalse(pc.judge(self.env(L9_CANARY_ADMITTED=ADMITTED))[0])
        self.admit("incomplete")
        self.assertFalse(pc.judge(self.env(L9_CANARY_ADMITTED=ADMITTED))[0])

    def test_an_admitted_leg_is_judged_when_the_steps_are_unchanged(self) -> None:
        for leg in self.legs:
            self.record("head", leg, "findings", ("f1",), digest=DIGEST_A)
            self.record("admitted", leg, "pass", digest=DIGEST_A)
        passed, report = pc.judge(
            self.env(differential=False, L9_CANARY_ADMITTED=ADMITTED)
        )
        self.assertTrue(passed)
        self.assertTrue(any("improved" in r for r in report))

    def test_no_admission_judges_no_admitted_leg(self) -> None:
        self.both("pass", "pass")
        passed, report = pc.judge(self.env())
        self.assertTrue(passed)
        self.assertFalse(any("Admitted SDK" in r for r in report))

    def test_a_malformed_admitted_revision_fails_the_plan(self) -> None:
        self.both("pass", "pass")
        self.admit("pass")
        self.assertFalse(pc.judge(self.env(L9_CANARY_ADMITTED="main"))[0])


if __name__ == "__main__":
    unittest.main()
