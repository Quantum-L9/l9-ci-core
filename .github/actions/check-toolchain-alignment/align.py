"""Report consumer toolchain pins that drift from Core's toolchain lock.

Core owns exact CI tool versions (``.l9/architecture.yaml``); consumers may not
own central tool versions. Consumers still carry local copies — a dev-dependency
range, a pre-commit ``rev``, a ``biome.json`` ``$schema`` — and those copies
drift. This action reports each one against the lock with ``file:line`` so the
drift is visible on the pull request. It is advisory: a consumer finding never
fails the job. Only an unreadable Core lock or tool list (a Core contract
failure) exits non-zero.

Inputs are Core data from the same pinned revision as this action
(``../install-consumer-ci/toolchain-lock.json`` and ``core-owned-tools.json``)
and the consumer checkout at ``GITHUB_WORKSPACE``. Parsing is standard library
only. Paths are repository-relative; a symlink is never followed.

Classes per declaration:

``exact_match``         the declaration pins exactly the locked version
``exact_drift``         it pins exactly a different version
``range_admits_lock``   a range that includes the locked version
``range_excludes_lock`` a range that excludes the locked version
``unpinned``            no version constraint at all
``unparseable``         a constraint this checker cannot evaluate
``range_admits_older``  a ``requires-python`` that admits a Python older than
                        the locked minor version (Python is locked to one
                        minor version, operator decision 2026-11-20)

Surfaces: ``pyproject.toml`` (optional dependencies, dependency groups, uv
dev-dependencies, ``requires-python``), ``uv.lock``, requirement and
``*-bootstrap.txt`` files, ``.pre-commit-config.yaml``, ``biome.json``,
``.python-version``, and the repository's own workflows (``setup-python``
``python-version`` and ``PYTHON_VERSION`` literals, ``pip install
tool==version``, ``setup-uv`` ``version``, ``UV_VERSION`` and
``GITLEAKS_VERSION`` literals, gitleaks release download URLs).

Exit codes: ``0`` report written, ``3`` Core lock or tool list unreadable.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
from collections.abc import Iterator
from typing import Any

SCHEMA = "l9.toolchain-alignment/v1"
HERE = pathlib.Path(__file__).resolve().parent
CORE_DATA = HERE.parent / "install-consumer-ci"
LOCK_FILE = CORE_DATA / "toolchain-lock.json"
TOOLS_FILE = CORE_DATA / "core-owned-tools.json"

DRIFT = ("exact_drift", "range_excludes_lock", "range_admits_older")
UNCERTAIN = ("unpinned", "unparseable")

VERSION = re.compile(r"^v?(\d+(?:\.\d+)*)$")
PEP508 = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$")
CLAUSE = re.compile(r"^(===|==|~=|!=|<=|>=|<|>)\s*(.+)$")
BIOME_SCHEMA = re.compile(
    r"\"\$schema\"\s*:\s*\"[^\"]*/schemas/([^/\"]+)/schema\.json\""
)


class CoreContractError(RuntimeError):
    """Core's own data is unreadable; the report would be undeterminable."""


Finding = dict[str, Any]


