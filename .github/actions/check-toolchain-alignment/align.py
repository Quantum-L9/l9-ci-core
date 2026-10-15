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

DRIFT = ("exact_drift", "range_excludes_lock")
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

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        yield _finding("*", "pyproject.toml", 1, "pyproject", "", "", "unparseable")
        return
    groups: list[tuple[str, Any]] = []
    project = data.get("project", {})
    for name, deps in sorted(project.get("optional-dependencies", {}).items()):
        groups.append((f"project.optional-dependencies.{name}", deps))
    for name, deps in sorted(data.get("dependency-groups", {}).items()):
        groups.append((f"dependency-groups.{name}", deps))
    uv = data.get("tool", {}).get("uv", {})
    if "dev-dependencies" in uv:
        groups.append(("tool.uv.dev-dependencies", uv["dev-dependencies"]))
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
    current: str | None = None
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
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


def scan_requirements(
    root: pathlib.Path, lock: dict[str, str], pip: dict[str, str]
) -> Iterator[Finding]:
    for candidate in sorted(root.glob("requirements*.txt")):
        relative = candidate.name
        path = _safe_file(root, relative)
        if path is None:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
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
    current: str | None = None
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
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
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, 1):
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
    text = path.read_text(encoding="utf-8")
    try:
        manifest = json.loads(text)
    except ValueError:
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
