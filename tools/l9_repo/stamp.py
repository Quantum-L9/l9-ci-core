"""Core-owned consumer files: one stamp, one byte-identity proof.

A *Core-owned* consumer file is one a repository never edits. Core renders it
from data in this Core revision and the repository carries the bytes
unchanged:

========================  ==========================================  ==============
consumer path             Core source                                 profiles
========================  ==========================================  ==============
.l9/repo-workflow.json    the exact Repository Execution V2 contract  all
Makefile                  ``tools/l9_repo/facades/make-v1.mk``        all
.gitleaks.toml            ``presets/security/.gitleaks.toml``         all
requirements-ci.txt       rendered from ``toolchain-lock.json``       python
.python-version           rendered from ``toolchain-lock.json``       python
.pre-commit-config.yaml   rendered from ``toolchain-lock.json``       python
uv.toml                   rendered from ``toolchain-lock.json``       python
ruff.toml                 ``presets/python-tooling/ruff.toml``                python
mypy.ini                  ``presets/python-tooling/mypy.ini``                 python
pytest.ini                ``presets/python-tooling/pytest.ini``               python
biome.json                ``presets/typescript/biome.json``           typescript
.biomeignore              ``presets/typescript/.biomeignore``         typescript
.editorconfig             ``presets/typescript/.editorconfig``        typescript
========================  ==========================================  ==============

``python+typescript`` is the union.

Deliberately not stamped (decision record: ``AGENTS.md`` §25):

* no Semgrep configuration: the rulesets are ``l9-ci-sdk``'s and their
  blocking/advisory policy is Core's ``resolve-governance`` data, both run
  centrally by ``org-ci.yml``. A consumer-side Semgrep file would be a second
  source for the same semantics;
* no Pyright configuration: strict mypy is the single organization CI type
  gate, and Pyright is a gate nowhere (``AGENTS.md`` §26);
* no ESLint, Prettier, tsconfig or vitest configuration, and no Node pin:
  Core runs none of them, so none is locked (``AGENTS.md`` §26).

``.pre-commit-config.yaml`` uses only ``repo: local`` hooks with
``language: unsupported`` (pre-commit 4.4.0's name for ``system``): each hook
runs the tool ``requirements-ci.txt`` installed from the same lock, so a hook
can never carry a second version pin that drifts from the lock.

Product-owned files (``Repo.mk``,
``pyproject.toml``, ``package.json``, source) are never written: the file set
above is closed, and nothing outside it is opened for writing.

``stamp`` writes the profile's files and the stamp record
``.l9/core-stamp.json`` (``l9.core-stamp/v1``), which names the Core revision
the bytes came from and the sha256 of every stamped file. ``verify_stamp``
re-renders each file from *this* Core checkout and reports every file whose
bytes differ, are missing, or disagree with the record. Whether a finding
blocks is the caller's decision; the CLI ``validate`` and the organization
admission bridge both fail on any finding (``AGENTS.md`` §25).

Everything here is standard library only, because the admission bridge
imports it from a pinned Core checkout with no third-party packages.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
from collections.abc import Mapping
from typing import Any, NoReturn

from .contract import (
    CONTRACT_PATH,
    MAKEFILE_PATH,
    PHASES,
    V2_SCHEMA_NAME,
    ContractError,
    render_facade,
)

CORE_ROOT = pathlib.Path(__file__).resolve().parents[2]
CORE_REPOSITORY = "Quantum-L9/l9-ci-core"
STAMP_RECORD_PATH = pathlib.Path(".l9/core-stamp.json")
STAMP_SCHEMA = "l9.core-stamp/v1"
STAMP_FACADE = "make-v1"

PRESETS_DIR = CORE_ROOT / "presets"
TOOLCHAIN_LOCK = (
    CORE_ROOT / ".github" / "actions" / "install-consumer-ci" / "toolchain-lock.json"
)
# The pip-installable consumer tools, in the order of the canonical
# install-consumer-ci/requirements-consumer-ci.txt (test-bound to it).
REQUIREMENTS_TOOLS: tuple[str, ...] = (
    "pip",
    "pre-commit",
    "ruff",
    "mypy",
    "pytest",
    "uv",
)

PROFILE_PYTHON = "python"
PROFILE_TYPESCRIPT = "typescript"
PROFILE_BOTH = "python+typescript"
PROFILES: tuple[str, ...] = (PROFILE_PYTHON, PROFILE_TYPESCRIPT, PROFILE_BOTH)

GITLEAKS_CONFIG_PATH = ".gitleaks.toml"
_COMMON: tuple[str, ...] = (
    CONTRACT_PATH.as_posix(),
    MAKEFILE_PATH.as_posix(),
    GITLEAKS_CONFIG_PATH,
)
PYTHON_VERSION_PATH = ".python-version"
PRE_COMMIT_PATH = ".pre-commit-config.yaml"
UV_CONFIG_PATH = "uv.toml"
_PYTHON: tuple[str, ...] = (
    "requirements-ci.txt",
    PYTHON_VERSION_PATH,
    PRE_COMMIT_PATH,
    UV_CONFIG_PATH,
    "ruff.toml",
    "mypy.ini",
    "pytest.ini",
)
_TYPESCRIPT: tuple[str, ...] = ("biome.json", ".biomeignore", ".editorconfig")
PROFILE_FILES: Mapping[str, tuple[str, ...]] = {
    PROFILE_PYTHON: _COMMON + _PYTHON,
    PROFILE_TYPESCRIPT: _COMMON + _TYPESCRIPT,
    PROFILE_BOTH: _COMMON + _PYTHON + _TYPESCRIPT,
}
_PRESET_SOURCES: Mapping[str, pathlib.Path] = {
    GITLEAKS_CONFIG_PATH: PRESETS_DIR / "security" / ".gitleaks.toml",
    "ruff.toml": PRESETS_DIR / "python-tooling" / "ruff.toml",
    "mypy.ini": PRESETS_DIR / "python-tooling" / "mypy.ini",
    "pytest.ini": PRESETS_DIR / "python-tooling" / "pytest.ini",
    "biome.json": PRESETS_DIR / "typescript" / "biome.json",
    ".biomeignore": PRESETS_DIR / "typescript" / ".biomeignore",
    ".editorconfig": PRESETS_DIR / "typescript" / ".editorconfig",
}
_RECORD_KEYS = frozenset({"schema", "core", "profile", "files"})
_CORE_KEYS = frozenset({"repository", "revision"})
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+)+$")


def _fail(message: str) -> NoReturn:
    raise ContractError(message)


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def render_contract() -> bytes:
    """The exact three-field V2 contract, in its canonical byte form."""

    document = {
        "schema": V2_SCHEMA_NAME,
        "facade": STAMP_FACADE,
        "required_phases": list(PHASES),
    }
    return (json.dumps(document, indent=2) + "\n").encode("utf-8")


def _load_lock() -> dict[str, Any]:
    try:
        lock = json.loads(TOOLCHAIN_LOCK.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        _fail(f"unreadable Core toolchain lock {TOOLCHAIN_LOCK}: {error}")
    if not isinstance(lock, dict):
        _fail("Core toolchain lock must be a JSON object")
    return lock


def _lock_version(lock: Mapping[str, Any], tool: str) -> str:
    version = lock.get(tool)
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        _fail(f"Core toolchain lock has no exact version for {tool!r}")
    return version


def render_python_version() -> bytes:
    """``.python-version``: the exact interpreter Core's lock selects."""

    return (_lock_version(_load_lock(), "python") + "\n").encode("utf-8")