def _release(text: str) -> tuple[int, ...] | None:
    match = VERSION.match(text.strip())
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def _cmp(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    width = max(len(left), len(right))
    a = left + (0,) * (width - len(left))
    b = right + (0,) * (width - len(right))
    return (a > b) - (a < b)


def _pep440_clause(op: str, raw: str, lock: tuple[int, ...]) -> bool | None:
    raw = raw.strip()
    if raw.endswith(".*") and op in ("==", "!="):
        prefix = _release(raw[:-2])
        if prefix is None:
            return None
        matches = lock[: len(prefix)] == prefix
        return matches if op == "==" else not matches
    bound = _release(raw)
    if bound is None:
        return None
    order = _cmp(lock, bound)
    if op in ("==", "==="):
        return order == 0
    if op == "!=":
        return order != 0
    if op == ">=":
        return order >= 0
    if op == "<=":
        return order <= 0
    if op == ">":
        return order > 0
    if op == "<":
        return order < 0
    # ~=X.Y.Z  ==  >=X.Y.Z, ==X.Y.*
    if len(bound) < 2:
        return None
    return order >= 0 and lock[: len(bound) - 1] == bound[:-1]


def classify_pep440(spec: str, lock_version: str) -> tuple[str, str]:
    """Classify a PEP 440 specifier set. Returns (class, declared)."""
    spec = spec.split(";", 1)[0].strip()
    lock = _release(lock_version)
    if lock is None:
        raise CoreContractError(f"lock version {lock_version!r} is not x.y.z")
    if not spec:
        return "unpinned", ""
    clauses = [part.strip() for part in spec.split(",") if part.strip()]
    parsed: list[tuple[str, str]] = []
    for clause in clauses:
        match = CLAUSE.match(clause)
        if not match:
            return "unparseable", spec
        parsed.append((match.group(1), match.group(2).strip()))
    if len(parsed) == 1 and parsed[0][0] in ("==", "===") and "*" not in parsed[0][1]:
        pinned = _release(parsed[0][1])
        if pinned is None:
            return "unparseable", spec
        return ("exact_match" if _cmp(pinned, lock) == 0 else "exact_drift"), spec
    verdicts = [_pep440_clause(op, raw, lock) for op, raw in parsed]
    if any(verdict is None for verdict in verdicts):
        return "unparseable", spec
    return ("range_admits_lock" if all(verdicts) else "range_excludes_lock"), spec


def classify_exact(declared: str, lock_version: str) -> str:
    pinned = _release(declared)
    lock = _release(lock_version)
    if lock is None:
        raise CoreContractError(f"lock version {lock_version!r} is not x.y.z")
    if pinned is None:
        return "unparseable"
    return "exact_match" if _cmp(pinned, lock) == 0 else "exact_drift"


def classify_npm(spec: str, lock_version: str) -> str:
    """Classify the npm range forms Biome consumers actually write."""
    spec = spec.strip()
    lock = _release(lock_version)
    if lock is None:
        raise CoreContractError(f"lock version {lock_version!r} is not x.y.z")
    if spec in ("", "*", "latest", "x"):
        return "unpinned"
    if "||" in spec or " - " in spec:
        return "unparseable"
    exact = spec[1:] if spec.startswith("=") else spec
    if _release(exact) is not None and not exact.startswith("v"):
        return classify_exact(exact, lock_version)
    if spec[0] in "^~":
        base = _release(spec[1:])
        if base is None or len(base) != 3:
            return "unparseable"
        if _cmp(lock, base) < 0:
            return "range_excludes_lock"
        if spec[0] == "~":
            admits = lock[:2] == base[:2]
        elif base[0] != 0:
            admits = lock[0] == base[0]
        elif base[1] != 0:
            admits = lock[:2] == base[:2]
        else:
            admits = lock == base
        return "range_admits_lock" if admits else "range_excludes_lock"
    parts = spec.split()
    verdicts: list[bool | None] = []
    for part in parts:
        match = re.match(r"^(<=|>=|<|>)(.+)$", part)
        if not match:
            return "unparseable"
        verdicts.append(_pep440_clause(match.group(1), match.group(2), lock))
    if any(verdict is None for verdict in verdicts):
        return "unparseable"
    return "range_admits_lock" if all(verdicts) else "range_excludes_lock"


def load_core() -> tuple[dict[str, str], dict[str, Any]]:
    try:
        lock = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
        tools = json.loads(TOOLS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CoreContractError(f"Core toolchain data unreadable: {error}") from error
    if not isinstance(lock, dict) or not all(
        isinstance(value, str) for value in lock.values()
    ):
        raise CoreContractError("toolchain-lock.json must be a flat string map")
    if tools.get("schema") != "l9.core-owned-tools/v1":
        raise CoreContractError("core-owned-tools.json has an unknown schema")
    for tool in tools.get("tools", []):
        if tool.get("name") not in lock:
            raise CoreContractError(f"core-owned tool {tool.get('name')!r} has no lock")
    return lock, tools


def _index(tools: dict[str, Any], ecosystem: str) -> dict[str, str]:
    """Map a dependency identifier in one ecosystem to its tool name."""
    index: dict[str, str] = {}
    for tool in tools["tools"]:
        for identifier in tool.get("ecosystems", {}).get(ecosystem, []):
            key = _normalise(identifier, ecosystem)
            index[key] = tool["name"]
    return index


def _normalise(identifier: str, ecosystem: str) -> str:
    if ecosystem in ("pip", "uv"):
        return re.sub(r"[-_.]+", "-", identifier).lower()
    if ecosystem == "pre-commit":
        value = identifier.strip().rstrip("/").lower()
        return value[:-4] if value.endswith(".git") else value
    return identifier


def _safe_file(root: pathlib.Path, relative: str) -> pathlib.Path | None:
    path = root / relative
    if path.is_symlink() or not path.is_file():
        return None
    resolved = path.resolve()
    if root not in resolved.parents:
        return None
    return path


def _read_text(path: pathlib.Path) -> str | None:
    """Consumer bytes; None when they are not UTF-8 text (reported unparseable)."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _table(value: Any) -> dict[str, Any] | None:
    """A TOML/JSON table, or None when the consumer wrote another type."""
    return value if isinstance(value, dict) else None


def _line_of(lines: list[str], needle: str, default: int = 1) -> int:
    for number, line in enumerate(lines, start=1):
        if needle in line:
            return number
    return default


def _finding(
    tool: str, path: str, line: int, source: str, declared: str, lock: str, kind: str
) -> Finding:
    return {
        "class": kind,
        "declared": declared,
        "line": line,
        "lock": lock,
        "path": path,
        "source": source,
        "tool": tool,
    }


def scan_pyproject(
    root: pathlib.Path, lock: dict[str, str], pip: dict[str, str]
) -> Iterator[Finding]:
    path = _safe_file(root, "pyproject.toml")
    if path is None:
        return
    import tomllib

    unparseable = _finding("*", "pyproject.toml", 1, "pyproject", "", "", "unparseable")
    text = _read_text(path)
    if text is None:
        yield unparseable
        return
    lines = text.splitlines()
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        yield unparseable
        return
    # Valid TOML can still put a string where a table belongs. That is a
    # consumer finding, never a Core failure: report it and scan the rest.
    malformed = False

    def table(parent: dict[str, Any], key: str) -> dict[str, Any]:
        nonlocal malformed
        value = _table(parent.get(key, {}))
        if value is None:
            malformed = True
            return {}
        return value

    groups: list[tuple[str, Any]] = []
    project = table(data, "project")
    for name, deps in sorted(table(project, "optional-dependencies").items()):
        groups.append((f"project.optional-dependencies.{name}", deps))
    for name, deps in sorted(table(data, "dependency-groups").items()):
        groups.append((f"dependency-groups.{name}", deps))
    uv = table(table(data, "tool"), "uv")
    if "dev-dependencies" in uv:
        groups.append(("tool.uv.dev-dependencies", uv["dev-dependencies"]))
    for _source, deps in groups:
        if not isinstance(deps, list):
            malformed = True
    if malformed:
        yield unparseable
    for source, deps in groups:
        if not isinstance(deps, list):
            continue
        for requirement in deps:
            if not isinstance(requirement, str):
                continue  # e.g. {include-group = "..."}
            match = PEP508.match(requirement)
            if not match:
                continue
            tool = pip.get(_normalise(match.group(1), "pip"))
            if tool is None:
                continue
            kind, declared = classify_pep440(match.group(3), lock[tool])
            line = _line_of(lines, f'"{requirement}"', _line_of(lines, requirement))
            yield _finding(
                tool, "pyproject.toml", line, source, declared, lock[tool], kind
            )


def scan_uv_lock(
    root: pathlib.Path, lock: dict[str, str], pip: dict[str, str]
) -> Iterator[Finding]:
    path = _safe_file(root, "uv.lock")
    if path is None:
        return
    text = _read_text(path)
    if text is None:
        yield _finding("*", "uv.lock", 1, "uv.lock", "", "", "unparseable")
        return
    current: str | None = None
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("["):
            current = None
            continue
        match = re.match(r'^name\s*=\s*"([^"]+)"$', stripped)
        if match:
            current = pip.get(_normalise(match.group(1), "pip"))
            continue
        match = re.match(r'^version\s*=\s*"([^"]+)"$', stripped)
        if match and current is not None:
            yield _finding(
                current,
                "uv.lock",
                number,
                "uv.lock",
                match.group(1),
                lock[current],
                classify_exact(match.group(1), lock[current]),
            )
            current = None


SKIP_DIRECTORIES = frozenset({".git", ".venv", "venv", "node_modules", ".tox"})


def _requirement_files(root: pathlib.Path) -> list[str]:
    """Requirement-format files: ``*requirements*.txt`` and ``*-bootstrap.txt``.

    Searched to depth three, skipping virtual environments and vendored trees,
    so a ``tools/deps/uv-bootstrap.txt`` is read as well as the root files.
    """
    found: list[str] = []
    for depth in range(3):
        for candidate in sorted(root.glob("/".join(["*"] * (depth + 1)))):
            relative = candidate.relative_to(root)
            if any(part in SKIP_DIRECTORIES for part in relative.parts):
                continue
            name = candidate.name
            if name.endswith(".txt") and (
                "requirements" in name or name.endswith("-bootstrap.txt")
            ):
                found.append(relative.as_posix())
    return found


def scan_requirements(
    root: pathlib.Path, lock: dict[str, str], pip: dict[str, str]
) -> Iterator[Finding]:
    for relative in _requirement_files(root):
        path = _safe_file(root, relative)
        if path is None:
            continue
        content = _read_text(path)
        if content is None:
            yield _finding("*", relative, 1, "requirements", "", "", "unparseable")
            continue
        for number, line in enumerate(content.splitlines(), 1):
            text = line.split(" #", 1)[0].strip().rstrip("\\").strip()
            if not text or text.startswith(("#", "-")):
                continue
            text = text.split(" --hash", 1)[0].strip()
            match = PEP508.match(text)
            if not match:
                continue
            tool = pip.get(_normalise(match.group(1), "pip"))
            if tool is None:
                continue
            kind, declared = classify_pep440(match.group(3), lock[tool])
            yield _finding(
                tool, relative, number, "requirements", declared, lock[tool], kind
            )


def scan_pre_commit(
    root: pathlib.Path, lock: dict[str, str], repos: dict[str, str]
) -> Iterator[Finding]:
    relative = ".pre-commit-config.yaml"
    path = _safe_file(root, relative)
    if path is None:
        return
    text = _read_text(path)
    if text is None:
        yield _finding("*", relative, 1, "pre-commit", "", "", "unparseable")
        return
    current: str | None = None
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.split(" #", 1)[0].strip()
        match = re.match(r"^-?\s*repo:\s*['\"]?([^'\"\s]+)['\"]?$", stripped)
        if match:
            current = repos.get(_normalise(match.group(1), "pre-commit"))
            continue
        match = re.match(r"^-?\s*rev:\s*['\"]?([^'\"\s]+)['\"]?$", stripped)
        if match and current is not None:
            declared = match.group(1)
            yield _finding(
                current,
                relative,
                number,
                "pre-commit",
                declared,
                lock[current],
                classify_exact(declared, lock[current]),
            )
            current = None


def scan_biome(
    root: pathlib.Path, lock: dict[str, str], npm: dict[str, str]
) -> Iterator[Finding]:
    tool = npm.get("@biomejs/biome")
    if tool is None:
        return
    for relative in ("biome.json", "biome.jsonc"):
        path = _safe_file(root, relative)
        if path is None:
            continue
        content = _read_text(path)
        if content is None:
            yield _finding(tool, relative, 1, "$schema", "", lock[tool], "unparseable")
            continue
        for number, line in enumerate(content.splitlines(), 1):
            match = BIOME_SCHEMA.search(line)
            if match:
                declared = match.group(1)
                kind = classify_exact(declared, lock[tool])
                yield _finding(
                    tool, relative, number, "$schema", declared, lock[tool], kind
                )
                break
        else:
            yield _finding(tool, relative, 1, "$schema", "", lock[tool], "unpinned")
    path = _safe_file(root, "package.json")
    if path is None:
        return
    text = _read_text(path)
    try:
        manifest = _table(json.loads(text)) if text is not None else None
    except ValueError:
        manifest = None
    if text is None or manifest is None:
        yield _finding(
            tool, "package.json", 1, "package.json", "", lock[tool], "unparseable"
        )
        return
    lines = text.splitlines()
    for section in (
        "dependencies",
        "devDependencies",
        "optionalDependencies",
        "peerDependencies",
    ):
        deps = manifest.get(section)
        if not isinstance(deps, dict) or "@biomejs/biome" not in deps:
            continue
        declared = f"{deps['@biomejs/biome']}"
        yield _finding(
            tool,
            "package.json",
            _line_of(lines, '"@biomejs/biome"'),
            f"package.json {section}",
            declared,
            lock[tool],
            classify_npm(declared, lock[tool]),
        )


# Linear patterns only: a workflow line is split into key and value by
# `_key_value`, so no pattern needs to skip optional leading whitespace or
# dashes (Sonar S8786, super-linear backtracking).
PYTHON_LITERAL = re.compile(r"\b\d+\.\d+(?:\.\d+)?\b")
PIP_PIN = re.compile(r"(?<![\w.-])([A-Za-z0-9][A-Za-z0-9._-]*)==(v?\d+(?:\.\d+)*)\b")
STEP_KEYS = frozenset({"uses", "name", "run", "id", "with", "if"})
PYTHON_KEYS = frozenset({"python-version", "PYTHON_VERSION"})
GITLEAKS_URL = re.compile(r"gitleaks/releases/download/v(\d+\.\d+\.\d+)/")


def classify_python(declared: str, lock_version: str) -> str:
    """A Python declaration against the locked interpreter (minor version bound)."""
    lock = _release(lock_version)
    pinned = _release(declared)
    if lock is None:
        raise CoreContractError(f"lock version {lock_version!r} is not x.y.z")
    if pinned is None or len(pinned) < 2:
        return "unparseable"
    if pinned[:2] != lock[:2]:
        return "exact_drift"
    if len(pinned) == 2:
        return "range_admits_lock"
    return "exact_match" if _cmp(pinned, lock) == 0 else "exact_drift"


def _older_interpreters(lock: tuple[int, ...]) -> Iterator[str]:
    """Every release of every minor older than the lock's, in the lock's major.

    Probing a single sentinel is defeatable (``>=3.10,!=3.11.*`` excludes
    3.11.99 yet admits all of 3.10), so the effective lower bound is found by
    probing each older minor at every patch level a specifier could single out.
    """
    for minor in range(lock[1] - 1, -1, -1):
        for patch in range(100):
            yield f"{lock[0]}.{minor}.{patch}"


def classify_requires_python(spec: str, lock_version: str) -> tuple[str, str]:
    """``requires-python`` must admit the lock and nothing older than its minor."""
    kind, declared = classify_pep440(spec, lock_version)
    if kind != "range_admits_lock":
        return kind, declared
    lock = _release(lock_version)
    assert lock is not None
    for older in _older_interpreters(lock):
        older_kind, _ = classify_pep440(spec, older)
        if older_kind in ("range_admits_lock", "exact_match"):
            return "range_admits_older", declared
    return kind, declared


def scan_python(root: pathlib.Path, lock: dict[str, str]) -> Iterator[Finding]:
    """``requires-python`` and ``.python-version`` against the locked Python."""
    if "python" not in lock:
        return
    path = _safe_file(root, "pyproject.toml")
    text = _read_text(path) if path is not None else None
    if text is not None:
        import tomllib

        try:
            project = tomllib.loads(text).get("project", {})
        except tomllib.TOMLDecodeError:
            project = {}  # reported by scan_pyproject
        spec = project.get("requires-python") if isinstance(project, dict) else None
        if isinstance(spec, str):
            kind, declared = classify_requires_python(spec, lock["python"])
            line = _line_of(text.splitlines(), "requires-python")
            yield _finding(
                "python",
                "pyproject.toml",
                line,
                "requires-python",
                declared,
                lock["python"],
                kind,
            )
    marker = _safe_file(root, ".python-version")
    content = _read_text(marker) if marker is not None else None
    if content is not None and content.strip():
        declared = content.strip().splitlines()[0].strip()
        yield _finding(
            "python",
            ".python-version",
            1,
            "python-version-file",
            declared,
            lock["python"],
            classify_python(declared, lock["python"]),
        )


def _workflow_python(value: str) -> list[str]:
    """Literal Python versions in a ``python-version``/``PYTHON_VERSION`` value.

    ``${{ env.PYTHON_VERSION }}`` names a variable reported where it is set;
    ``${{ vars.X || '3.12' }}`` contributes its literal fallback.
    """
    if "${{" in value and "'" not in value and '"' not in value:
        return []
    return PYTHON_LITERAL.findall(value.replace("${{", "").replace("}}", ""))


def scan_workflows(
    root: pathlib.Path, lock: dict[str, str], pip: dict[str, str]
) -> Iterator[Finding]:
    """The repository's own workflows: interpreter, installs, uv and gitleaks."""
    directory = root / ".github" / "workflows"
    if directory.is_symlink() or not directory.is_dir():
        return
    for candidate in sorted(directory.iterdir()):
        if candidate.suffix not in (".yml", ".yaml"):
            continue
        relative = candidate.relative_to(root).as_posix()
        path = _safe_file(root, relative)
        if path is None:
            continue
        text = _read_text(path)
        if text is None:
            yield _finding("*", relative, 1, "workflow", "", "", "unparseable")
            continue
        yield from _scan_workflow_text(relative, text.splitlines(), lock, pip)


def _key_value(code: str) -> tuple[bool, str, str]:
    """``(starts a list item, key, value)`` of one YAML-ish workflow line."""
    text = code.strip()
    item = text.startswith("-")
    if item:
        text = text[1:].lstrip()
    key, separator, value = text.partition(":")
    if not separator:
        return item, "", ""
    return item, key.strip(), value.strip()


def _first_token(value: str) -> str:
    return value.split(None, 1)[0].strip("'\"") if value.strip() else ""


def _workflow_python_findings(
    relative: str, number: int, key: str, value: str, lock: dict[str, str]
) -> Iterator[Finding]:
    if key not in PYTHON_KEYS or "python" not in lock or not value:
        return
    for declared in _workflow_python(value):
        yield _finding(
            "python",
            relative,
            number,
            "workflow",
            declared,
            lock["python"],
            classify_python(declared, lock["python"]),
        )


def _workflow_pip_findings(
    relative: str, number: int, code: str, lock: dict[str, str], pip: dict[str, str]
) -> Iterator[Finding]:
    if "pip install" not in code and "pip3 install" not in code:
        return
    for name, version in PIP_PIN.findall(code):
        tool = pip.get(_normalise(name, "pip"))
        if tool is not None:
            yield _finding(
                tool,
                relative,
                number,
                "workflow",
                version,
                lock[tool],
                classify_exact(version, lock[tool]),
            )


def _exact_workflow_finding(
    tool: str, relative: str, number: int, declared: str, lock: dict[str, str]
) -> Finding:
    return _finding(
        tool,
        relative,
        number,
        "workflow",
        declared,
        lock[tool],
        classify_exact(declared, lock[tool]),
    )


def _gitleaks_declared(code: str, key: str, value: str) -> str | None:
    if key == "GITLEAKS_VERSION":
        return _first_token(value) or None
    # Shell assignment inside a `run:` block: `GITLEAKS_VERSION="8.24.3"`,
    # optionally `export`ed. A reference (`${GITLEAKS_VERSION}`) is not one.
    _, assigned, rest = code.partition("GITLEAKS_VERSION=")
    if assigned and not code.partition("GITLEAKS_VERSION=")[0].endswith("{"):
        declared = _first_token(rest)
        if declared and "$" not in declared:
            return declared
    url = GITLEAKS_URL.search(code)
    if url and "${" not in url.group(0):
        return url.group(1)
    return None


def _scan_workflow_text(
    relative: str, lines: list[str], lock: dict[str, str], pip: dict[str, str]
) -> Iterator[Finding]:
    in_setup_uv = 0  # lines left in the `with:` block of a setup-uv step
    for number, line in enumerate(lines, 1):
        code = line.split(" #", 1)[0]
        item, key, value = _key_value(code)
        yield from _workflow_python_findings(relative, number, key, value, lock)
        yield from _workflow_pip_findings(relative, number, code, lock, pip)
        if "astral-sh/setup-uv@" in code:
            in_setup_uv = 8
        elif item and key in STEP_KEYS:
            in_setup_uv = 0  # a new step ends the setup-uv `with:` block
        elif in_setup_uv:
            in_setup_uv -= 1
        uv_key = key == "UV_VERSION" or (key == "version" and in_setup_uv > 0)
        if uv_key and "uv" in lock and _first_token(value):
            yield _exact_workflow_finding(
                "uv", relative, number, _first_token(value), lock
            )
        gitleaks = _gitleaks_declared(code, key, value) if "gitleaks" in lock else None
        if gitleaks is not None:
            yield _exact_workflow_finding("gitleaks", relative, number, gitleaks, lock)


def scan(
    root: pathlib.Path, lock: dict[str, str], tools: dict[str, Any]
) -> list[Finding]:
    pip = _index(tools, "pip")
    repos = _index(tools, "pre-commit")
    npm = _index(tools, "npm")
    findings: list[Finding] = [
        *scan_pyproject(root, lock, pip),
        *scan_uv_lock(root, lock, pip),
        *scan_requirements(root, lock, pip),
        *scan_pre_commit(root, lock, repos),
        *scan_biome(root, lock, npm),
        *scan_python(root, lock),
        *scan_workflows(root, lock, pip),
    ]
    return sorted(findings, key=lambda item: (item["path"], item["line"], item["tool"]))


def report(findings: list[Finding], lock: dict[str, str]) -> dict[str, Any]:
    if not findings:
        status = "not_applicable"
    elif any(item["class"] in DRIFT for item in findings):
        status = "drift"
    else:
        status = "aligned"
    return {
        "findings": findings,
        "lock": dict(sorted(lock.items())),
        "schema": SCHEMA,
        "status": status,
    }


def _annotate(findings: list[Finding]) -> None:
    for item in findings:
        message = (
            f"{item['tool']} {item['declared'] or '(no version)'} "
            f"[{item['class']}] — Core lock is {item['lock']}"
        )
        if item["class"] in DRIFT:
            print(f"::warning file={item['path']},line={item['line']}::{message}")
        elif item["class"] in UNCERTAIN:
            print(f"::notice file={item['path']},line={item['line']}::{message}")


def _summary(document: dict[str, Any]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [
        "### Toolchain alignment (Core lock, advisory)",
        "",
        f"- Status: `{document['status']}`",
    ]
    if document["findings"]:
        lines += [
            "",
            "| Tool | Location | Declared | Lock | Class |",
            "|---|---|---|---|---|",
        ]
        for item in document["findings"]:
            lines.append(
                f"| {item['tool']} | `{item['path']}:{item['line']}` | "
                f"`{item['declared'] or '—'}` | `{item['lock']}` | {item['class']} |"
            )
    with open(path, "a", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report consumer toolchain drift")
    parser.add_argument("--output", default=".l9/runtime/toolchain-alignment.json")
    args = parser.parse_args(argv)

    root = pathlib.Path(os.environ.get("GITHUB_WORKSPACE") or ".").resolve()
    output = (root / args.output).resolve()
    if root not in output.parents:
        print(
            "check-toolchain-alignment: output escapes the workspace", file=sys.stderr
        )
        return 2
    try:
        lock, tools = load_core()
        document = report(scan(root, lock, tools), lock)
    except CoreContractError as error:
        print(f"check-toolchain-alignment: {error}", file=sys.stderr)
        return 3

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", "utf-8")
    _annotate(document["findings"])
    _summary(document)
    print(
        f"check-toolchain-alignment: {document['status']} "
        f"({len(document['findings'])} declaration(s))"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
