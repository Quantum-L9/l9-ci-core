"""Repository Execution V2: contract, versioned facade, and Repo.mk ABI.

The portable consumer contract is three declarative fields::

    {"schema": "l9.repo-execution/v2",
     "facade": "make-v1",
     "required_phases": ["setup", "validate", "check", "test"]}

It carries no commands. The facade named by the contract is a versioned,
immutable Core-owned Make template that routes each phase to a leaf the
repository implements in its own ``Repo.mk``::

    generated Makefile (facade)        repository-owned Repo.mk
        setup    -> repo-setup             repo-setup
        validate -> repo-validate          repo-validate
        check    -> repo-check             repo-check
        test     -> repo-test              repo-test

This module owns exactly that: contract validation (standard library only),
the facade registry, rendering and drift detection for the generated
``Makefile``, and structural validation of the repository's ``Repo.mk``. It
never rewrites ``Repo.mk``, never inspects repository implementation beyond
the structural properties it can prove, and imports no consumer code.
"""

from __future__ import annotations

import json
import pathlib
import re
from collections.abc import Mapping
from typing import Any, NoReturn

PHASES: tuple[str, ...] = ("setup", "validate", "check", "test")
V2_SCHEMA_NAME = "l9.repo-execution/v2"
CONTRACT_PATH = pathlib.Path(".l9/repo-workflow.json")
MAKEFILE_PATH = pathlib.Path("Makefile")
REPO_MK_PATH = pathlib.Path("Repo.mk")
_CONTRACT_KEYS = frozenset({"schema", "facade", "required_phases"})

# Facade identity maps explicitly to its artifact. A released facade is
# immutable: an incompatible change is a new entry (make-v2), never an edit of
# make-v1. The contract's ``facade`` value must name a key of this mapping.
FACADES_DIR = pathlib.Path(__file__).resolve().parent / "facades"
FACADE_TEMPLATES: Mapping[str, pathlib.Path] = {
    "make-v1": FACADES_DIR / "make-v1.mk",
}

# The targets the facade itself owns. A repository implements ``repo-<phase>``
# leaves; redefining a facade verb in Repo.mk would replace the ABI instead of
# implementing it, so validation rejects it. This is Core's own ABI vocabulary,
# not a registry of any other tool's commands.
FACADE_TARGETS = frozenset({"help", *PHASES})
REQUIRED_LEAVES: tuple[str, ...] = tuple(f"repo-{phase}" for phase in PHASES)

# A rule line: target names before the first ':' that is not part of an
# assignment (':=' / '::='), not a recipe line (leading tab), not a comment.
_MAKE_RULE = re.compile(r"^(?!\t)([^\s:=#][^:=#]*?)\s*(?:::(?!=)|:(?![:=]))")
_PHONY_RULE = re.compile(r"^\.PHONY\s*:\s*(.*)$")


class ContractError(ValueError):
    """Raised when a contract, facade, Makefile, or Repo.mk is not admissible."""


def _fail(message: str) -> NoReturn:
    raise ContractError(message)


