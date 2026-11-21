"""Core gitleaks rules reach every repository; rule-id collisions fail by name.

Operator correction (2026-11-21; docs/gitleaks-core-rules.md). gitleaks keeps
one rule per id: a repository rule whose id equals a default (or a Core rule)
silently replaces it, which cost Enrichment.Inference.Engine four rules. The
gitleaks step of org-ci.yml (and of security.yml, the v1 kernel) now merges
gitleaks' defaults, the Core rules from presets/security/.gitleaks.toml and
the repository's own .gitleaks.toml, and refuses a colliding id by name.

These tests run the step's own merge script, bind its embedded Core rules to
the preset, and prove each Core rule catches what it was moved in to catch;
where a gitleaks executable is installed they also show the defaults miss it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import tomllib
import unittest
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
PRESET = ROOT / "presets" / "security" / ".gitleaks.toml"
BEGIN = "# --- Core rules (begin) ---\n"
END = "# --- Core rules (end) ---"


def gitleaks_step(workflow: str, job: str) -> dict[str, Any]:
    steps = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))["jobs"][
        job
    ]["steps"]
    [found] = [s for s in steps if s.get("name") == "Secret scan (gitleaks)"]
    return found


def heredoc(script: str, tag: str, index: int = 0) -> str:
    bodies = re.findall(rf"<<'{tag}'\n(.*?)\n{tag}\n", script, flags=re.S)
    return bodies[index]


def preset_core_rules() -> str:
    text = PRESET.read_text(encoding="utf-8")
    return text[text.index(BEGIN) + len(BEGIN) : text.index(END)]


def merge_script() -> str:
    return heredoc(gitleaks_step("org-ci.yml", "gitleaks")["run"], "PY", 0)


#: A stand-in for gitleaks' default rules: the ids the merge must refuse.
DEFAULTS = textwrap.dedent(
    """\
    [[rules]]
    id = "openai-api-key"
    regex = '''sk-[a-zA-Z0-9]{20}T3BlbkFJ[a-zA-Z0-9]{20}'''

    [[rules]]
    id = "generic-api-key"
    regex = '''x'''

    [[rules]]
    id = "slack-bot-token"
    regex = '''xoxb-'''

    [[rules]]
    id = "private-key"
    regex = '''BEGIN'''
    """
)


class Merge:
    """The step's merge script in a directory with an optional .gitleaks.toml."""

    def __init__(self, repository_config: str | None) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        if repository_config is not None:
            (self.root / ".gitleaks.toml").write_text(
                repository_config, encoding="utf-8"
            )
        (self.root / "defaults.toml").write_text(DEFAULTS, encoding="utf-8")
        (self.root / "core.toml").write_text(preset_core_rules(), encoding="utf-8")
        self.result = subprocess.run(
            [
                "python3",
                "-I",
                "-",
                ".gitleaks.toml",
                "defaults.toml",
                "core.toml",
                "merged.toml",
            ],
            input=merge_script(),
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )

    @property
    def merged(self) -> dict[str, Any]:
        return tomllib.loads((self.root / "merged.toml").read_text(encoding="utf-8"))

    def rule_ids(self) -> list[str]:
        return [rule["id"] for rule in self.merged.get("rules", [])]

    def close(self) -> None:
        self._tmp.cleanup()


def core_ids() -> list[str]:
    return [rule["id"] for rule in tomllib.loads(preset_core_rules())["rules"]]


class BindingTests(unittest.TestCase):
    def test_every_core_rule_id_is_namespaced(self) -> None:
        ids = core_ids()
        self.assertTrue(ids)
        self.assertEqual(len(ids), len(set(ids)))
        for rule_id in ids:
            self.assertTrue(rule_id.startswith("l9-"), rule_id)

    def test_both_workflows_embed_the_presets_core_rules(self) -> None:
        for workflow, job in (("org-ci.yml", "gitleaks"), ("security.yml", "security")):
            with self.subTest(workflow=workflow):
                script = gitleaks_step(workflow, job)["run"]
                self.assertEqual(
                    preset_core_rules().rstrip("\n"), heredoc(script, "TOML")
                )

    def test_both_workflows_run_the_same_merge(self) -> None:
        self.assertEqual(
            merge_script(),
            heredoc(gitleaks_step("security.yml", "security")["run"], "PY", 0),
        )

    def test_the_default_ids_come_from_the_pinned_release(self) -> None:
        script = gitleaks_step("org-ci.yml", "gitleaks")["run"]
        self.assertRegex(script, r'GITLEAKS_DEFAULTS_SHA256="[0-9a-f]{64}"')
        self.assertIn(
            "raw.githubusercontent.com/gitleaks/gitleaks/v${GITLEAKS_VERSION}/config/gitleaks.toml",
            script,
        )
        self.assertIn(
            '[ "${actual_defaults}" != "${GITLEAKS_DEFAULTS_SHA256}" ]', script
        )

    def test_the_preset_is_valid_gitleaks_configuration(self) -> None:
        data = tomllib.loads(PRESET.read_text(encoding="utf-8"))
        self.assertEqual({"useDefault": True}, data["extend"])
        self.assertEqual(core_ids(), [rule["id"] for rule in data["rules"]])


