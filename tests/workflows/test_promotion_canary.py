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


class RenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.org_ci = (ROOT / pc.ORG_CI).read_text(encoding="utf-8")
        self.canary = (ROOT / pc.CANARY).read_text(encoding="utf-8")

    def test_the_committed_canary_is_the_render_of_org_ci(self) -> None:
        self.assertEqual(pc.render(self.org_ci), self.canary)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(0, pc.main(["--root", str(ROOT), "render", "--check"]))

    def test_the_canary_runs_org_ci_steps_verbatim_apart_from_the_target(self) -> None:
        steps = pc.analyze_steps(self.org_ci)
        for old, new in pc.REPLACEMENTS:
            steps = steps.replace(old, new)
        self.assertIn(steps, self.canary)
        org = yaml.safe_load(self.org_ci)["jobs"]["analyze"]["steps"]
        canary = yaml.safe_load(self.canary)["jobs"]["canary"]["steps"]
        self.assertEqual(
            [s.get("uses") or s.get("name") for s in org],
            [s.get("uses") or s.get("name") for s in canary[: len(org)]],
        )

    def test_no_target_reference_survives(self) -> None:
        canary = yaml.safe_load(self.canary)["jobs"]["canary"]
        text = yaml.safe_dump(canary["steps"])
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
            pc.render(changed)

    def test_a_vanished_target_reference_is_refused(self) -> None:
        changed = self.org_ci.replace("${{ github.ref }}", "refs/heads/main")
        with self.assertRaisesRegex(pc.CanaryError, "no longer contains"):
            pc.render(changed)

    def test_a_stale_canary_fails_the_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / pc.ORG_CI).parent.mkdir(parents=True)
            (root / pc.ORG_CI).write_text(self.org_ci, encoding="utf-8")
            (root / pc.CANARY).write_text(self.canary + "# drift\n", encoding="utf-8")
            with redirect_stderr(io.StringIO()):
                self.assertEqual(1, pc.main(["--root", tmp, "render", "--check"]))
            with redirect_stdout(io.StringIO()):
                self.assertEqual(0, pc.main(["--root", tmp, "render"]))
            self.assertEqual(self.canary, (root / pc.CANARY).read_text("utf-8"))

    def test_the_verdict_always_runs_and_the_legs_never_stop_each_other(self) -> None:
        jobs = yaml.safe_load(self.canary)["jobs"]
        self.assertEqual("${{ !cancelled() }}", jobs["verdict"]["if"])
        self.assertEqual("Promotion canary (Core)", jobs["verdict"]["name"])
        self.assertEqual(["plan", "canary"], jobs["verdict"]["needs"])
        self.assertIs(False, jobs["canary"]["strategy"]["fail-fast"])
        for job in jobs.values():
            self.assertEqual({"contents": "read"}, job["permissions"])

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
            [{**good, "known_red": {"reason": " "}}],
            [{**good, "known_red": "red"}],
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