def render_pre_commit() -> bytes:
    """``.pre-commit-config.yaml`` bound to Core's toolchain lock.

    Every hook is ``repo: local`` and runs the lock-pinned tool that
    ``requirements-ci.txt`` installs (``python -m <tool>``); the only version
    the file names is ``minimum_pre_commit_version``, taken from the lock.
    """

    lock = _load_lock()
    for tool in ("ruff", "mypy"):
        _lock_version(lock, tool)
    minimum = _lock_version(lock, "pre-commit")
    text = f"""\
# Core-owned: rendered from l9-ci-core
# .github/actions/install-consumer-ci/toolchain-lock.json by
# `python3 -m tools.l9_repo --workspace <repo> stamp`. Do not edit; re-stamp
# from the Core revision org-ci.yml pins. Every hook runs the lock-pinned tool
# installed by `python -m pip install -r requirements-ci.txt`.
minimum_pre_commit_version: "{minimum}"
repos:
  - repo: local
    hooks:
      - id: ruff-check
        name: ruff check
        entry: python -m ruff check --fix
        language: unsupported
        types_or: [python, pyi]
        require_serial: true
      - id: ruff-format
        name: ruff format
        entry: python -m ruff format
        language: unsupported
        types_or: [python, pyi]
        require_serial: true
      - id: mypy
        name: mypy
        entry: python -m mypy
        language: unsupported
        types_or: [python, pyi]
        require_serial: true
"""
    return text.encode("utf-8")