class CollisionTests(unittest.TestCase):
    def test_eie_s_four_colliding_rules_are_refused_by_name_and_line(self) -> None:
        config = textwrap.dedent(
            """\
            title = "EIE"

            [[rules]]
            id = "openai-api-key"
            regex = '''sk-[a-zA-Z0-9]{20,}'''

            [[rules]]
            id = "perplexity-api-key"
            regex = '''pplx-[a-zA-Z0-9]{40,}'''

            [[rules]]
            id = "generic-api-key"
            regex = '''x'''

            [[rules]]
            id = "slack-bot-token"
            regex = '''y'''

            [[rules]]
            id = "private-key"
            regex = '''z'''
            """
        )
        merge = Merge(config)
        try:
            self.assertNotEqual(0, merge.result.returncode)
            out = merge.result.stdout
            for rule_id, line in (
                ("openai-api-key", 4),
                ("generic-api-key", 12),
                ("slack-bot-token", 16),
                ("private-key", 20),
            ):
                self.assertIn(
                    f"::error file=.gitleaks.toml,line={line},title=gitleaks rule id collision::"
                    f"rule '{rule_id}' is a gitleaks default rule id",
                    out,
                )
            self.assertNotIn("perplexity-api-key", out)
            self.assertIn("4 gitleaks rule id collision(s)", merge.result.stderr)
            self.assertFalse(
                (merge.root / "merged.toml").exists(), "nothing is scanned"
            )
        finally:
            merge.close()

    def test_a_repository_rule_reusing_a_core_rule_id_is_refused(self) -> None:
        merge = Merge("[[rules]]\nid = \"l9-openai-api-key\"\nregex = '''sk-x'''\n")
        try:
            self.assertNotEqual(0, merge.result.returncode)
            self.assertIn("is a Core rule id", merge.result.stdout)
        finally:
            merge.close()

    def test_a_rule_defined_twice_is_refused(self) -> None:
        merge = Merge(
            "[[rules]]\nid = \"mine\"\nregex = '''a'''\n\n"
            "[[rules]]\nid = \"mine\"\nregex = '''b'''\n"
        )
        try:
            self.assertNotEqual(0, merge.result.returncode)
            self.assertIn("rule 'mine' is defined twice", merge.result.stdout)
        finally:
            merge.close()


class InjectionTests(unittest.TestCase):
    def test_a_repository_without_a_config_gets_the_defaults_and_core_rules(
        self,
    ) -> None:
        merge = Merge(None)
        try:
            self.assertEqual(0, merge.result.returncode, merge.result.stdout)
            self.assertEqual({"useDefault": True}, merge.merged["extend"])
            self.assertEqual(core_ids(), merge.rule_ids())
        finally:
            merge.close()

    def test_a_repository_s_own_rules_and_allowlists_are_kept(self) -> None:
        merge = Merge(
            "[allowlist]\npaths = ['''^docs/''']\n\n"
            "[[rules]]\nid = \"acme-token\"\nregex = '''acme_[0-9]{8}'''\n"
        )
        try:
            self.assertEqual(0, merge.result.returncode, merge.result.stdout)
            self.assertEqual(["acme-token", *core_ids()], merge.rule_ids())
            self.assertEqual(["^docs/"], merge.merged["allowlist"]["paths"])
        finally:
            merge.close()

    def test_the_stamped_preset_is_not_doubled(self) -> None:
        merge = Merge(PRESET.read_text(encoding="utf-8"))
        try:
            self.assertEqual(0, merge.result.returncode, merge.result.stdout)
            self.assertEqual(core_ids(), merge.rule_ids())
        finally:
            merge.close()

    def test_an_older_stamped_preset_gains_the_core_rules(self) -> None:
        merge = Merge('title = "old"\n\n[extend]\nuseDefault = true\n')
        try:
            self.assertEqual(0, merge.result.returncode, merge.result.stdout)
            self.assertEqual(core_ids(), merge.rule_ids())
        finally:
            merge.close()

    def test_a_path_extend_is_refused(self) -> None:
        merge = Merge('[extend]\npath = "other.toml"\n')
        try:
            self.assertNotEqual(0, merge.result.returncode)
            self.assertIn("names a path", merge.result.stderr)
        finally:
            merge.close()