def load_contract(path: pathlib.Path) -> dict[str, Any]:
    """Read ``.l9/repo-workflow.json``; the root must be a JSON object."""

    if path.is_symlink() or not path.is_file():
        _fail(f"missing repository execution contract: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        _fail(f"unreadable repository execution contract {path}: {error}")
    if not isinstance(data, dict):
        _fail("repository execution contract root must be a JSON object")
    return data


def validate_v2_contract_data(data: object) -> dict[str, Any]:
    """Fail closed unless ``data`` is exactly the three-field V2 contract."""

    if not isinstance(data, dict) or not all(isinstance(key, str) for key in data):
        _fail("repo-execution/v2 contract must be a JSON object")
    keys = set(data)
    unknown = sorted(keys - _CONTRACT_KEYS)
    if unknown:
        _fail(f"repo-execution/v2 contract has unsupported keys: {', '.join(unknown)}")
    missing = sorted(_CONTRACT_KEYS - keys)
    if missing:
        _fail(f"repo-execution/v2 contract missing keys: {', '.join(missing)}")
    if data["schema"] != V2_SCHEMA_NAME:
        _fail(f"repo-execution/v2 contract schema must be {V2_SCHEMA_NAME!r}")
    facade = data["facade"]
    if not isinstance(facade, str) or facade not in FACADE_TEMPLATES:
        _fail(
            "repo-execution/v2 contract requests an unsupported facade; supported: "
            + ", ".join(sorted(FACADE_TEMPLATES))
        )
    phases = data["required_phases"]
    if not isinstance(phases, list) or phases != list(PHASES):
        _fail(
            "repo-execution/v2 contract required_phases must be exactly "
            + json.dumps(list(PHASES))
        )
    return data


def render_facade(facade: str) -> bytes:
    """Return the immutable bytes of a released facade."""

    template = FACADE_TEMPLATES.get(facade)
    if template is None:
        _fail(f"unsupported facade: {facade!r}")
    if template.is_symlink() or not template.is_file():
        _fail(f"missing facade template for {facade!r}: {template}")
    return template.read_bytes()


def _logical_lines(text: str) -> list[tuple[int, str]]:
    """Join backslash continuations, keeping the first physical line number."""

    lines: list[tuple[int, str]] = []
    buffer = ""
    start = 0
    for number, raw in enumerate(text.splitlines(), start=1):
        if not buffer:
            start = number
        if raw.endswith("\\"):
            buffer += raw[:-1] + " "
            continue
        lines.append((start, buffer + raw))
        buffer = ""
    if buffer:
        lines.append((start, buffer))
    return lines


def make_target_declarations(text: str) -> list[tuple[int, str]]:
    """Return ``(line, target)`` for every explicit rule in a Make fragment.

    Special targets such as ``.PHONY`` are not repository targets and are
    skipped. Only structural properties are recognized; this is not a GNU Make
    parser.
    """

    declarations: list[tuple[int, str]] = []
    for number, line in _logical_lines(text):
        match = _MAKE_RULE.match(line)
        if not match:
            continue
        declarations.extend(
            (number, name)
            for name in match.group(1).split()
            if not name.startswith(".")
        )
    return declarations


def make_phony_targets(text: str) -> set[str]:
    """Return every target named by a ``.PHONY:`` declaration."""

    phony: set[str] = set()
    for _, line in _logical_lines(text):
        match = _PHONY_RULE.match(line.split("#", 1)[0].rstrip())
        if match:
            phony.update(match.group(1).split())
    return phony


def validate_repo_mk_text(text: str) -> None:
    """Structural ABI validation of a repository-owned ``Repo.mk``.

    Required leaves must be declared and ``.PHONY`` (so a same-named file can
    never suppress a phase), and the facade's own verbs may not be redefined.
    Everything else in the file is the repository's business.
    """

    declarations = make_target_declarations(text)
    declared = {target for _, target in declarations}
    phony = make_phony_targets(text)
    for leaf in REQUIRED_LEAVES:
        if leaf not in declared:
            _fail(f"Repo.mk is missing required implementation target: {leaf}")
        if leaf not in phony:
            _fail(f"Repo.mk required target {leaf} must be declared .PHONY")
    for number, target in declarations:
        if target in FACADE_TARGETS:
            _fail(
                f"Repo.mk:{number} redefines facade target {target!r}; "
                f"implement repo-{target} instead"
            )


class RepositoryExecution:
    """Non-mutating validator and one-directional reconciler for a consumer."""

    def __init__(self, root: pathlib.Path) -> None:
        self.root = root.resolve()

    def contract(self) -> dict[str, Any]:
        return validate_v2_contract_data(load_contract(self.root / CONTRACT_PATH))

    def facade(self) -> str:
        facade = self.contract()["facade"]
        assert isinstance(facade, str)
        return facade

    def repo_mk_text(self) -> str:
        path = self.root / REPO_MK_PATH
        if path.is_symlink() or not path.is_file():
            _fail(f"missing repository implementation: {REPO_MK_PATH}")
        return path.read_text(encoding="utf-8", errors="replace")

    def verify_makefile(self) -> None:
        """The generated facade must match its template byte-for-byte."""

        expected = render_facade(self.facade())
        path = self.root / MAKEFILE_PATH
        if path.is_symlink() or not path.is_file():
            _fail(f"missing generated {MAKEFILE_PATH}; run reconcile")
        if path.read_bytes() != expected:
            _fail(f"{MAKEFILE_PATH} drift from facade template; run reconcile")

    def validate(self) -> None:
        """Contract, generated facade, and Repo.mk ABI, without mutation."""

        self.contract()
        self.verify_makefile()
        validate_repo_mk_text(self.repo_mk_text())

    def reconcile(self) -> bool:
        """Write the facade into ``Makefile``; never touch ``Repo.mk``.

        Returns ``True`` when the file changed. Idempotent: a second call on
        the same tree writes nothing.
        """

        expected = render_facade(self.facade())
        path = self.root / MAKEFILE_PATH
        if path.is_symlink():
            _fail(f"{MAKEFILE_PATH} must not be a symlink")
        if path.is_file() and path.read_bytes() == expected:
            return False
        path.write_bytes(expected)
        return True
