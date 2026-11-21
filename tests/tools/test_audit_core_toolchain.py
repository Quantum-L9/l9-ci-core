"""Core's pinned toolchain resolves into a complete, layered audit set.

``tools/audit_core_toolchain.py`` builds what ``self-toolchain-audit.yml``
audits with ``pip-audit --no-deps``. These tests run it against Core's real
lock files (every Python tool covered at its pinned version) and against
fixtures for each way the set could be incomplete, which must be refused
rather than audited as clean.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import datetime as dt  # noqa: E402

import audit_core_toolchain as audit  # noqa: E402

LOCK = {
    "ruff": "0.16.1",
    "semgrep": "1.171.0",
    "bandit": "1.8.6",
    "biome": "2.5.8",
    "python": "3.12.14",
}
OWNERSHIP = {
    "schema": "l9.core-owned-tools/v1",
    "tools": [
        {"name": "ruff", "ecosystems": {"pip": ["ruff"]}},
        {"name": "semgrep", "ecosystems": {"pip": ["semgrep"]}},
        {"name": "bandit", "ecosystems": {"pip": ["bandit"]}},
        {"name": "biome", "ecosystems": {"npm": ["@biomejs/biome"]}},
    ],
}
SEMGREP_LOCK = (
    "# hash-locked\n"
    "semgrep==1.171.0 \\\n    --hash=sha256:aa\n"
    "urllib3==2.7.0 \\\n    --hash=sha256:bb\n"
)


def fake_resolver(closures: dict[str, str]) -> audit.Resolver:
    """Resolve by looking the requirements up, as uv would answer them."""

    def resolve(requirements: str) -> str:
        return closures[requirements]

    return resolve


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        consumer = root / audit.CONSUMER_CI
        consumer.mkdir(parents=True)
        (root / audit.SEMGREP_LOCKS).mkdir(parents=True)
        self.write(lock=LOCK, ownership=OWNERSHIP, consumer="ruff==0.16.1\n")
        (root / audit.SEMGREP_LOCKS / "semgrep-1.171.0.txt").write_text(
            SEMGREP_LOCK, encoding="utf-8"
        )

    def write(
        self,
        *,
        lock: dict[str, str] | None = None,
        ownership: dict[str, object] | None = None,
        consumer: str | None = None,
    ) -> None:
        if lock is not None:
            (self.root / audit.LOCK).write_text(json.dumps(lock), encoding="utf-8")
        if ownership is not None:
            (self.root / audit.OWNERSHIP).write_text(
                json.dumps(ownership), encoding="utf-8"
            )
        if consumer is not None:
            (self.root / audit.CONSUMER_REQUIREMENTS).write_text(
                consumer, encoding="utf-8"
            )


class CoresOwnLockTests(unittest.TestCase):
    """The committed lock files plan a complete audit."""

    def test_every_python_tool_is_covered_at_its_pinned_version(self) -> None:
        tools = audit.python_tools(ROOT)
        lock = json.loads((ROOT / audit.LOCK).read_text(encoding="utf-8"))
        self.assertEqual(
            {
                "bandit",
                "mypy",
                "pip",
                "pip-audit",
                "pre-commit",
                "pytest",
                "ruff",
                "semgrep",
                "uv",
            },
            set(tools),
        )
        for name, version in tools.items():
            self.assertEqual(lock[name], version)
        planned = audit.sources(ROOT, tools)
        audit.check_coverage(tools, planned)
        labels = [source.label for source in planned]
        self.assertEqual("consumer CI toolchain", labels[0])
        self.assertIn(f"semgrep {lock['semgrep']}", labels)
        semgrep = next(s for s in planned if s.label.startswith("semgrep"))
        self.assertTrue(semgrep.locked)
        self.assertEqual(
            {"bandit", "pip-audit"},
            {s.label for s in planned if not s.locked} - {"consumer CI toolchain"},
        )

    def test_non_python_tools_are_out_of_scope(self) -> None:
        tools = audit.python_tools(ROOT)
        for name in ("biome", "gitleaks", "python"):
            self.assertNotIn(name, tools)


class PlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fixture = Fixture(Path(self._tmp.name))

    def plan(self) -> tuple[dict[str, str], list[audit.Source]]:
        tools = audit.python_tools(self.fixture.root)
        planned = audit.sources(self.fixture.root, tools)
        audit.check_coverage(tools, planned)
        return tools, planned

    def test_each_install_is_a_source(self) -> None:
        tools, planned = self.plan()
        self.assertEqual(
            {"ruff": "0.16.1", "semgrep": "1.171.0", "bandit": "1.8.6"}, tools
        )
        self.assertEqual(
            ["consumer CI toolchain", "semgrep 1.171.0", "bandit"],
            [source.label for source in planned],
        )
        self.assertEqual("bandit==1.8.6\n", planned[2].requirements)

    def test_a_tool_without_a_locked_version_is_refused(self) -> None:
        self.fixture.write(lock={k: v for k, v in LOCK.items() if k != "bandit"})
        with self.assertRaisesRegex(audit.AuditError, "bandit has a pip package"):
            self.plan()

    def test_an_install_that_disagrees_with_the_lock_is_refused(self) -> None:
        self.fixture.write(consumer="ruff==0.15.0\n")
        with self.assertRaisesRegex(
            audit.AuditError, "the lock and the install disagree"
        ):
            self.plan()

    def test_a_semgrep_version_without_its_locked_closure_is_refused(self) -> None:
        self.fixture.write(lock={**LOCK, "semgrep": "1.172.0"})
        with self.assertRaisesRegex(audit.AuditError, "no hash-locked closure"):
            self.plan()

    def test_no_python_tool_is_refused(self) -> None:
        self.fixture.write(ownership={"tools": [OWNERSHIP["tools"][3]]})
        with self.assertRaisesRegex(audit.AuditError, "names no Python tool"):
            self.plan()


class PythonVersionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fixture = Fixture(Path(self._tmp.name))

    def test_the_exact_locked_patch_version_is_used(self) -> None:
        self.assertEqual("3.12.14", audit.python_version(self.fixture.root))
        lock = json.loads((ROOT / audit.LOCK).read_text(encoding="utf-8"))
        self.assertEqual(lock["python"], audit.python_version(ROOT))

    def test_a_lock_without_an_exact_python_is_refused(self) -> None:
        for python in (None, "3.12", "latest"):
            with self.subTest(python=python):
                lock = {k: v for k, v in LOCK.items() if k != "python"}
                if python is not None:
                    lock["python"] = python
                self.fixture.write(lock=lock)
                with self.assertRaisesRegex(audit.AuditError, "no exact Python"):
                    audit.python_version(self.fixture.root)


class ResolveTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        fixture = Fixture(Path(self._tmp.name))
        tools = audit.python_tools(fixture.root)
        self.planned = audit.sources(fixture.root, tools)

    def test_the_union_keeps_every_distinct_pin(self) -> None:
        union, counts = audit.resolve_all(
            self.planned,
            fake_resolver(
                {
                    "ruff==0.16.1\n": "ruff==0.16.1\n",
                    "bandit==1.8.6\n": "bandit==1.8.6\nurllib3==2.8.0\nPyYAML==6.0.3\n",
                }
            ),
        )
        self.assertEqual({"2.7.0", "2.8.0"}, union["urllib3"])
        self.assertEqual({"6.0.3"}, union["pyyaml"])
        self.assertEqual(
            [("consumer CI toolchain", 1), ("semgrep 1.171.0", 2), ("bandit", 3)],
            counts,
        )

    def test_a_resolution_that_drops_its_tool_is_refused(self) -> None:
        resolver = fake_resolver(
            {"ruff==0.16.1\n": "ruff==0.16.1\n", "bandit==1.8.6\n": "pyyaml==6\n"}
        )
        with self.assertRaisesRegex(audit.AuditError, "did not resolve bandit"):
            audit.resolve_all(self.planned, resolver)

    def test_an_empty_resolution_is_refused(self) -> None:
        resolver = fake_resolver({"ruff==0.16.1\n": "", "bandit==1.8.6\n": ""})
        with self.assertRaisesRegex(audit.AuditError, "resolved to no pinned package"):
            audit.resolve_all(self.planned, resolver)


class LayerTests(unittest.TestCase):
    def test_layers_name_each_package_once_and_hold_every_pin(self) -> None:
        union = {"a": {"1"}, "b": {"2", "1"}, "c": {"3", "1", "2"}}
        layers = audit.layers(union)
        self.assertEqual(["a==1\nb==1\nc==1\n", "b==2\nc==2\n", "c==3\n"], layers)
        for text in layers:
            names = [line.split("==")[0] for line in text.splitlines()]
            self.assertEqual(len(names), len(set(names)))
        self.assertEqual(6, sum(len(text.splitlines()) for text in layers))

    def test_no_pin_is_no_layer(self) -> None:
        self.assertEqual([], audit.layers({}))


class MainTests(unittest.TestCase):
    """The command resolves with a uv executable and names the count."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.fixture = Fixture(self.tmp / "core")
        self.uv = self.tmp / "uv"

    def fake_uv(self, code: int = 0) -> None:
        """Writes ``<tool>`` plus ``urllib3==2.8.0`` to --output-file."""
        self.uv.write_text(
            "#!/usr/bin/env bash\n"
            f"[ {code} -eq 0 ] || {{ echo 'no solution' >&2; exit {code}; }}\n"
            'input="$3"\n'
            f'echo "$@" > "{self.tmp}/uv-args"\n'
            'while [ "$#" -gt 0 ]; do\n'
            '  if [ "$1" = "--output-file" ]; then\n'
            '    { cat "${input}"; echo "urllib3==2.8.0"; } > "$2"\n'
            "  fi\n"
            "  shift\n"
            "done\n",
            encoding="utf-8",
        )
        self.uv.chmod(0o755)

    def run_main(self) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = audit.main(
                [
                    "--root",
                    str(self.fixture.root),
                    "resolve",
                    "--uv",
                    str(self.uv),
                    "--output-dir",
                    str(self.tmp / "out"),
                ]
            )
        return code, out.getvalue(), err.getvalue()

    def test_the_set_is_written_in_layers_and_counted(self) -> None:
        self.fake_uv()
        code, out, err = self.run_main()
        self.assertEqual(0, code, err)
        self.assertIn("toolchain audit: semgrep 1.171.0: 2 pinned package(s)", out)
        self.assertIn(
            "toolchain audit: 5 distinct pinned package(s) (4 project(s)) from 3 "
            "Core-pinned Python tool(s): bandit, ruff, semgrep",
            out,
        )
        args = (self.tmp / "uv-args").read_text(encoding="utf-8").split()
        self.assertEqual("3.12.14", args[args.index("--python-version") + 1])
        layers = sorted(os.listdir(self.tmp / "out"))
        self.assertEqual(["layer-1.txt", "layer-2.txt"], layers)
        self.assertEqual(
            "urllib3==2.8.0\n",
            (self.tmp / "out" / "layer-2.txt").read_text(encoding="utf-8"),
        )

    def test_an_output_directory_outside_the_allowed_roots_is_refused(self) -> None:
        self.fake_uv()
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = audit.main(
                [
                    "--root",
                    str(self.fixture.root),
                    "resolve",
                    "--uv",
                    str(self.uv),
                    "--output-dir",
                    "/proc/self/toolchain-audit",
                ]
            )
        self.assertEqual(2, code)
        self.assertIn("outside the working and temp directories", err.getvalue())

    def test_an_unresolvable_toolchain_fails_without_a_set(self) -> None:
        self.fake_uv(code=1)
        code, _, err = self.run_main()
        self.assertEqual(2, code)
        self.assertIn("uv could not resolve", err)
        self.assertFalse((self.tmp / "out").exists())