#: Per Core rule: a value it was moved into Core to catch (synthetic, never a
#: real credential), and where it came from.
SAMPLES = {
    "l9-openai-api-key": "OPENAI = 'sk-" + "Ab3dE6gH9jK2mN5pQ8sT1vW4yZ'",
    "l9-openai-project-key": "OPENAI = 'sk-proj-" + "a1B2c3D4e5" * 9 + "'",
    "l9-api-key-assignment": 'api_key = "' + "abcdefghijklmnopqrstuvwx" + '"',
    "l9-slack-bot-token": "SLACK = 'xoxb-"
    + "12345678901234-12345678901234-"
    + "AbCdEfGhIjKlMnOpQrStUv'",
    "l9-private-key-header": "-----BEGIN RSA " + "PRIVATE KEY-----",
    "l9-jwt": "t = 'eyJ"
    + "hbGciOiJIUz"
    + ".eyJ"
    + "zdWIiOiIxMj"
    + "."
    + "SflKxwRJSMe'",
    "l9-perplexity-api-key": "PPLX = 'pplx-" + "a1B2c3D4e5" * 4 + "'",
    "l9-postgres-url-password": "DSN = 'postgresql://app:"
    + "Zq8wPx2Lk9"
    + "@db.internal/app'",
    "l9-redis-url-password": "URL = 'redis://:"
    + "Zq8wPx2Lk9"
    + "@cache.internal:6379'",
    "l9-neo4j-url-password": "URI = 'neo4j+s://neo4j:"
    + "Zq8wPx2Lk9"
    + "@graph.internal'",
    "l9-neo4j-password-assignment": "NEO4J_PASSWORD = '" + "Zq8wPx2Lk9" + "'",
}


def go_regex(rule: dict[str, Any]) -> re.Pattern[str]:
    pattern = rule["regex"]
    flags = 0
    if pattern.startswith("(?i)"):
        pattern, flags = pattern[4:], re.IGNORECASE
    return re.compile(pattern, flags)


class CoreRuleTests(unittest.TestCase):
    def test_every_core_rule_has_a_sample_it_catches(self) -> None:
        rules = {
            rule["id"]: rule for rule in tomllib.loads(preset_core_rules())["rules"]
        }
        self.assertEqual(set(rules), set(SAMPLES))
        for rule_id, sample in SAMPLES.items():
            with self.subTest(rule=rule_id):
                self.assertRegex(sample, go_regex(rules[rule_id]))
                keywords = [k.lower() for k in rules[rule_id].get("keywords", [])]
                self.assertTrue(
                    not keywords or any(k in sample.lower() for k in keywords),
                    "gitleaks only evaluates a rule whose keyword is present",
                )

    def test_gitleaks_finds_each_sample_only_through_its_core_rule(self) -> None:
        """Real gitleaks, where installed: the Core rule fires, the defaults miss.

        Skip condition: no ``gitleaks`` executable on PATH (the step downloads
        its pinned release at run time; a unit test cannot). The regex tests
        above run everywhere.
        """
        executable = shutil.which("gitleaks")
        if executable is None:
            self.skipTest("no gitleaks executable on PATH (see the docstring)")
        for rule_id, sample in SAMPLES.items():
            with self.subTest(rule=rule_id), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "src").mkdir()
                (root / "src" / "settings.py").write_text(
                    sample + "\n", encoding="utf-8"
                )
                found = {}
                for name, config in (
                    ("defaults", "[extend]\nuseDefault = true\n"),
                    ("core", PRESET.read_text(encoding="utf-8")),
                ):
                    (root / f"{name}.toml").write_text(config, encoding="utf-8")
                    report = root / f"{name}.json"
                    subprocess.run(
                        [
                            executable,
                            "dir",
                            "--config",
                            str(root / f"{name}.toml"),
                            "--no-banner",
                            "--report-format",
                            "json",
                            "--report-path",
                            str(report),
                            str(root / "src"),
                        ],
                        capture_output=True,
                        check=False,
                        env={**os.environ, "HOME": tmp},
                    )
                    found[name] = {f["RuleID"] for f in json.loads(report.read_text())}
                self.assertIn(rule_id, found["core"])
                self.assertEqual(
                    set(),
                    found["defaults"],
                    f"{rule_id}: the defaults already catch this sample",
                )


if __name__ == "__main__":
    unittest.main()
