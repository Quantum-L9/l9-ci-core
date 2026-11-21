"""Every failing exit in Core's CI names its subject (L9-SIGNAL-001).

tools/signal_law.py scans the run: blocks of .github/workflows/*.yml and
.github/actions/*/action.yml. A non-zero exit must have an ::error on its line
or in the lines before it; otherwise the site must be listed, with a reason,
in tests/workflows/signal-law-exemptions.json. The list only shrinks: an
exemption that no longer matches a site (outside rendered files) fails too.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import signal_law  # noqa: E402

REQUIRED_PATH = (
    ".github/workflows/org-ci.yml",
    ".github/workflows/security.yml",
)


def scan(text: str) -> list[tuple[str, str]]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / ".github" / "workflows" / "w.yml"
        path.parent.mkdir(parents=True)
        path.write_text(text, encoding="utf-8")
        return signal_law.violations(path, root)


def workflow(script: str) -> str:
    body = "".join(f"          {line}\n" for line in script.splitlines())
    return (
        "on: push\njobs:\n  j:\n    runs-on: ubuntu-24.04\n    steps:\n"
        "      - run: |\n" + body
    )


class DetectorTests(unittest.TestCase):
    def test_an_unnamed_failing_exit_is_a_violation(self) -> None:
        for script in (
            "make test\nexit 1",
            'status=$?\nexit "${status}"',
            "python3 - <<'PY'\nimport sys\nsys.exit(2)\nPY",
            "python3 - <<'PY'\nraise SystemExit('bad input')\nPY",
            '[ -n "$X" ] || { echo nope >&2; exit 1; }',
        ):
            with self.subTest(script=script):
                self.assertEqual(1, len(scan(workflow(script))))

    def test_a_named_failing_exit_is_not(self) -> None:
        for script in (
            'echo "::error file=a.py,line=3::rule X failed"\nexit 1',
            "python3 - <<'PY'\nraise SystemExit('::error::bad input: x')\nPY",
            'echo "::error title=T::make test exited 2"\nfoo\nexit "${rc}"',
        ):
            with self.subTest(script=script):
                self.assertEqual([], scan(workflow(script)))

    def test_success_exits_and_message_text_are_not_failures(self) -> None:
        for script in (
            "exit 0",
            "python3 - <<'PY'\nimport sys\nsys.exit(0)\nPY",
            'echo "Required instances: fail (exit ${code})"',
            "# exit 1 in a comment",
        ):
            with self.subTest(script=script):
                self.assertEqual([], scan(workflow(script)))

    def test_an_error_far_above_does_not_count(self) -> None:
        script = 'echo "::error::x"\n' + "true\n" * (signal_law.WINDOW + 1) + "exit 1"
        self.assertEqual(1, len(scan(workflow(script))))


class ConformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.counts = signal_law.all_violations()
        self.exempt = signal_law.load_exemptions()

    def test_every_unnamed_failing_exit_is_exempt_with_a_reason(self) -> None:
        open_sites = sorted(key for key in self.counts if key not in self.exempt)
        self.assertEqual([], open_sites, "name the subject in an ::error first")

    def test_the_exemption_list_only_shrinks(self) -> None:
        self.assertEqual([], signal_law.stale_exemptions(self.counts, self.exempt))

    def test_the_required_check_path_has_no_open_exemption(self) -> None:
        open_on_required = [
            key
            for key, entry in self.exempt.items()
            if key[0] in REQUIRED_PATH and entry["reason"].startswith("open:")
        ]
        self.assertEqual([], open_on_required)

    def test_the_operator_named_counter_examples_name_their_subject(self) -> None:
        """CEG #529, CEG #532 and .github #278 (L9-SIGNAL-001 directive)."""
        org_ci = (ROOT / ".github/workflows/org-ci.yml").read_text(encoding="utf-8")
        self.assertIn("::error title=Required instances::", org_ci)
        self.assertIn('{"title": f"Required instance {ident}"}', org_ci)
        self.assertIn("::error title=Repository verification::${subject}", org_ci)
        violations = Counter(
            file for file, _ in signal_law.violations(ROOT / REQUIRED_PATH[0])
        )
        self.assertEqual(Counter(), violations)


if __name__ == "__main__":
    unittest.main()
