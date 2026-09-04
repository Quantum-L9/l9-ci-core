#!/usr/bin/env python3
"""Namespace-aware release-writer uniqueness for ``Quantum-L9/l9-ci-core``.

Two tag namespaces exist in this repository and must never be conflated
(``.l9/release-plane.yaml`` → ``release_writers``):

``vMAJOR.MINOR.PATCH``
    Immutable Core release identity. Audit, provenance, rollback.
    Authorized writer: ``docs/release/tag-and-release.sh``.

``v2``
    The transitional ``install-consumer-ci@v2`` toolchain installer tag.
    Authorized writer: ``tools/publish_consumer_ci_tag.sh``.

The invariant is *namespace ownership*, not the absence of tagging commands:
"only one ``git tag`` may exist in the repository" is the wrong rule and would
either forbid the transitional lane or bless a second exact-release writer.
What this checks is that exactly one executable surface can mutate each
namespace, and that neither authorized writer can reach into the other's.

Scope is executable release/tag mutation only. Prose that mentions ``git tag``
is not a writer: shell comments, YAML comments, Python comments and
docstrings, printed instructions (``echo``/``printf``), and Markdown are all
excluded, so documentation never fails this check. Read-only inspection
(``git rev-parse``, ``git show-ref``, ``gh release view``) is likewise not a
mutation.

Fail-closed: a mutation site whose target ref cannot be resolved to a
namespace is an error, not a pass.

    python3 tools/check_release_writers.py [--root PATH] [--json]

Exit codes: ``0`` conforming, ``2`` a violation, ``3`` the contract could not
be read.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import tokenize
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONTRACT = Path(".l9") / "release-plane.yaml"

#: Directories whose executable files can mutate a release identity. Markdown
#: and other prose is excluded by construction: only these suffixes are read.
SEARCH_ROOTS = (
    ".github/workflows",
    ".github/actions",
    "tools",
    "scripts",
    "docs/release",
)
EXECUTABLE_SUFFIXES = (".sh", ".bash", ".py", ".yml", ".yaml")

#: Commands that only print. A printed instruction such as
#: ``echo "to publish: git push origin v2"`` is documentation, not a writer.
PRINTING_COMMANDS = frozenset({"echo", "printf", "say", "die", "cat", "warn", "log"})
#: Wrappers that execute their arguments (``run() { say "$*"; "$@"; }``).
EXECUTING_WRAPPERS = frozenset({"run", "sudo", "env", "time", "exec", "xargs"})

#: Mutation primitives, matched against a comment-stripped line.
GIT_TAG = re.compile(r"\bgit\s+tag\b(?P<rest>.*)")
GIT_PUSH = re.compile(r"\bgit\s+push\b(?P<rest>.*)")
GH_RELEASE = re.compile(
    r"\bgh\s+release\s+(?P<sub>[a-z-]+)\b(?P<rest>.*)",
)
#: ``gh api``/``curl`` against the git-refs API, and the REST path itself.
REFS_API = re.compile(r"git/refs(?:/tags/(?P<ref>[^\s\"'}]*))?")
#: Third-party actions that create or update a GitHub release or tag.
RELEASE_ACTIONS = re.compile(
    r"uses:\s*(?P<action>[^@\s]*(?:action-gh-release|create-release|"
    r"release-action|tag-action|github-tag-action|upload-release-asset)[^@\s]*)"
)

#: Mutating ``gh release`` subcommands. Everything else (``view``, ``list``,
#: ``download``) only reads and is not a writer.
GH_RELEASE_MUTATIONS = frozenset({"create", "edit", "delete", "upload", "update"})

#: ``git tag`` forms that only read.
GIT_TAG_READ_FLAGS = frozenset({"-l", "--list", "--contains", "--points-at", "-n"})

#: Variable names whose push target is a plausible release ref. An
#: unresolvable ``git push origin "${BRANCH}"`` is not a release writer; an
#: unresolvable ``git push origin "${RELEASE_TAG}"`` must fail closed.
TAGGISH_NAME = re.compile(r"TAG|RELEASE|VERSION", re.IGNORECASE)

#: A shell assignment: ``NAME=value`` / ``NAME="value"``.
ASSIGNMENT = re.compile(
    r"^\s*(?:export\s+|local\s+|declare\s+(?:-\w+\s+)?)?"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)="
    r"(?P<value>\"[^\"]*\"|'[^']*'|\S*)\s*$"
)
EXPANSION = re.compile(
    r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)\}|\$(?P<bare>[A-Za-z_][A-Za-z0-9_]*)"
)
#: A bash regex guard that constrains a variable to exact semver, e.g.
#: ``[[ "${VERSION}" =~ ^(0|[1-9][0-9]*)\.(0|...)\.(0|...)$ ]]``.
SEMVER_GUARD = re.compile(
    r"\[\[\s*\"?\$\{?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\}?\"?\s*=~\s*"
    r"(?P<pattern>\^.*\$)\s*\]\]"
)
SEMVER_GUARD_BODY = re.compile(
    r"^\^\(0\|\[1-9\]\[0-9\]\*\)\\\.\(0\|\[1-9\]\[0-9\]\*\)\\\.\(0\|\[1-9\]\[0-9\]\*\)\$$"
)

UNRESOLVED = "\x00"


class ReleaseWriterError(RuntimeError):
    """The contract could not be read, so nothing can be attested."""


@dataclass(frozen=True)
class Namespace:
    key: str
    pattern: re.Pattern[str]
    authorized_writer: str


@dataclass(frozen=True)
class Site:
    """One executable mutation of a tag or release identity."""

    path: str
    line_number: int
    namespace: str | None
    target: str
    evidence: str

    def describe(self) -> str:
        return f"{self.path}:{self.line_number}: {self.evidence}"


@dataclass
class Report:
    sites: list[Site] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


def load_contract(root: Path) -> tuple[dict[str, Namespace], str]:
    path = root / CONTRACT
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ReleaseWriterError(f"cannot read {CONTRACT}: {error}") from error
    except yaml.YAMLError as error:
        raise ReleaseWriterError(f"{CONTRACT} is not valid YAML: {error}") from error
    if not isinstance(document, dict):
        raise ReleaseWriterError(f"{CONTRACT} is not a mapping")
    writers = document.get("release_writers")
    if not isinstance(writers, dict):
        raise ReleaseWriterError(f"{CONTRACT} declares no release_writers block")
    namespaces = writers.get("namespaces")
    if not isinstance(namespaces, dict) or not namespaces:
        raise ReleaseWriterError(f"{CONTRACT} declares no release_writers.namespaces")
    resolved: dict[str, Namespace] = {}
    for key, entry in namespaces.items():
        if not isinstance(entry, dict):
            raise ReleaseWriterError(
                f"release_writers.namespaces.{key} is not a mapping"
            )
        pattern = entry.get("pattern")
        writer = entry.get("authorized_writer")
        if not isinstance(pattern, str) or not isinstance(writer, str):
            raise ReleaseWriterError(
                f"release_writers.namespaces.{key} needs a pattern and an "
                "authorized_writer"
            )
        try:
            compiled = re.compile(pattern)
        except re.error as error:
            raise ReleaseWriterError(
                f"release_writers.namespaces.{key}.pattern is not a regex: {error}"
            ) from error
        resolved[str(key)] = Namespace(str(key), compiled, writer)
    validator = writers.get("validator")
    return resolved, validator if isinstance(validator, str) else ""


def executable_surfaces(root: Path) -> list[Path]:
    surfaces: set[Path] = set()
    for relative in SEARCH_ROOTS:
        base = root / relative
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix in EXECUTABLE_SUFFIXES:
                surfaces.add(path)
    return sorted(surfaces)


def strip_comments(line: str) -> str:
    """Drop a trailing comment while respecting quoting.

    ``echo "record previous SHA # here"`` keeps its text; ``git tag v2  #
    creates the alias`` loses the comment. Shell, YAML, and Python all mark
    comments with ``#``, so one implementation serves every surface read here.
    """
    result: list[str] = []
    quote: str | None = None
    previous = ""
    for character in line:
        if quote:
            result.append(character)
            if character == quote and previous != "\\":
                quote = None
        elif character in "\"'":
            quote = character
            result.append(character)
        elif character == "#":
            break
        else:
            result.append(character)
        previous = character
    return "".join(result)


def python_code_lines(text: str) -> list[str]:
    """Blank every Python string literal and comment, keeping line numbers.

    A docstring that documents ``git tag`` is prose, and this module's own
    docstring is the proof: without this, the checker reports itself as an
    unauthorized release writer. Tokenizing is what separates a string
    constant from an executed call; a regex over raw source cannot.
    """
    lines = text.splitlines()
    blanked = list(lines)
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # Unparseable Python is not silently trusted: fall back to the raw
        # lines so any mutation primitive in it is still seen.
        return [strip_comments(line) for line in lines]
    for token in tokens:
        if token.type not in (tokenize.STRING, tokenize.COMMENT):
            continue
        first, last = token.start[0] - 1, token.end[0] - 1
        for index in range(first, last + 1):
            if index >= len(blanked):
                continue
            start = token.start[1] if index == first else 0
            end = token.end[1] if index == last else len(blanked[index])
            line = blanked[index]
            blanked[index] = line[:start] + " " * (end - start) + line[end:]
    return blanked


def collect_assignments(lines: list[str]) -> dict[str, str]:
    assignments: dict[str, str] = {}
    for line in lines:
        match = ASSIGNMENT.match(line)
        if match is None:
            continue
        value = match.group("value")
        if value[:1] in "\"'" and value[-1:] == value[:1]:
            value = value[1:-1]
        name = match.group("name")
        # A variable reassigned to a different value is no longer a literal:
        # `VERSION="${VERSION#v}"` must not leave the first value standing.
        if name in assignments and assignments[name] != value:
            assignments[name] = UNRESOLVED
        else:
            assignments.setdefault(name, value)
    return assignments


def guarded_exact_variables(text: str) -> set[str]:
    """Variables a bash guard proves to hold an exact ``MAJOR.MINOR.PATCH``.

    ``docs/release/tag-and-release.sh`` builds its tag as ``v${VERSION}``
    after refusing any ``VERSION`` that is not exact semver. That guard is the
    reason the script cannot write ``v2``, so it is real evidence of namespace
    ownership rather than an unresolved expansion to wave through.
    """
    guarded: set[str] = set()
    for match in SEMVER_GUARD.finditer(text):
        if SEMVER_GUARD_BODY.match(match.group("pattern").replace(" ", "")):
            guarded.add(match.group("name"))
    return guarded


def expand(value: str, assignments: dict[str, str], guarded: set[str]) -> str:
    """Resolve shell expansions to a literal, or to a semver-guarded template.

    A guarded variable resolves to a marker the namespace matcher understands;
    anything still unresolved resolves to :data:`UNRESOLVED` so the caller
    fails closed instead of guessing.
    """
    current = value
    for _ in range(8):
        if "$" not in current:
            return current

        def substitute(match: re.Match[str]) -> str:
            name = match.group("name") or match.group("bare")
            if name in guarded:
                return "0.0.0"
            replacement = assignments.get(name, UNRESOLVED)
            return UNRESOLVED if replacement == UNRESOLVED else replacement

        expanded = EXPANSION.sub(substitute, current)
        if expanded == current:
            break
        current = expanded
    return current if "$" not in current else UNRESOLVED


def unquote(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    return token


def command_words(code: str) -> list[str]:
    """Split a comment-stripped line into shell-ish words."""
    return [word for word in re.split(r"\s+", code.strip()) if word]


def is_printed(code: str) -> bool:
    """True when the line only prints its arguments."""
    words = command_words(code)
    index = 0
    while index < len(words) and words[index] in EXECUTING_WRAPPERS:
        index += 1
    if index >= len(words):
        return False
    head = words[index].lstrip("$(").strip("`")
    return head in PRINTING_COMMANDS


def classify(target: str, namespaces: dict[str, Namespace]) -> str | None:
    """Return the namespace key a target ref belongs to, or ``None``."""
    if target == UNRESOLVED or UNRESOLVED in target:
        return None
    candidate = target.removeprefix("refs/tags/")
    for namespace in namespaces.values():
        if namespace.pattern.fullmatch(candidate):
            return namespace.key
    return "other"


def _tag_targets(rest: str) -> list[str]:
    """Ref operands of a ``git tag`` invocation, ignoring flags and messages."""
    words = command_words(rest)
    targets: list[str] = []
    skip_next = False
    for word in words:
        if skip_next:
            skip_next = False
            continue
        if word in {"-m", "--message", "-u", "--local-user", "-F", "--file"}:
            skip_next = True
            continue
        if word.startswith("-"):
            continue
        targets.append(word)
        break  # the first non-flag operand is the tag name
    return targets


def _push_targets(rest: str) -> list[str]:
    """Ref operands of a ``git push`` invocation (remote dropped)."""
    words = [word for word in command_words(rest) if not word.startswith("-")]
    return words[1:] if len(words) > 1 else []


def scan_file(path: Path, root: Path, namespaces: dict[str, Namespace]) -> list[Site]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    relative = path.relative_to(root).as_posix()
    raw_lines = text.splitlines()
    if path.suffix == ".py":
        code_lines = python_code_lines(text)
    else:
        code_lines = [strip_comments(line) for line in raw_lines]
    assignments = collect_assignments(code_lines)
    guarded = guarded_exact_variables(text)
    sites: list[Site] = []

    def record(line_number: int, raw: str, target: str, evidence: str) -> None:
        resolved = expand(unquote(target), assignments, guarded)
        namespace = classify(resolved, namespaces)
        shown = raw.strip()
        sites.append(
            Site(
                path=relative,
                line_number=line_number,
                namespace=namespace,
                target=target if resolved == UNRESOLVED else resolved,
                evidence=f"{evidence}: {shown}",
            )
        )

    for number, (raw, code) in enumerate(zip(raw_lines, code_lines), start=1):
        if not code.strip() or is_printed(code):
            continue

        tag = GIT_TAG.search(code)
        if tag is not None:
            rest = tag.group("rest")
            flags = set(command_words(rest))
            if not flags & GIT_TAG_READ_FLAGS:
                for target in _tag_targets(rest) or [UNRESOLVED]:
                    record(number, raw, target, "git tag")

        push = GIT_PUSH.search(code)
        if push is not None:
            rest = push.group("rest")
            pushes_all_tags = "--tags" in command_words(rest)
            for target in _push_targets(rest) or (
                [UNRESOLVED] if pushes_all_tags else []
            ):
                bare = unquote(target)
                resolved = expand(bare, assignments, guarded)
                namespace = classify(resolved, namespaces)
                # `git push origin "${BRANCH}"` is not a release writer, but an
                # unresolvable tag-shaped ref must not slip through as one.
                relevant = (
                    bare.startswith("refs/tags/")
                    or pushes_all_tags
                    or namespace not in {"other", None}
                    or (namespace is None and TAGGISH_NAME.search(bare) is not None)
                )
                if relevant:
                    record(number, raw, target, "git push")

        release = GH_RELEASE.search(code)
        if release is not None and release.group("sub") in GH_RELEASE_MUTATIONS:
            operands = [
                word
                for word in command_words(release.group("rest"))
                if not word.startswith("-")
            ]
            for target in operands[:1] or [UNRESOLVED]:
                record(number, raw, target, f"gh release {release.group('sub')}")

        refs = REFS_API.search(code)
        if refs is not None and ("POST" in code or "PATCH" in code or "-X" in code):
            record(number, raw, refs.group("ref") or UNRESOLVED, "git refs API")

        action = RELEASE_ACTIONS.search(code)
        if action is not None:
            record(number, raw, UNRESOLVED, f"release action {action.group('action')}")

    return sites


def evaluate(sites: list[Site], namespaces: dict[str, Namespace]) -> list[str]:
    violations: list[str] = []
    authorized = {
        namespace.authorized_writer: key for key, namespace in namespaces.items()
    }

    for site in sites:
        if site.namespace is None:
            violations.append(
                f"{site.describe()}\n"
                "  the mutated ref could not be resolved to a tag namespace; "
                "an undeterminable release writer is not a pass"
            )

    for key, namespace in namespaces.items():
        writers = sorted({site.path for site in sites if site.namespace == key})
        if writers == [namespace.authorized_writer]:
            continue
        if namespace.authorized_writer not in writers:
            violations.append(
                f"namespace {key} ({namespace.pattern.pattern}): the authorized "
                f"writer {namespace.authorized_writer} does not mutate it"
            )
        for unauthorized in writers:
            if unauthorized == namespace.authorized_writer:
                continue
            crossed = authorized.get(unauthorized)
            reason = (
                f"it is the authorized writer for {crossed}, and the two "
                "namespaces must not cross-write"
                if crossed
                else "it is not the authorized writer"
            )
            offending = [
                site.describe()
                for site in sites
                if site.path == unauthorized and site.namespace == key
            ]
            violations.append(
                f"namespace {key} ({namespace.pattern.pattern}): "
                f"{unauthorized} can mutate it but {reason}\n  "
                + "\n  ".join(offending)
            )
    return violations


def check(root: Path) -> Report:
    namespaces, _ = load_contract(root)
    sites: list[Site] = []
    for path in executable_surfaces(root):
        sites.extend(scan_file(path, root, namespaces))
    return Report(sites=sites, violations=evaluate(sites, namespaces))


def render(report: Report, namespaces: dict[str, Namespace]) -> str:
    lines: list[str] = []
    for key, namespace in namespaces.items():
        writers = sorted({site.path for site in report.sites if site.namespace == key})
        status = "PASS" if writers == [namespace.authorized_writer] else "FAIL"
        lines.append(f"{status} {key} -> {', '.join(writers) or '(no writer)'}")
    for violation in report.violations:
        lines.append(f"  {violation}")
    return "\n".join(lines)


def as_json(report: Report, namespaces: dict[str, Namespace]) -> dict[str, Any]:
    return {
        "schema": "l9.release-writer-uniqueness/v1",
        "conforming": report.ok,
        "namespaces": {
            key: {
                "pattern": namespace.pattern.pattern,
                "authorized_writer": namespace.authorized_writer,
                "observed_writers": sorted(
                    {site.path for site in report.sites if site.namespace == key}
                ),
            }
            for key, namespace in namespaces.items()
        },
        "sites": [
            {
                "path": site.path,
                "line": site.line_number,
                "namespace": site.namespace,
                "target": site.target,
            }
            for site in report.sites
        ],
        "violations": report.violations,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    arguments = parser.parse_args(argv)
    root = Path(arguments.root).resolve()
    try:
        namespaces, _ = load_contract(root)
        report = check(root)
    except ReleaseWriterError as error:
        print(f"check-release-writers: {error}", file=sys.stderr)
        return 3
    if arguments.json:
        print(json.dumps(as_json(report, namespaces), indent=2))
    else:
        print(render(report, namespaces))
    return 0 if report.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