TODAY = dt.date(2026, 11, 21)


def entry(**overrides: str) -> dict[str, str]:
    base = {
        "id": "GHSA-53pm-wgxf-c6qf",
        "package": "wcmatch",
        "version": "8.5.2",
        "reason": "not reachable: why, concretely",
        "owner": "l9-ci-core",
        "created": "2026-11-21",
        "expires": "2026-12-05",
        "removal_trigger": "upstream fix published",
    }
    base.update(overrides)
    return base


def report(*deps: dict[str, object]) -> dict[str, object]:
    return {"dependencies": list(deps), "fixes": []}


def dep(name: str, version: str, *vulns: tuple[str, list[str]]) -> dict[str, object]:
    return {
        "name": name,
        "version": version,
        "vulns": [
            {"id": i, "aliases": a, "fix_versions": [], "description": ""}
            for i, a in vulns
        ],
    }


class ExceptionsFileTests(unittest.TestCase):
    """Core's exceptions file is well formed, bounded and current."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "exceptions.yaml"

    def load(
        self, *entries: object, schema: str = audit.EXCEPTIONS_SCHEMA
    ) -> list[audit.AdvisoryException]:
        self.path.write_text(
            json.dumps({"schema": schema, "exceptions": list(entries)}),
            encoding="utf-8",
        )
        return audit.load_exceptions(self.path, TODAY)

    def test_a_valid_entry_loads(self) -> None:
        (loaded,) = self.load(entry(package="WCMatch"))
        self.assertEqual(
            ("GHSA-53pm-wgxf-c6qf", "wcmatch", "8.5.2", dt.date(2026, 12, 5)),
            (loaded.advisory, loaded.package, loaded.version, loaded.expires),
        )

    def test_the_last_day_is_still_valid(self) -> None:
        self.load(entry(created="2026-11-07", expires="2026-11-21"))

    def test_malformed_documents_and_entries_are_refused(self) -> None:
        cases: list[tuple[str, list[object], str]] = [
            ("not-a-schema", [], "is not an"),
            (audit.EXCEPTIONS_SCHEMA, ["x"], "must hold exactly"),
            (audit.EXCEPTIONS_SCHEMA, [{**entry(), "extra": "1"}], "must hold exactly"),
            (
                audit.EXCEPTIONS_SCHEMA,
                [{k: v for k, v in entry().items() if k != "removal_trigger"}],
                "must hold exactly",
            ),
            (audit.EXCEPTIONS_SCHEMA, [entry(reason="  ")], "must be non-empty"),
            (audit.EXCEPTIONS_SCHEMA, [entry(id="CVE-x")], "is not an advisory id"),
            (audit.EXCEPTIONS_SCHEMA, [entry(version="8.*")], "is not a pin"),
            (
                audit.EXCEPTIONS_SCHEMA,
                [entry(created="2026-13-01")],
                "is not a YYYY-MM-DD",
            ),
            (
                audit.EXCEPTIONS_SCHEMA,
                [entry(created="2026-11-22", expires="2026-11-30")],
                "is after today",
            ),
            (audit.EXCEPTIONS_SCHEMA, [entry(expires="2026-12-06")], "within 14 days"),
            (
                audit.EXCEPTIONS_SCHEMA,
                [entry(expires="2026-11-20", created="2026-11-19")],
                "expired on 2026-11-20",
            ),
            (audit.EXCEPTIONS_SCHEMA, [entry(), entry()], "duplicate exception"),
        ]
        for schema, entries, message in cases:
            with self.subTest(message=message, entries=entries):
                with self.assertRaisesRegex(audit.AuditError, message):
                    self.load(*entries, schema=schema)

    def test_cores_own_file_is_valid_bounded_and_matches_the_semgrep_lock(self) -> None:
        document = json.loads((ROOT / audit.EXCEPTIONS).read_text(encoding="utf-8"))
        created = {dt.date.fromisoformat(e["created"]) for e in document["exceptions"]}
        loaded = audit.load_exceptions(ROOT / audit.EXCEPTIONS, max(created))
        lock = json.loads((ROOT / audit.LOCK).read_text(encoding="utf-8"))
        semgrep = audit.pins(
            (ROOT / audit.SEMGREP_LOCKS / f"semgrep-{lock['semgrep']}.txt").read_text(
                encoding="utf-8"
            )
        )
        for item, raw in zip(loaded, document["exceptions"], strict=True):
            with self.subTest(exception=item.advisory):
                self.assertEqual("l9-ci-core", raw["owner"])
                self.assertEqual(item.version, semgrep.get(item.package))
                self.assertIn("semgrep scan", raw["reason"])


class JudgeTests(unittest.TestCase):
    LAYER = "mcp==1.23.3\nwcmatch==8.5.2\n"

    def exception(
        self, advisory: str, package: str = "mcp", version: str = "1.23.3"
    ) -> audit.AdvisoryException:
        return audit.AdvisoryException(advisory, package, version, dt.date(2026, 12, 5))

    def test_a_finding_is_excepted_by_its_id_or_an_alias(self) -> None:
        verdict = audit.judge(
            [self.LAYER],
            [
                report(
                    dep(
                        "mcp",
                        "1.23.3",
                        ("PYSEC-1", ["GHSA-aaaa-bbbb-cccc"]),
                        ("PYSEC-2", []),
                    ),
                    dep("wcmatch", "8.5.2"),
                )
            ],
            [self.exception("GHSA-aaaa-bbbb-cccc"), self.exception("PYSEC-2")],
        )
        self.assertEqual(2, verdict.audited)
        self.assertEqual(
            [("mcp", "1.23.3", "PYSEC-1"), ("mcp", "1.23.3", "PYSEC-2")],
            verdict.excepted,
        )
        self.assertEqual([], verdict.open)
        self.assertEqual([], verdict.stale)

    def test_an_exception_on_another_version_does_not_cover_a_finding(self) -> None:
        stale = self.exception("PYSEC-1", version="1.23.2")
        verdict = audit.judge(
            [self.LAYER],
            [report(dep("mcp", "1.23.3", ("PYSEC-1", [])), dep("wcmatch", "8.5.2"))],
            [stale],
        )
        self.assertEqual([("mcp", "1.23.3", "PYSEC-1")], verdict.open)
        self.assertEqual([stale], verdict.stale)

    def test_an_exception_matching_no_finding_is_stale(self) -> None:
        stale = self.exception("PYSEC-9")
        verdict = audit.judge(
            [self.LAYER],
            [report(dep("mcp", "1.23.3"), dep("wcmatch", "8.5.2"))],
            [stale],
        )
        self.assertEqual([stale], verdict.stale)

    def test_a_pin_pip_audit_did_not_audit_is_refused(self) -> None:
        for reports, message in (
            ([report(dep("mcp", "1.23.3"))], "no result for wcmatch==8.5.2"),
            (
                [report(dep("mcp", "1.23.3"), {"name": "wcmatch", "skip_reason": "x"})],
                "did not audit wcmatch",
            ),
            ([{"fixes": []}], "no dependency list"),
        ):
            with self.subTest(message=message):
                with self.assertRaisesRegex(audit.AuditError, message):
                    audit.judge([self.LAYER], reports, [])


class JudgeCommandTests(unittest.TestCase):
    """``judge`` and ``exceptions`` exit codes and the count line."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "core"
        (self.root / audit.EXCEPTIONS).parent.mkdir(parents=True)
        self.reports = Path(self._tmp.name) / "reports"
        self.reports.mkdir()
        (self.reports / "layer-1.txt").write_text(
            "mcp==1.23.3\nwcmatch==8.5.2\n", encoding="utf-8"
        )

    def write(self, entries: list[dict[str, str]], *deps: dict[str, object]) -> None:
        (self.root / audit.EXCEPTIONS).write_text(
            json.dumps({"schema": audit.EXCEPTIONS_SCHEMA, "exceptions": entries}),
            encoding="utf-8",
        )
        (self.reports / "layer-1.json").write_text(
            json.dumps(report(*deps)), encoding="utf-8"
        )

    def run_command(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = audit.main(["--root", str(self.root), *argv])
        return code, out.getvalue(), err.getvalue()

    def judge(self) -> tuple[int, str, str]:
        return self.run_command(
            "judge", "--today", "2026-11-21", "--reports", str(self.reports)
        )

    def test_all_excepted_passes_and_names_the_counts(self) -> None:
        self.write(
            [entry(id="PYSEC-2026-1", package="mcp", version="1.23.3")],
            dep("mcp", "1.23.3", ("PYSEC-2026-1", [])),
            dep("wcmatch", "8.5.2"),
        )
        code, out, _ = self.judge()
        self.assertEqual(0, code)
        self.assertIn(
            "toolchain audit: 2 pinned package(s) audited, 1 finding(s) excepted, 0 open",
            out,
        )

    def test_an_open_finding_fails(self) -> None:
        self.write(
            [],
            dep("mcp", "1.23.3"),
            dep("wcmatch", "8.5.2", ("GHSA-53pm-wgxf-c6qf", [])),
        )
        code, out, _ = self.judge()
        self.assertEqual(1, code)
        self.assertIn("open GHSA-53pm-wgxf-c6qf on wcmatch==8.5.2", out)
        self.assertIn("0 finding(s) excepted, 1 open", out)

    def test_a_stale_exception_fails(self) -> None:
        self.write([entry()], dep("mcp", "1.23.3"), dep("wcmatch", "8.5.2"))
        code, out, _ = self.judge()
        self.assertEqual(2, code)
        self.assertIn("stale exception GHSA-53pm-wgxf-c6qf on wcmatch==8.5.2", out)

    def test_an_expired_exception_fails_both_commands(self) -> None:
        self.write(
            [entry(created="2026-11-01", expires="2026-11-15")],
            dep("mcp", "1.23.3"),
            dep("wcmatch", "8.5.2"),
        )
        self.assertEqual(2, self.judge()[0])
        code, _, err = self.run_command("exceptions", "--today", "2026-11-21")
        self.assertEqual(2, code)
        self.assertIn("expired on 2026-11-15", err)

    def test_a_missing_report_fails(self) -> None:
        self.write([], dep("mcp", "1.23.3"), dep("wcmatch", "8.5.2"))
        (self.reports / "layer-1.json").unlink()
        code, _, err = self.judge()
        self.assertEqual(2, code)
        self.assertIn("1 layer(s) and 0 report(s)", err)

    def test_exceptions_prints_each_advisory_once(self) -> None:
        self.write(
            [entry(), entry(package="bracex", version="3.2.4")], dep("mcp", "1.23.3")
        )
        code, out, _ = self.run_command("exceptions", "--today", "2026-11-21")
        self.assertEqual((0, "GHSA-53pm-wgxf-c6qf\n"), (code, out))


if __name__ == "__main__":
    unittest.main()
