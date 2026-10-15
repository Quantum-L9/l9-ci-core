"""``tools/l9_repo/contract.py``: the single Repository Execution V2 authority.

The organization admission bridge validates V2 consumers only through this
module, so its contract, facade, and ``Repo.mk`` ABI rules are tested here
directly; the bridge suite (``tests/workflows``) proves the same rules gate
execution.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from l9_repo.contract import (  # noqa: E402
    PHASES,
    ContractError,
    RepositoryExecution,
    render_facade,
    validate_repo_mk_text,
    validate_v2_contract_data,
)

LEAVES = [f"repo-{phase}" for phase in PHASES]
VALID_REPO_MK = (
    ".PHONY: "
    + " ".join(LEAVES)
    + "\n\n"
    + "".join(f"{leaf}:\n\t@true\n" for leaf in LEAVES)
)


class ContractTests(unittest.TestCase):
    def test_exact_contract_is_admitted(self) -> None:
        document = {
            "schema": "l9.repo-execution/v2",
            "facade": "make-v1",
            "required_phases": list(PHASES),
        }
        self.assertEqual(document, validate_v2_contract_data(dict(document)))

    def test_unsupported_facade_and_phase_drift_are_rejected(self) -> None:
        base = {
            "schema": "l9.repo-execution/v2",
            "facade": "make-v1",
            "required_phases": list(PHASES),
        }
        for name, override in {
            "facade": {"facade": "make-v2"},
            "reordered": {"required_phases": ["setup", "check", "validate", "test"]},
            "extra key": {"commands": {}},
        }.items():
            with self.subTest(case=name), self.assertRaises(ContractError):
                validate_v2_contract_data({**base, **override})


class RepoMkAbiTests(unittest.TestCase):
    def test_unconditional_leaves_are_admitted(self) -> None:
        validate_repo_mk_text(VALID_REPO_MK)

    def test_leaf_inside_a_conditional_or_define_is_missing(self) -> None:
        for opener, closer in (
            ("ifdef NEVER", "endif"),
            ("ifndef ALWAYS", "endif"),
            ("ifeq ($(A),b)", "else\nendif"),
            ("ifneq ($(A),b)", "endif"),
            ("define HIDDEN", "endef"),
            ("override define HIDDEN", "endef"),
        ):
            text = VALID_REPO_MK.replace(
                "repo-test:\n\t@true\n", f"{opener}\nrepo-test:\n\t@true\n{closer}\n"
            )
            with self.subTest(opener=opener):
                with self.assertRaisesRegex(ContractError, "repo-test"):
                    validate_repo_mk_text(text)

    def test_conditional_phony_does_not_satisfy_the_abi(self) -> None:
        first, rest = VALID_REPO_MK.split("\n", 1)
        with self.assertRaisesRegex(ContractError, r"must be declared \.PHONY"):
            validate_repo_mk_text(f"ifdef NEVER\n{first}\nendif\n{rest}")

    def test_conditional_redefinition_of_a_facade_verb_is_still_rejected(self) -> None:
        text = VALID_REPO_MK + "ifdef CI\ntest:\n\t@true\nendif\n"
        with self.assertRaisesRegex(ContractError, "redefines facade target"):
            validate_repo_mk_text(text)

    def test_conditional_content_after_the_leaves_is_the_repository_business(
        self,
    ) -> None:
        validate_repo_mk_text(
            VALID_REPO_MK + "ifdef CI\nextra:\n\t@true\nelse\nother:\n\t@true\nendif\n"
        )

    @unittest.skipUnless(shutil.which("make"), "GNU make is not installed")
    def test_the_rejected_shape_really_passes_under_make(self) -> None:
        # The reason the rule exists: make exits 0 for a .PHONY leaf whose only
        # rule sits behind a false conditional.
        with tempfile.TemporaryDirectory() as tmp:
            makefile = pathlib.Path(tmp) / "Makefile"
            makefile.write_text(
                ".PHONY: repo-test\nifdef NEVER\nrepo-test:\n\t@false\nendif\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                ["make", "-f", str(makefile), "repo-test"],
                cwd=tmp,
                capture_output=True,
                check=False,
            )
        self.assertEqual(0, result.returncode)


class FacadeVerificationTests(unittest.TestCase):
    def test_validate_requires_the_byte_exact_facade(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / ".l9").mkdir()
            (root / ".l9" / "repo-workflow.json").write_text(
                '{"schema": "l9.repo-execution/v2", "facade": "make-v1", '
                '"required_phases": ["setup", "validate", "check", "test"]}\n',
                encoding="utf-8",
            )
            (root / "Repo.mk").write_text(VALID_REPO_MK, encoding="utf-8")
            (root / "Makefile").write_bytes(render_facade("make-v1"))
            RepositoryExecution(root).validate()
            (root / "Makefile").write_bytes(render_facade("make-v1") + b"\n")
            with self.assertRaisesRegex(ContractError, "drift from facade template"):
                RepositoryExecution(root).validate()


if __name__ == "__main__":
    unittest.main()