class PlanTests(unittest.TestCase):
    def test_watched_paths_cover_org_ci_and_every_action_it_calls(self) -> None:
        org_ci = (ROOT / pc.ORG_CI).read_text(encoding="utf-8")
        actions = pc.core_action_paths(org_ci)
        self.assertIn(".github/actions/run-repository-verification/", actions)
        self.assertIn(".github/actions/provision-sdk/", actions)
        watched = [*pc.STATIC_PATHS, *actions]
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
                watched,
            ),
        )

    def _repo(self, tmp: str) -> tuple[str, str]:
        def git(*args: str) -> str:
            return subprocess.run(
                ["git", "-C", tmp, *args], check=True, capture_output=True, text=True
            ).stdout.strip()

        git("init", "-q")
        git("config", "user.email", "t@example.invalid")
        git("config", "user.name", "t")
        root = Path(tmp)
        (root / pc.ORG_CI).parent.mkdir(parents=True)
        (root / pc.ORG_CI).write_text(
            (ROOT / pc.ORG_CI).read_text("utf-8"), encoding="utf-8"
        )
        (root / pc.DECLARATION).parent.mkdir(parents=True)
        (root / pc.DECLARATION).write_text(
            (ROOT / pc.DECLARATION).read_text("utf-8"), encoding="utf-8"
        )
        (root / "README.md").write_text("x\n", encoding="utf-8")
        git("add", ".")
        git("commit", "-qm", "base")
        base = git("rev-parse", "HEAD")
        (root / "README.md").write_text("y\n", encoding="utf-8")
        git("commit", "-qam", "docs")
        return base, git("rev-parse", "HEAD")

    def test_a_change_outside_the_canary_paths_does_not_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(tmp)
            out = io.StringIO()
            with (
                redirect_stdout(out),
                mock.patch.dict("os.environ", {"GITHUB_OUTPUT": ""}),
            ):
                code = pc.main(["--root", tmp, "plan"])
            self.assertEqual(0, code)
            self.assertIn("applies=false", out.getvalue())

    def test_a_change_to_org_ci_plans_every_declared_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(tmp)
            org = Path(tmp) / pc.ORG_CI
            org.write_text(org.read_text("utf-8") + "# change\n", encoding="utf-8")
            subprocess.run(["git", "-C", tmp, "commit", "-qam", "org"], check=True)
            resolved = []

            def fake_get(url: str, token: str) -> dict[str, str]:
                resolved.append((url, token))
                return {"sha": "a" * 40}

            out = io.StringIO()
            env = {
                "GITHUB_OUTPUT": "",
                "L9_CANARY_TOKEN": "public",
                "L9_CANARY_PRIVATE_TOKEN": "private",
            }
            with (
                redirect_stdout(out),
                mock.patch.dict("os.environ", env),
                mock.patch.object(pc, "_get", fake_get),
            ):
                code = pc.main(["--root", tmp, "plan"])
            self.assertEqual(0, code)
            values = dict(
                line.split("=", 1)
                for line in out.getvalue().splitlines()
                if "=" in line
            )
            self.assertEqual("true", values["applies"])
            legs = json.loads(values["matrix"])["include"]
            self.assertEqual(5, len(legs))
            self.assertTrue(all(leg["revision"] == "a" * 40 for leg in legs))
            tokens = {
                url.split("/repos/")[1].split("/commits")[0]: t for url, t in resolved
            }
            self.assertEqual("private", tokens["Quantum-L9/l9-ci-sdk"])
            self.assertEqual("public", tokens["Quantum-L9/l9-ci-core"])
            self.assertTrue(
                next(leg for leg in legs if "Cursor-Governance" in leg["repository"])[
                    "known_red"
                ]
            )

    def test_an_unreadable_change_set_is_invalid_not_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "-C", tmp, "init", "-q"], check=True)
            (Path(tmp) / pc.DECLARATION).parent.mkdir(parents=True)
            (Path(tmp) / pc.DECLARATION).write_text(
                (ROOT / pc.DECLARATION).read_text("utf-8"), encoding="utf-8"
            )
            (Path(tmp) / pc.ORG_CI).parent.mkdir(parents=True)
            (Path(tmp) / pc.ORG_CI).write_text(
                (ROOT / pc.ORG_CI).read_text("utf-8"), encoding="utf-8"
            )
            with redirect_stderr(io.StringIO()):
                code = pc.main(["--root", tmp, "plan"])
            self.assertEqual(2, code)


class VerdictTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.records = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def leg(self, slug: str, known_red: str = "") -> dict[str, str]:
        return {
            "repository": "Quantum-L9/" + slug,
            "revision": slug[0] * 40,
            "slug": slug,
            "known_red": known_red,
        }

    def record(self, leg: dict[str, str], status: str) -> None:
        (self.records / f"{leg['slug']}.json").write_text(
            json.dumps({"revision": leg["revision"], "status": status}),
            encoding="utf-8",
        )

    def judge(self, legs: list[dict[str, str]]) -> tuple[bool, list[str]]:
        return pc.judge("success", "true", json.dumps({"include": legs}), self.records)

    def test_not_applicable_passes_and_a_failed_plan_fails(self) -> None:
        self.assertTrue(pc.judge("success", "false", "", self.records)[0])
        self.assertFalse(pc.judge("failure", "", "", self.records)[0])
        self.assertFalse(pc.judge("success", "true", "{bad", self.records)[0])
        self.assertFalse(self.judge([])[0])

    def test_green_legs_pass(self) -> None:
        legs = [self.leg("a"), self.leg("b")]
        for leg in legs:
            self.record(leg, "pass")
        self.assertTrue(self.judge(legs)[0])

    def test_incomplete_fails_even_where_red_is_declared(self) -> None:
        leg = self.leg("a", known_red="findings")
        self.record(leg, "incomplete")
        passed, report = self.judge([leg])
        self.assertFalse(passed)
        self.assertIn("incomplete", report[0])

    def test_an_infrastructure_error_is_never_masked_and_is_named(self) -> None:
        leg = self.leg("a", known_red="findings")
        self.record(leg, "error")
        passed, report = self.judge([leg])
        self.assertFalse(passed)
        self.assertIn("infrastructure error", report[0])
        self.record(leg, "bogus")
        self.assertFalse(self.judge([leg])[0])

    def test_a_real_failure_is_named_as_one(self) -> None:
        for status in ("findings", "verification"):
            with self.subTest(status=status):
                leg = self.leg("a")
                self.record(leg, status)
                passed, report = self.judge([leg])
                self.assertFalse(passed)
                self.assertIn("a real failure of this repository", report[0])

    def test_the_leg_record_classifies_the_failure(self) -> None:
        canary = (ROOT / pc.CANARY).read_text(encoding="utf-8")
        record = canary[canary.index("name: Record the canary leg") :]
        for status in (
            '"pass"',
            '"incomplete"',
            '"verification"',
            '"findings"',
            '"error"',
        ):
            self.assertIn(f"status = {status}", record)

    def test_a_missing_or_foreign_record_fails(self) -> None:
        a, b = self.leg("a"), self.leg("b")
        self.record(a, "pass")
        self.assertFalse(self.judge([a, b])[0])
        (self.records / "b.json").write_text(
            json.dumps({"revision": "f" * 40, "status": "pass"}), encoding="utf-8"
        )
        self.assertFalse(self.judge([a, b])[0])

    def test_red_fails_unless_declared_and_declared_red_is_announced(self) -> None:
        plain, red = self.leg("a"), self.leg("b", known_red="findings")
        self.record(plain, "findings")
        self.record(red, "findings")
        passed, report = self.judge([plain, red])
        self.assertFalse(passed)
        self.assertTrue(self.judge([red])[0])
        self.assertTrue(any(line.startswith("known-red") for line in report))

    def test_a_declared_red_repository_that_turns_green_is_reported(self) -> None:
        red = self.leg("a", known_red="findings")
        self.record(red, "pass")
        passed, report = self.judge([red])
        self.assertTrue(passed)
        self.assertTrue(any(line.startswith("stale-known-red") for line in report))

    def test_the_verdict_command_exit_codes(self) -> None:
        leg = self.leg("a")
        self.record(leg, "findings")
        args = [
            "verdict",
            "--plan-result",
            "success",
            "--applies",
            "true",
            "--matrix",
            json.dumps({"include": [leg]}),
            "--records",
            str(self.records),
        ]
        with (
            redirect_stdout(io.StringIO()),
            mock.patch.dict("os.environ", {"GITHUB_STEP_SUMMARY": ""}),
        ):
            self.assertEqual(1, pc.main(args))
            self.record(leg, "pass")
            self.assertEqual(0, pc.main(args))


if __name__ == "__main__":
    unittest.main()
