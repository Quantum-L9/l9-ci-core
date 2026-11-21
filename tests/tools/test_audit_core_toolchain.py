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


if __name__ == "__main__":
    unittest.main()