def render_uv_config() -> bytes:
    """``uv.toml``: refuse any uv but the one Core's lock selects.

    ``required-version`` makes every uv command fail on a different uv
    version. A ``uv.toml`` takes precedence over ``[tool.uv]`` in
    ``pyproject.toml``, which uv then ignores: the product conforms
    (``AGENTS.md`` §26, no per-repository overlay).
    """

    version = _lock_version(_load_lock(), "uv")
    text = f"""\
# Core-owned: rendered from l9-ci-core
# .github/actions/install-consumer-ci/toolchain-lock.json by
# `python3 -m tools.l9_repo --workspace <repo> stamp`. Do not edit; re-stamp
# from the Core revision org-ci.yml pins. uv reads this file instead of
# [tool.uv] in pyproject.toml.
required-version = "=={version}"
"""
    return text.encode("utf-8")


def render_requirements() -> bytes:
    """``requirements-ci.txt`` pinned exactly to Core's toolchain lock."""

    lock = _load_lock()
    lines = [
        "# Core-owned: rendered from l9-ci-core",
        "# .github/actions/install-consumer-ci/toolchain-lock.json by",
        "# `python3 -m tools.l9_repo --workspace <repo> stamp`. Do not edit;",
        "# re-stamp from the Core revision org-ci.yml pins. Product dependencies",
        "# belong in pyproject.toml.",
    ]
    for tool in REQUIREMENTS_TOOLS:
        lines.append(f"{tool}=={_lock_version(lock, tool)}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def render_file(relative: str) -> bytes:
    """The bytes this Core revision owns for one consumer path."""

    if relative == CONTRACT_PATH.as_posix():
        return render_contract()
    if relative == MAKEFILE_PATH.as_posix():
        return render_facade(STAMP_FACADE)
    if relative == "requirements-ci.txt":
        return render_requirements()
    if relative == PYTHON_VERSION_PATH:
        return render_python_version()
    if relative == PRE_COMMIT_PATH:
        return render_pre_commit()
    if relative == UV_CONFIG_PATH:
        return render_uv_config()
    source = _PRESET_SOURCES.get(relative)
    if source is None:
        _fail(f"not a Core-owned consumer file: {relative}")
    if source.is_symlink() or not source.is_file():
        _fail(f"missing Core preset source for {relative}: {source}")
    return source.read_bytes()


def profile_files(profile: str) -> tuple[str, ...]:
    files = PROFILE_FILES.get(profile)
    if files is None:
        _fail(
            f"unsupported stamp profile {profile!r}; supported: {', '.join(PROFILES)}"
        )
    return files


def render_profile(profile: str) -> dict[str, bytes]:
    return {relative: render_file(relative) for relative in profile_files(profile)}


def _destination(root: pathlib.Path, relative: str) -> pathlib.Path:
    path = root / relative
    for parent in [path, *path.parents]:
        if parent == root:
            break
        if parent.is_symlink():
            _fail(f"refusing to write through a symlink: {parent.relative_to(root)}")
    return path


def stamp(root: pathlib.Path, profile: str, core_revision: str) -> list[str]:
    """Write every Core-owned file for ``profile`` plus the stamp record.

    Returns the paths whose bytes changed (the record included). Idempotent: a
    second call with the same profile and revision writes nothing.
    """

    root = root.resolve()
    if not root.is_dir():
        _fail(f"workspace is not a directory: {root}")
    if not _REVISION.fullmatch(core_revision):
        _fail(
            f"Core revision must be a full 40-character commit SHA: {core_revision!r}"
        )
    rendered = render_profile(profile)
    record = {
        "schema": STAMP_SCHEMA,
        "core": {"repository": CORE_REPOSITORY, "revision": core_revision},
        "profile": profile,
        "files": {
            relative: _digest(data) for relative, data in sorted(rendered.items())
        },
    }
    outputs = {
        **rendered,
        STAMP_RECORD_PATH.as_posix(): (json.dumps(record, indent=2) + "\n").encode(),
    }
    changed: list[str] = []
    for relative, data in outputs.items():
        path = _destination(root, relative)
        if path.is_file() and path.read_bytes() == data:
            continue
        if path.exists() and not path.is_file():
            _fail(f"refusing to replace a non-file: {relative}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        changed.append(relative)
    return changed


def load_stamp_record(root: pathlib.Path) -> dict[str, Any] | None:
    """Return the validated stamp record, ``None`` when absent."""

    path = root / STAMP_RECORD_PATH
    if path.is_symlink():
        _fail(f"{STAMP_RECORD_PATH} must not be a symlink")
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        _fail(f"unreadable stamp record {STAMP_RECORD_PATH}: {error}")
    if not isinstance(data, dict) or set(data) != _RECORD_KEYS:
        _fail(
            f"stamp record must be an object with exactly: {', '.join(sorted(_RECORD_KEYS))}"
        )
    if data["schema"] != STAMP_SCHEMA:
        _fail(f"stamp record schema must be {STAMP_SCHEMA!r}")
    core = data["core"]
    if not isinstance(core, dict) or set(core) != _CORE_KEYS:
        _fail("stamp record core must carry exactly repository and revision")
    if core["repository"] != CORE_REPOSITORY:
        _fail(f"stamp record core.repository must be {CORE_REPOSITORY!r}")
    if not isinstance(core["revision"], str) or not _REVISION.fullmatch(
        core["revision"]
    ):
        _fail("stamp record core.revision must be a full 40-character commit SHA")
    expected = set(
        profile_files(data["profile"]) if isinstance(data["profile"], str) else ()
    )
    if not expected:
        _fail("stamp record profile must be a string")
    files = data["files"]
    # A record from an earlier Core revision may predate a file the profile
    # now owns: that is drift (re-stamp), reported per file by verify_stamp,
    # not a malformed record. A record naming a file the profile does not own,
    # or naming none, is malformed.
    if not isinstance(files, dict) or not files or not set(files) <= expected:
        _fail(f"stamp record files must name only {data['profile']} profile files")
    for relative, digest in files.items():
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            _fail(f"stamp record digest for {relative} must be sha256:<64 hex>")
    return data


def verify_stamp(root: pathlib.Path) -> tuple[dict[str, Any] | None, list[str]]:
    """Compare every stamped file with this Core checkout's rendering.

    Returns ``(record, findings)``. ``record`` is ``None`` when the consumer
    carries no stamp record (nothing to verify). A malformed record raises
    ``ContractError``. Each finding names one file and why it drifted; an empty
    list proves every Core-owned file is byte-identical to this Core revision.
    """

    root = root.resolve()
    record = load_stamp_record(root)
    if record is None:
        return None, []
    findings: list[str] = []
    for relative, expected in render_profile(record["profile"]).items():
        path = root / relative
        if path.is_symlink() or not path.is_file():
            findings.append(f"{relative}: missing (Core-owned; re-stamp)")
            continue
        actual = path.read_bytes()
        recorded = record["files"].get(relative)
        if actual != expected:
            findings.append(
                f"{relative}: differs from Core-owned bytes at this Core revision "
                f"(stamped from {record['core']['revision']}; re-stamp, do not edit)"
            )
        elif recorded is None:
            findings.append(
                f"{relative}: Core-owned for the {record['profile']} profile but "
                "absent from the stamp record (stamped by an earlier Core; re-stamp)"
            )
        elif recorded != _digest(actual):
            findings.append(f"{relative}: stamp record digest does not match the file")
    return record, findings
