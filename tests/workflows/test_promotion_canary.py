"""Core's promotion canary (``tools/promotion_canary.py``, AGENTS.md §40).

The generated workflow is compared byte for byte with what the tool renders from
the current ``org-ci.yml``; the plan, the applicability decision and the verdict
run here against real files, a real git history and recorded legs.
"""

from __future__ import annotations

import importlib.util
import io
import json
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


def leg_steps(workflow: str, job: str) -> list[dict[str, object]]:
    return yaml.safe_load(workflow)["jobs"][job]["steps"]


class RenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.org_ci = (ROOT / pc.ORG_CI).read_text(encoding="utf-8")
        self.canary = (ROOT / pc.CANARY).read_text(encoding="utf-8")

    def test_the_committed_canary_is_the_render_of_org_ci(self) -> None:
        base = pc.embedded_base(self.canary)
        self.assertEqual(pc.render(self.org_ci, base), self.canary)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(0, pc.main(["--root", str(ROOT), "render", "--check"]))

    def test_the_head_leg_runs_org_ci_steps_verbatim_apart_from_the_target(
        self,
    ) -> None:
        steps = pc.analyze_steps(self.org_ci)
        for old, new in pc.REPLACEMENTS:
            steps = steps.replace(old, new.replace("@@SIDE@@", "head"))
        self.assertIn(steps, self.canary)
        org = yaml.safe_load(self.org_ci)["jobs"]["analyze"]["steps"]
        head = leg_steps(self.canary, "head")
        self.assertEqual(
            [s.get("uses") or s.get("name") for s in org],
            [s.get("uses") or s.get("name") for s in head[: len(org)]],
        )

    def test_the_base_leg_is_the_same_render_for_the_base_org_ci(self) -> None:
        block = pc.base_block(self.org_ci)
        self.assertTrue(block.startswith(pc.BASE_BEGIN))
        self.assertTrue(block.endswith(pc.BASE_END))
        rendered = pc.render(self.org_ci, block)
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
            pc.leg_job("head", changed)

    def test_a_vanished_target_reference_is_refused(self) -> None:
        changed = self.org_ci.replace("${{ github.ref }}", "refs/heads/main")
        with self.assertRaisesRegex(pc.CanaryError, "no longer contains"):
            pc.leg_job("head", changed)

    def test_a_stale_or_baseless_canary_fails_the_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / pc.ORG_CI).parent.mkdir(parents=True)
            (root / pc.ORG_CI).write_text(self.org_ci, encoding="utf-8")
            (root / pc.CANARY).write_text(self.canary + "# drift\n", encoding="utf-8")
            with redirect_stderr(io.StringIO()):
                self.assertEqual(1, pc.main(["--root", tmp, "render", "--check"]))
            without_base = self.canary.replace(pc.embedded_base(self.canary), "")
            (root / pc.CANARY).write_text(without_base, encoding="utf-8")
            with redirect_stderr(io.StringIO()):
                self.assertEqual(2, pc.main(["--root", tmp, "render", "--check"]))

    def test_every_job_is_read_only_and_the_verdict_decides_itself(self) -> None:
        jobs = yaml.safe_load(self.canary)["jobs"]
        self.assertEqual(["plan", "head", "base", "verdict"], list(jobs))
        for name, job in jobs.items():
            with self.subTest(job=name):
                self.assertEqual({"contents": "read"}, job["permissions"])
        self.assertEqual("${{ !cancelled() }}", jobs["verdict"]["if"])
        self.assertEqual("Promotion canary (Core)", jobs["verdict"]["name"])
        self.assertEqual(["plan", "head", "base"], jobs["verdict"]["needs"])
        for leg in ("head", "base"):
            self.assertIs(False, jobs[leg]["strategy"]["fail-fast"])
        self.assertEqual(pc.HEAD_IF, jobs["head"]["if"])
        self.assertEqual(pc.BASE_IF, jobs["base"]["if"])

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
        (root / pc.CANARY).write_text(
            pc.render(self.org_ci, pc.base_block(self.org_ci)), encoding="utf-8"
        )
        (root / "README.md").write_text("x\n", encoding="utf-8")
        _git(tmp, "add", ".")
        _git(tmp, "commit", "-qm", "base")
        (root / pc.ORG_CI).write_text(head_org_ci, encoding="utf-8")
        (root / pc.CANARY).write_text(
            pc.render(head_org_ci, pc.base_block(embedded_from)), encoding="utf-8"
        )
        (root / "README.md").write_text("y\n", encoding="utf-8")
        _git(tmp, "commit", "-qam", "change")

    def plan(self, tmp: str) -> dict[str, str]:
        out = io.StringIO()
        with (
            redirect_stdout(out),
            mock.patch.dict("os.environ", {"GITHUB_OUTPUT": ""}),
            mock.patch.object(pc, "_get", lambda url, token: {"sha": "a" * 40}),
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
            "L9_CANARY_HEAD_DIGEST": DIGEST_B if differential else DIGEST_A,
            "L9_CANARY_BASE_DIGEST": DIGEST_A,
            "L9_CANARY_MATRIX": json.dumps({"include": self.legs}),
            "L9_CANARY_RECORDS": str(self.records),
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


if __name__ == "__main__":
    unittest.main()
