#!/usr/bin/env python3
"""Static pull-request expense judge for the trusted required workflow.

This module is Core runtime code. It does not admit organization policy and
it does not decide that a suite passed. ``receipt`` is always ``static``:
a fixture or a green judge is not a hosted test receipt.

The judge reads the proposed revision. A diff that only touches workflow
files is not an exemption. It follows Make, pre-commit, and literal nested
scripts; a comment that mentions pytest is not an edge. The make-v1 ``test``
leaf is always in scope when a Makefile is present, because the required
workflow runs that leaf even when no consumer workflow names it.

A ``bash -c "$VAR"`` install command is not a nested script. An unresolved
literal script path, or a pytest/unittest/make selection whose arguments are
not literals, blocks as ``UNKNOWN_SCRIPT``.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

JUDGED_EVENTS = frozenset(
    {"pull_request", "pull_request_target", "merge_group", "merge", "push"}
)
PR_EVENTS = frozenset({"pull_request", "pull_request_target", "merge_group", "merge"})
_FULL_ROOTS = frozenset({"", ".", "tests", "test"})
_STATUS_ORDER = (
    "FULL_PYTEST",
    "REPO_TEST_FULL",
    "PRE_COMMIT_PYTEST",
    "SONAR_PYTEST",
    "UNKNOWN_SCRIPT",
    "SKIPPED_REQUIRED",
    "ZERO_TESTS",
)
_PYTEST_VALUE = frozenset(
    {
        "-k",
        "-m",
        "-n",
        "--numprocesses",
        "-c",
        "--confcutdir",
        "-p",
        "--override-ini",
        "-o",
        "--tb",
        "--maxfail",
        "--cov",
        "--cov-report",
        "--cov-config",
        "--rootdir",
        "--junitxml",
        "--html",
        "--json-report",
        "-W",
        "--ignore",
        "--ignore-glob",
        "--deselect",
        "--timeout",
        "-r",
        "--durations",
    }
)
_PYTEST_FLAGS = frozenset(
    {
        "-q",
        "-qq",
        "-v",
        "-vv",
        "-vvv",
        "-x",
        "-s",
        "--ff",
        "--lf",
        "--sw",
        "--disable-warnings",
        "--collect-only",
        "-ra",
        "--strict-markers",
        "--strict-config",
    }
)
_UNITTEST_FLAGS = frozenset(
    {"-v", "--verbose", "-q", "--quiet", "-b", "--buffer", "-f", "--failfast"}
)


class GuardParseError(ValueError):
    """A workflow, hook, or makefile could not be read far enough to judge."""


@dataclass(frozen=True)
class GuardDecision:
    status: str
    exit_code: int
    heavy: bool
    suite_pass: bool
    selected_suite: str
    receipt: str
    findings: tuple[str, ...]
    changed_files: tuple[str, ...]


@dataclass(frozen=True)
class _Hit:
    code: str
    detail: str


@dataclass
class _Target:
    deps: list[str]
    recipe: list[str]


def protocol(decision: GuardDecision) -> str:
    heavy = "true" if decision.heavy else "false"
    suite_pass = "true" if decision.suite_pass else "false"
    return (
        f"{decision.status}\n"
        f"heavy={heavy}\n"
        f"suite_pass={suite_pass}\n"
        f"selected_suite={decision.selected_suite}\n"
        f"receipt={decision.receipt}\n"
    )


def _decide(
    status: str,
    findings: Sequence[_Hit] = (),
    *,
    selected: str = "none",
    changed: Sequence[str] = (),
) -> GuardDecision:
    passed = status in {"BOUNDED_FAST", "NOT_APPLICABLE"}
    return GuardDecision(
        status=status,
        exit_code=0 if passed else 1,
        heavy=False,
        suite_pass=False,
        selected_suite=selected if status == "BOUNDED_FAST" else "none",
        receipt="static",
        findings=tuple(f"{hit.code} {hit.detail}" for hit in findings),
        changed_files=tuple(changed),
    )


def judge(
    root: Path,
    *,
    event: str,
    scope_resolved: bool,
    changed_files: Sequence[str] = (),
) -> GuardDecision:
    """Judge one revision.

    ``changed_files`` is evidence of what the diff named. It never exempts a
    workflow-only change and it is never replaced with ``.`` or ``tests/``.
    """

    changed = tuple(changed_files)
    if event not in JUDGED_EVENTS:
        return _decide("NOT_APPLICABLE", changed=changed)
    if not scope_resolved:
        return _decide(
            "PR_SCOPE_UNKNOWN",
            (_Hit("PR_SCOPE_UNKNOWN", "changed files are unresolved"),),
            changed=changed,
        )
    try:
        hits = _collect(root, event)
    except GuardParseError as error:
        return _decide(
            "UNKNOWN_SCRIPT",
            (_Hit("UNKNOWN_SCRIPT", str(error)),),
            changed=changed,
        )
    codes = {hit.code for hit in hits}
    if "BOUNDED" in codes:
        codes.discard("SKIPPED_REQUIRED")
        codes.discard("ZERO_TESTS")
    for status in _STATUS_ORDER:
        if status in codes:
            return _decide(status, hits, changed=changed)
    bounded = [hit for hit in hits if hit.code == "BOUNDED"]
    if bounded:
        return _decide(
            "BOUNDED_FAST",
            hits,
            selected=f"bounded:{len(bounded)}",
            changed=changed,
        )
    return _decide(
        "ZERO_TESTS",
        (_Hit("ZERO_TESTS", "no bounded nonempty fast selection"),),
        changed=changed,
    )


def _collect(root: Path, event: str) -> list[_Hit]:
    hits: list[_Hit] = []
    _walk_make_test(root, hits)
    _walk_pre_commit(root, hits)
    workflows = _workflow_paths(root)
    # promotion-canary.yml is the render of org-ci.yml and central-semgrep.yml,
    # which are walked themselves. The render also runs a provisioner it
    # downloaded under runner.temp; that path is not a test selection.
    relevant = [
        path
        for path in workflows
        if path.name != "promotion-canary.yml" and _workflow_relevant(root, path, event)
    ]
    seen: set[Path] = set()
    for path in relevant:
        _walk_workflow(root, path, event, hits, seen, inherited=True)
    return hits


def _workflow_paths(root: Path) -> list[Path]:
    directory = root / ".github" / "workflows"
    if not directory.is_dir():
        return []
    found = [
        path
        for pattern in ("*.yml", "*.yaml")
        for path in directory.glob(pattern)
        if path.is_file()
    ]
    return sorted(found)


def _workflow_relevant(root: Path, path: Path, event: str) -> bool:
    document = _load_workflow(path)
    return _document_relevant(document, event)


def _document_relevant(document: Mapping[str, object], event: str) -> bool:
    events = _active_events(document)
    if event in PR_EVENTS:
        return "pull_request" in events
    if event == "push":
        return "push" in events
    return False


def _active_events(document: Mapping[str, object]) -> set[str]:
    triggers = document.get("on")
    names: set[str] = set()
    if isinstance(triggers, str):
        names.add(triggers)
        return _filter_push(names, None)
    if isinstance(triggers, list):
        for item in triggers:
            if isinstance(item, str):
                names.add(item)
        return _filter_push(names, None)
    if isinstance(triggers, dict):
        names.update(str(key) for key in triggers)
        return _filter_push(names, triggers.get("push"))
    return set()


def _filter_push(names: set[str], push: object) -> set[str]:
    if "pull_request" in names or "pull_request_target" in names:
        names.add("pull_request")
    if "push" not in names:
        return names
    if isinstance(push, dict) and "tags" in push and "branches" not in push:
        names.discard("push")
    return names


def _load_workflow(path: Path) -> Mapping[str, object]:
    document = loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise GuardParseError(f"{path} is not a workflow mapping")
    return document


def _walk_workflow(
    root: Path,
    path: Path,
    event: str,
    hits: list[_Hit],
    seen: set[Path],
    *,
    inherited: bool,
) -> None:
    resolved = path.resolve()
    if resolved in seen:
        return
    seen.add(resolved)
    document = _load_workflow(path)
    if not inherited and not _document_relevant(document, event):
        return
    jobs = document.get("jobs")
    if not isinstance(jobs, dict):
        return
    workflow_name = str(document.get("name", path.name))
    judge_events = _judge_events(event)
    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            raise GuardParseError(f"{path} job {job_id} is not a mapping")
        _walk_job(
            root,
            path,
            str(job_id),
            job,
            judge_events,
            workflow_name,
            hits,
            seen,
            event,
        )


def _judge_events(event: str) -> set[str]:
    if event in PR_EVENTS:
        return {"pull_request"}
    if event == "push":
        return {"push"}
    return set()


def _walk_job(
    root: Path,
    workflow: Path,
    job_id: str,
    job: Mapping[str, object],
    events: set[str],
    workflow_name: str,
    hits: list[_Hit],
    seen: set[Path],
    event: str,
) -> None:
    job_name = str(job.get("name", job_id))
    sonar = _sonarish(workflow_name) or _sonarish(job_id) or _sonarish(job_name)
    raw_if = _text(job.get("if"))
    uses = job.get("uses")
    if isinstance(uses, str) and uses.strip():
        _walk_job_uses(root, uses.strip(), event, hits, seen)
        return
    if _definitely_false(raw_if):
        if _job_names_tests(job_name, job):
            hits.append(_Hit("SKIPPED_REQUIRED", f"{workflow.name}:{job_id}"))
        return
    if not _holds(raw_if, events):
        return
    steps = job.get("steps")
    if steps is None:
        return
    if not isinstance(steps, list):
        raise GuardParseError(f"{workflow} steps are not a list")
    skipped_test = False
    for step in steps:
        if not isinstance(step, dict):
            raise GuardParseError(f"{workflow} has a step that is not a mapping")
        step_if = _text(step.get("if"))
        step_name = str(step.get("name", ""))
        step_sonar = (
            sonar or _sonarish(step_name) or _sonarish(str(step.get("uses", "")))
        )
        if _definitely_false(step_if) and _step_names_tests(step):
            skipped_test = True
            continue
        if not _holds(step_if, events):
            continue
        run = step.get("run")
        if isinstance(run, str):
            directory = _workdir(step, job)
            _scan_shell(
                root,
                run,
                hits,
                via="sonar" if step_sonar else "workflow",
                directory=directory,
                depth=0,
                seen=set(),
            )
    if skipped_test and not any(hit.code == "BOUNDED" for hit in hits):
        reachable_bad = any(hit.code in _STATUS_ORDER[:5] for hit in hits)
        if not reachable_bad:
            hits.append(_Hit("SKIPPED_REQUIRED", f"{workflow.name}:{job_id}"))


def _walk_job_uses(
    root: Path,
    uses: str,
    event: str,
    hits: list[_Hit],
    seen: set[Path],
) -> None:
    local = _local_workflow(uses)
    if local is None:
        hits.append(_Hit("UNKNOWN_SCRIPT", f"unreadable reusable workflow {uses}"))
        return
    path = root / local
    if not path.is_file():
        # owner/repo/.github/workflows/name.yml@sha is another repository.
        # When that file is not in this checkout it is not this tree's tests.
        if not uses.startswith("./"):
            return
        hits.append(_Hit("UNKNOWN_SCRIPT", f"missing reusable workflow {local}"))
        return
    _walk_workflow(root, path, event, hits, seen, inherited=True)


def _local_workflow(uses: str) -> str | None:
    path = uses.split("@", 1)[0]
    if path.startswith("./"):
        path = path[2:]
    marker = ".github/workflows/"
    # org-ci.yml calls this repository's own workflow by
    # Quantum-L9/l9-ci-core/.github/workflows/<name>@<sha>. The file in this
    # checkout is that workflow.
    index = path.find(marker)
    if index > 0:
        path = path[index:]
    if path.startswith(marker) and path.endswith((".yml", ".yaml")):
        return path
    return None


def _job_names_tests(job_name: str, job: Mapping[str, object]) -> bool:
    if re.search(r"test", job_name, re.IGNORECASE):
        return True
    steps = job.get("steps")
    if not isinstance(steps, list):
        return False
    return any(isinstance(step, dict) and _step_names_tests(step) for step in steps)


def _step_names_tests(step: Mapping[str, object]) -> bool:
    blob = f"{step.get('name', '')}\n{step.get('run', '')}"
    return bool(
        re.search(r"\b(pytest|unittest|repo-test)\b", blob)
        or re.search(r"\bmake\s+(test|repo-test)\b", blob)
    )


def _sonarish(value: str) -> bool:
    return "sonar" in value.lower()


def _text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _definitely_false(expression: str | None) -> bool:
    if expression is None:
        return False
    compact = " ".join(expression.split()).lower()
    return compact in {"false", "${{ false }}", "${{false}}"}


def _holds(expression: str | None, events: set[str]) -> bool:
    if expression is None or expression.strip() == "":
        return True
    if _definitely_false(expression):
        return False
    compact = " ".join(expression.strip().split())
    if compact.lower() in {"true", "${{ true }}", "${{true}}", "always()"}:
        return True
    inner = compact
    if inner.startswith("${{") and inner.endswith("}}"):
        inner = inner[3:-2].strip()
    if not events:
        return True
    return any(_event_matches(inner, event) for event in events)


def _event_matches(expression: str, event: str) -> bool:
    parts = _split_top(expression, "||")
    return any(_and_matches(part, event) for part in parts)


def _and_matches(expression: str, event: str) -> bool:
    for part in _split_top(expression, "&&"):
        kind = _clause(part.strip(), event)
        if kind is False:
            return False
        if kind is None:
            return True
    return True


def _clause(text: str, event: str) -> bool | None:
    match = re.fullmatch(
        r"github\.event_name\s*([=!]=)\s*['\"]([^'\"]+)['\"]",
        text,
    )
    if match is None:
        lowered = text.lower()
        if lowered == "false":
            return False
        if lowered == "true":
            return True
        return None
    operator, expected = match.group(1), match.group(2)
    equals = expected == event
    return equals if operator == "==" else not equals


def _split_top(expression: str, separator: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    quote = ""
    index = 0
    while index < len(expression):
        char = expression[index]
        if quote:
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        elif depth == 0 and expression.startswith(separator, index):
            parts.append(expression[start:index])
            index += len(separator)
            start = index
            continue
        index += 1
    parts.append(expression[start:])
    return parts or [expression]


def _workdir(step: Mapping[str, object], job: Mapping[str, object]) -> str:
    for source in (
        step.get("working-directory"),
        _nested_workdir(job.get("defaults")),
    ):
        if isinstance(source, str) and source.strip() and "${{" not in source:
            return source.strip().strip("/")
    return ""


def _nested_workdir(defaults: object) -> object:
    if not isinstance(defaults, dict):
        return None
    run = defaults.get("run")
    if not isinstance(run, dict):
        return None
    return run.get("working-directory")


def _scan_shell(
    root: Path,
    script: str,
    hits: list[_Hit],
    *,
    via: str,
    directory: str,
    depth: int,
    seen: set[str],
    test_recipe: bool = False,
) -> None:
    if depth > 8:
        hits.append(_Hit("UNKNOWN_SCRIPT", "nested script depth exceeded"))
        return
    stripped = _strip_heredocs(script)
    stripped = _strip_shell_comments(stripped)
    arrays = _parse_arrays(stripped)
    stripped = _strip_arrays(stripped)
    logical = _join_continuations(stripped.splitlines())
    for line in logical:
        for statement in _split_statements(line):
            argv = _expand_tokens(_tokenize(statement), arrays)
            if not argv:
                continue
            _classify_argv(
                root,
                argv,
                hits,
                via=via,
                directory=directory,
                depth=depth,
                seen=seen,
                test_recipe=test_recipe,
            )


def _classify_argv(
    root: Path,
    argv: Sequence[str],
    hits: list[_Hit],
    *,
    via: str,
    directory: str,
    depth: int,
    seen: set[str],
    test_recipe: bool = False,
) -> None:
    argv = _strip_command_prefix(argv)
    if not argv:
        return
    command = argv[0]
    if _MAKE_FUNCTION_PLACEHOLDER in command:
        # The command word came from a GNU make function the judge cannot
        # evaluate. It is not "no test": it is an undecided command.
        if test_recipe:
            hits.append(
                _Hit("UNKNOWN_SCRIPT", f"make function in command word {command}")
            )
        return
    base = Path(command).name
    if base in {"pytest", "py.test"}:
        hits.append(_retag(_classify_pytest(root, argv[1:], directory), via))
        return
    if base in {"python", "python3"} or base.startswith("python3."):
        _classify_python(
            root,
            list(argv),
            hits,
            via=via,
            directory=directory,
            depth=depth,
            seen=seen,
            test_recipe=test_recipe,
        )
        return
    if base == "coverage" and "pytest" in argv:
        index = list(argv).index("pytest")
        hits.append(_retag(_classify_pytest(root, argv[index + 1 :], directory), via))
        return
    if base == "make":
        _classify_make(root, list(argv[1:]), hits, directory)
        return
    if base in {"bash", "sh", "dash", "source", "."}:
        _follow_shell(
            root,
            list(argv),
            hits,
            via=via,
            directory=directory,
            depth=depth,
            seen=seen,
            test_recipe=test_recipe,
        )
        return
    if command.startswith("./") or command.endswith((".sh", ".bash", ".py")):
        _follow_path(
            root,
            command,
            hits,
            via=via,
            directory=directory,
            depth=depth,
            seen=seen,
            test_recipe=test_recipe,
        )
        return
    if "$" in command and (
        "/" in command
        or command.endswith((".sh", ".bash", ".py"))
        or command.startswith("./")
    ):
        # A tool the job installed under the runner temp directory is not
        # this repository's test suite. CodeQL and the canary provisioner
        # both land there.
        if "RUNNER_TEMP" in command or "runner.temp" in command:
            return
        hits.append(_Hit("UNKNOWN_SCRIPT", f"unresolved script {command}"))


_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _strip_command_prefix(argv: Sequence[str]) -> list[str]:
    """Drop leading ``NAME=value`` words and an ``env`` wrapper.

    ``PYTHONPATH=. pytest tests/`` runs pytest. Dropping the whole statement
    because its first word is an assignment made the judge blind to it.
    """

    words = list(argv)
    if words and (
        "+=" in words[0].split("=", 1)[0] + "="
        or (_ASSIGNMENT.match(words[0]) and ("$(" in words[0] or "`" in words[0]))
    ):
        # An array append or a command-substitution assignment. The
        # tokenizer cannot split nested quotes in a substitution, so the
        # statement is not read as a command; this is what the judge did
        # before prefix assignments were stripped.
        return []
    while words:
        if _ASSIGNMENT.match(words[0]):
            words = words[1:]
            continue
        if words[0] == "env":
            words = words[1:]
            while words and words[0].startswith("-"):
                takes_value = words[0] in {"-u", "--unset", "-C", "--chdir"}
                words = words[2:] if takes_value else words[1:]
            continue
        break
    return words


def _retag(hit: _Hit, via: str) -> _Hit:
    if via == "sonar" and hit.code in {"FULL_PYTEST", "BOUNDED", "ZERO_TESTS"}:
        return _Hit("SONAR_PYTEST", hit.detail)
    if via == "make" and hit.code == "FULL_PYTEST":
        return _Hit("REPO_TEST_FULL", hit.detail)
    if via == "pre-commit" and hit.code == "FULL_PYTEST":
        return _Hit("PRE_COMMIT_PYTEST", hit.detail)
    if via == "pre-commit" and hit.code == "UNKNOWN_SCRIPT":
        return _Hit("PRE_COMMIT_PYTEST", hit.detail)
    return hit


def _classify_python(
    root: Path,
    argv: list[str],
    hits: list[_Hit],
    *,
    via: str,
    directory: str,
    depth: int,
    seen: set[str],
    test_recipe: bool = False,
) -> None:
    if "-c" in argv:
        return
    if "-m" in argv:
        module = argv[argv.index("-m") + 1] if argv.index("-m") + 1 < len(argv) else ""
        rest = argv[argv.index("-m") + 2 :]
        if module == "pytest":
            hits.append(_retag(_classify_pytest(root, rest, directory), via))
        elif module == "unittest":
            hits.append(_retag(_classify_unittest(root, rest, directory), via))
        return
    for arg in argv[1:]:
        if arg.startswith("-"):
            continue
        if arg.endswith(".py"):
            _follow_path(
                root,
                arg,
                hits,
                via=via,
                directory=directory,
                depth=depth,
                seen=seen,
                test_recipe=test_recipe,
            )
        return


def _classify_pytest(root: Path, args: Sequence[str], directory: str) -> _Hit:
    positionals: list[str] = []
    index = 0
    values = list(args)
    while index < len(values):
        token = values[index]
        if token in _PYTEST_FLAGS:
            index += 1
            continue
        if token in _PYTEST_VALUE:
            if index + 1 >= len(values):
                return _Hit("UNKNOWN_SCRIPT", f"pytest option {token} has no value")
            index += 2
            continue
        if token.startswith("--") and "=" in token:
            index += 1
            continue
        if token.startswith("-"):
            return _Hit("UNKNOWN_SCRIPT", f"unclassified pytest option {token}")
        positionals.append(token)
        index += 1
    return _selection(root, positionals, directory, "pytest")


def _classify_unittest(root: Path, args: Sequence[str], directory: str) -> _Hit:
    values = list(args)
    if values and values[0] == "discover":
        return _classify_discover(root, values[1:], directory)
    positionals: list[str] = []
    index = 0
    while index < len(values):
        token = values[index]
        if token in _UNITTEST_FLAGS:
            index += 1
            continue
        if token in {"-k", "-p", "-s", "-t"}:
            if index + 1 >= len(values):
                return _Hit("UNKNOWN_SCRIPT", f"unittest option {token} has no value")
            index += 2
            continue
        if token.startswith("-"):
            return _Hit("UNKNOWN_SCRIPT", f"unclassified unittest option {token}")
        positionals.append(token)
        index += 1
    if not positionals:
        return _Hit("FULL_PYTEST", "unittest with no modules")
    paths = [_dotted_module(token) for token in positionals]
    return _selection(root, paths, directory, "unittest")


def _classify_discover(root: Path, args: Sequence[str], directory: str) -> _Hit:
    start = "."
    pattern = "test*.py"
    index = 0
    values = list(args)
    while index < len(values):
        token = values[index]
        if token in {"-s", "--start-directory"}:
            if index + 1 >= len(values):
                return _Hit("UNKNOWN_SCRIPT", "unittest discover missing directory")
            start = values[index + 1]
            index += 2
            continue
        if token in {"-p", "--pattern"}:
            if index + 1 >= len(values):
                return _Hit("UNKNOWN_SCRIPT", "unittest discover missing pattern")
            pattern = values[index + 1].strip("'\"")
            index += 2
            continue
        if token in {"-t", "--top-level-directory"}:
            index += 2
            continue
        if token in _UNITTEST_FLAGS:
            index += 1
            continue
        return _Hit("UNKNOWN_SCRIPT", f"unclassified discover option {token}")
    if _is_full_root(start):
        return _Hit("FULL_PYTEST", f"unittest discover {start}")
    path = _safe_path(root, start, directory)
    if path is None or not path.is_dir():
        return _Hit("ZERO_TESTS", f"missing discover directory {start}")
    matched = [
        item
        for item in path.glob(pattern)
        if item.is_file() and item.stat().st_size > 0
    ]
    if not matched:
        return _Hit("ZERO_TESTS", f"discover {start} selected nothing")
    return _Hit("BOUNDED", start)


def _selection(
    root: Path,
    positionals: Sequence[str],
    directory: str,
    kind: str,
) -> _Hit:
    if not positionals:
        return _Hit("FULL_PYTEST", f"{kind} with no paths")
    if any(_is_full_root(token) for token in positionals):
        return _Hit("FULL_PYTEST", f"{kind} {' '.join(positionals)}")
    if any("$" in token for token in positionals):
        return _Hit("UNKNOWN_SCRIPT", f"{kind} selection is not literal")
    existing = 0
    for token in positionals:
        path = _safe_path(root, token, directory)
        if path is None:
            return _Hit("UNKNOWN_SCRIPT", f"{kind} path escapes the revision")
        if path.is_file() and path.stat().st_size > 0 and _file_is_test(path):
            existing += 1
        elif path.is_dir() and _directory_has_tests(path):
            existing += 1
    if existing == 0:
        return _Hit("ZERO_TESTS", f"{kind} selected nothing")
    return _Hit("BOUNDED", " ".join(positionals))


def _is_full_root(token: str) -> bool:
    normalized = token.replace("\\", "/").strip().rstrip("/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized in _FULL_ROOTS


def _dotted_module(token: str) -> str:
    if "/" in token or token.endswith(".py"):
        return token
    if re.fullmatch(r"[A-Za-z_][\w.]*", token):
        return token.replace(".", "/") + ".py"
    return token


def _safe_path(root: Path, token: str, directory: str) -> Path | None:
    relative = token.strip().strip("'\"")
    if relative.startswith("/") or relative.startswith("~"):
        return None
    parts = Path(relative).parts
    if ".." in parts:
        return None
    base = root / directory if directory else root
    path = (base / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return None
    return path


def _missing_script_is_a_test(token: str) -> bool:
    name = Path(token).name
    if name.startswith("test_") or name.endswith("_test.py"):
        return True
    return token.endswith((".sh", ".bash"))


def _file_is_test(path: Path) -> bool:
    name = path.name
    if name.startswith("test_") or name.endswith("_test.py"):
        return True
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return False
    return "def test_" in text or "class Test" in text


def _directory_has_tests(path: Path) -> bool:
    for pattern in ("test_*.py", "*_test.py"):
        for item in path.rglob(pattern):
            if item.is_file() and item.stat().st_size > 0:
                return True
    return False


def _follow_shell(
    root: Path,
    argv: list[str],
    hits: list[_Hit],
    *,
    via: str,
    directory: str,
    depth: int,
    seen: set[str],
    test_recipe: bool = False,
) -> None:
    command = argv[0]
    args = argv[1:]
    if command in {"bash", "sh", "dash"}:
        while args and args[0].startswith("-") and args[0] != "-c":
            args = args[1:]
        if not args or args[0] == "-c":
            return
        _follow_path(
            root,
            args[0],
            hits,
            via=via,
            directory=directory,
            depth=depth,
            seen=seen,
            test_recipe=test_recipe,
        )
        return
    if len(args) < 1:
        hits.append(_Hit("UNKNOWN_SCRIPT", f"{command} has no script"))
        return
    _follow_path(
        root,
        args[0],
        hits,
        via=via,
        directory=directory,
        depth=depth,
        seen=seen,
        test_recipe=test_recipe,
    )


def _follow_path(
    root: Path,
    token: str,
    hits: list[_Hit],
    *,
    via: str,
    directory: str,
    depth: int,
    seen: set[str],
    test_recipe: bool = False,
) -> None:
    if "$" in token:
        hits.append(_Hit("UNKNOWN_SCRIPT", f"unresolved script {token}"))
        return
    path = _safe_path(root, token, directory)
    if path is None:
        hits.append(_Hit("UNKNOWN_SCRIPT", f"script escapes the revision {token}"))
        return
    key = str(path)
    if key in seen:
        return
    if not path.is_file():
        if test_recipe or _missing_script_is_a_test(token):
            hits.append(_Hit("UNKNOWN_SCRIPT", f"missing script {token}"))
        return
    seen.add(key)
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        _scan_python(root, text, hits, via=via, directory=directory)
        return
    _scan_shell(
        root,
        text,
        hits,
        via=via,
        directory=directory,
        depth=depth + 1,
        seen=seen,
    )


def _scan_python(
    root: Path,
    source: str,
    hits: list[_Hit],
    *,
    via: str,
    directory: str,
) -> None:
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        hits.append(_Hit("UNKNOWN_SCRIPT", f"python script does not parse: {error}"))
        return
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in {"eval", "exec"}:
            hits.append(_Hit("UNKNOWN_SCRIPT", "python eval/exec is unresolved"))
            return
        for constant in _call_strings(node):
            if _looks_like_test_command(constant):
                _scan_shell(
                    root,
                    constant,
                    hits,
                    via=via,
                    directory=directory,
                    depth=1,
                    seen=set(),
                )


def _looks_like_test_command(constant: str) -> bool:
    pieces = re.split(r"(?:&&|\|\||;|\n)", constant)
    for piece in pieces:
        text = piece.strip()
        if re.match(r"^(?:pytest|py\.test)(?:\s|$)", text):
            return True
        if re.match(r"^python[0-9.]*\s+-m\s+(?:pytest|unittest)(?:\s|$)", text):
            return True
        if re.match(r"^make\s+(?:test|repo-test|test-deep)(?:\s|$)", text):
            return True
    return False


def _call_strings(node: ast.Call) -> list[str]:
    found: list[str] = []
    for item in list(node.args) + [keyword.value for keyword in node.keywords]:
        if isinstance(item, ast.Constant) and isinstance(item.value, str):
            found.append(item.value)
    return found


def _classify_make(
    root: Path,
    args: list[str],
    hits: list[_Hit],
    directory: str,
) -> None:
    targets: list[str] = []
    index = 0
    while index < len(args):
        token = args[index]
        if token in {"-C", "-f", "--file", "--makefile"}:
            hits.append(_Hit("UNKNOWN_SCRIPT", f"make {token} is unresolved"))
            return
        if token.startswith("-"):
            index += 1
            continue
        targets.append(token)
        index += 1
    if not targets:
        targets = ["test"]
    for target in targets:
        if target in {"test", "repo-test", "test-deep"}:
            _walk_make_target(root, target, hits, directory, set())
        elif target.startswith("repo-"):
            _walk_make_target(root, target, hits, directory, set())


def _walk_make_test(root: Path, hits: list[_Hit]) -> None:
    makefile = root / "Makefile"
    if not makefile.is_file():
        return
    try:
        targets, _variables = _parse_make_tree(makefile)
    except GuardParseError as error:
        hits.append(_Hit("UNKNOWN_SCRIPT", str(error)))
        return
    # A Makefile that does not define test has no make test suite. An
    # explicit `make test` in a workflow still fails closed below.
    if "test" not in targets:
        return
    before = len(hits)
    _walk_make_target(root, "test", hits, "", set())
    if len(hits) == before:
        recipe = _first_recipe_line(targets, "test", set())
        if recipe is not None:
            # make test runs something, and nothing in it classified. That is
            # an undecided suite, not zero tests.
            hits.append(
                _Hit(
                    "UNKNOWN_SCRIPT",
                    f"make test runs no command the judge can classify: {recipe}",
                )
            )


def _first_recipe_line(
    targets: Mapping[str, _Target], name: str, seen: set[str]
) -> str | None:
    if name in seen or name not in targets:
        return None
    seen.add(name)
    target = targets[name]
    for dep in target.deps:
        line = _first_recipe_line(targets, dep, seen)
        if line is not None:
            return line
    for line in target.recipe:
        if line.strip():
            return line.strip()
    return None


def _walk_make_target(
    root: Path,
    name: str,
    hits: list[_Hit],
    directory: str,
    seen: set[str],
) -> None:
    if name in seen:
        return
    seen.add(name)
    base = root / directory if directory else root
    try:
        targets, _variables = _parse_make_tree(base / "Makefile")
    except GuardParseError as error:
        hits.append(_Hit("UNKNOWN_SCRIPT", str(error)))
        return
    target = targets.get(name)
    if target is None:
        hits.append(_Hit("UNKNOWN_SCRIPT", f"make target {name} is absent"))
        return
    for dep in target.deps:
        if dep in targets and not dep.startswith("."):
            _walk_loaded_target(root, targets, dep, hits, directory, seen)
    if target.recipe:
        _scan_shell(
            root,
            "\n".join(target.recipe),
            hits,
            via="make",
            directory=directory,
            depth=0,
            seen=set(),
            test_recipe=_is_test_target(name),
        )


def _walk_loaded_target(
    root: Path,
    targets: Mapping[str, _Target],
    name: str,
    hits: list[_Hit],
    directory: str,
    seen: set[str],
) -> None:
    if name in seen:
        return
    seen.add(name)
    target = targets[name]
    for dep in target.deps:
        if dep in targets and not dep.startswith("."):
            _walk_loaded_target(root, targets, dep, hits, directory, seen)
    if target.recipe:
        _scan_shell(
            root,
            "\n".join(target.recipe),
            hits,
            via="make",
            directory=directory,
            depth=0,
            seen=set(),
            test_recipe=_is_test_target(name),
        )


def _parse_make_tree(path: Path) -> tuple[dict[str, _Target], dict[str, str]]:
    targets: dict[str, _Target] = {}
    variables: dict[str, str] = {}
    _parse_makefile(path, targets, variables, [])
    return targets, variables


def _parse_makefile(
    path: Path,
    targets: dict[str, _Target],
    variables: dict[str, str],
    stack: list[Path],
) -> None:
    resolved = path.resolve()
    if resolved in stack:
        raise GuardParseError(f"makefile include cycle at {path.name}")
    if not path.is_file():
        raise GuardParseError(f"missing makefile {path}")
    current: str | None = None
    lines = _join_continuations(path.read_text(encoding="utf-8").splitlines())
    for raw in lines:
        if raw.startswith("\t"):
            if current is None:
                raise GuardParseError(f"{path.name} has a recipe outside a target")
            targets[current].recipe.append(
                _expand_make(raw[1:].lstrip("@+-"), variables)
            )
            continue
        stripped = raw.strip()
        if stripped == "" or stripped.startswith("#"):
            continue
        if stripped.startswith("include ") or stripped.startswith("-include "):
            optional = stripped.startswith("-include ")
            name = stripped.split(None, 1)[1].strip()
            included = path.parent / name
            if not included.is_file() and optional:
                continue
            _parse_makefile(included, targets, variables, [*stack, resolved])
            current = None
            continue
        assignment = re.match(
            r"^([A-Za-z0-9_.-]+)\s*(:=|\?=|\+=|=)\s*(.*)$",
            stripped,
        )
        if assignment is not None:
            key, operator, value = (
                assignment.group(1),
                assignment.group(2),
                assignment.group(3),
            )
            value = _strip_make_comment(value).strip()
            expanded = _expand_make(value, variables)
            if operator == "?=":
                variables.setdefault(key, expanded)
            elif operator == "+=":
                variables[key] = (variables.get(key, "") + " " + expanded).strip()
            else:
                variables[key] = expanded
            current = None
            continue
        target = re.match(r"^([^:#=]+):(.*)$", stripped)
        if target is None:
            raise GuardParseError(f"{path.name} has an unreadable line: {stripped}")
        name = target.group(1).strip()
        deps = _strip_make_comment(target.group(2)).split()
        targets[name] = _Target(deps=deps, recipe=[])
        current = name


def _strip_make_comment(text: str) -> str:
    if "#" not in text:
        return text
    return text.split("#", 1)[0]


_MAKE_FUNCTIONS = frozenset({"if", "wildcard", "shell"})
# What an unevaluated make function expands to. It is never empty: deleting
# the call erased the command word of `$(PY) -m pytest tests/` and the judge
# then saw no test command at all.
_MAKE_FUNCTION_PLACEHOLDER = "$(make-function)"


def _is_test_target(name: str) -> bool:
    return name in {"test", "repo-test", "test-deep"} or name.startswith("repo-")


def _is_make_function(name: str) -> bool:
    head = name.split(None, 1)[0] if name else ""
    return " " in name or head in _MAKE_FUNCTIONS


def _expand_make(value: str, variables: Mapping[str, str], depth: int = 0) -> str:
    if depth > 12:
        raise GuardParseError("makefile variable expansion is too deep")
    output: list[str] = []
    index = 0
    while index < len(value):
        if value[index] != "$" or index + 1 >= len(value):
            output.append(value[index])
            index += 1
            continue
        opener = value[index + 1]
        if opener == "$":
            output.append("$")
            index += 1
            continue
        if opener not in "({":
            output.append(value[index])
            index += 1
            continue
        closer = ")" if opener == "(" else "}"
        end = _matching_closer(value, index + 2, opener, closer)
        if end is None:
            output.append(value[index])
            index += 1
            continue
        name = value[index + 2 : end].strip()
        if _is_make_function(name):
            output.append(_expand_make_function(name, variables, depth))
            index = end + 1
            continue
        if name not in variables:
            output.append(value[index : end + 1])
        else:
            output.append(_expand_make(variables[name], variables, depth + 1))
        index = end + 1
    return "".join(output)


def _interpreter_family(word: str) -> str | None:
    base = Path(word.strip().strip("'\"")).name
    if base in {"python", "python3"} or re.fullmatch(r"python3\.\d+", base):
        return "python"
    return None


def _expand_make_function(name: str, variables: Mapping[str, str], depth: int) -> str:
    """Evaluate the one shape that is decidable without running make.

    ``$(if COND,THEN,ELSE)`` resolves when both branches expand to the same
    text, or to the same Python interpreter (``.venv/bin/python`` vs
    ``python3``). Anything else stays an explicit unevaluated token.
    """

    head, _, rest = name.partition(" ")
    if head == "if":
        arguments = _split_top(rest, ",")
        if len(arguments) in {2, 3}:
            then = _expand_make(arguments[1], variables, depth + 1).strip()
            other = (
                _expand_make(arguments[2], variables, depth + 1).strip()
                if len(arguments) == 3
                else ""
            )
            if then == other:
                return then
            if (
                " " not in then
                and " " not in other
                and _interpreter_family(then) == "python"
                and _interpreter_family(other) == "python"
            ):
                return other
    return _MAKE_FUNCTION_PLACEHOLDER


def _matching_closer(value: str, start: int, opener: str, closer: str) -> int | None:
    depth = 1
    index = start
    while index < len(value):
        if (
            value[index] == "$"
            and index + 1 < len(value)
            and value[index + 1] == opener
        ):
            depth += 1
            index += 2
            continue
        if value[index] == closer:
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _walk_pre_commit(root: Path, hits: list[_Hit]) -> None:
    path = root / ".pre-commit-config.yaml"
    if not path.is_file():
        return
    document = loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise GuardParseError("pre-commit config is not a mapping")
    repos = document.get("repos")
    if not isinstance(repos, list):
        return
    for repo in repos:
        if not isinstance(repo, dict):
            continue
        hooks = repo.get("hooks")
        if not isinstance(hooks, list):
            continue
        for hook in hooks:
            if not isinstance(hook, dict):
                continue
            _walk_hook(root, hook, hits)


def _walk_hook(root: Path, hook: Mapping[str, object], hits: list[_Hit]) -> None:
    hook_id = str(hook.get("id", ""))
    entry = hook.get("entry")
    args = hook.get("args")
    extra: list[str] = []
    if isinstance(args, list):
        extra = [str(item) for item in args]
    elif isinstance(args, str):
        extra = _tokenize(args)
    if isinstance(entry, str) and entry.strip():
        argv = _tokenize(entry) + extra
        if argv and Path(argv[0]).name in {"pytest", "py.test"}:
            hits.append(_retag(_classify_pytest(root, argv[1:], ""), "pre-commit"))
            return
        if argv and Path(argv[0]).name in {"python", "python3"}:
            _classify_python(
                root,
                argv,
                hits,
                via="pre-commit",
                directory="",
                depth=0,
                seen=set(),
            )
            return
    if "pytest" in hook_id.lower():
        hits.append(_Hit("PRE_COMMIT_PYTEST", f"hook {hook_id} has no literal entry"))


def _strip_heredocs(script: str) -> str:
    lines = script.splitlines()
    output: list[str] = []
    index = 0
    pattern = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
    while index < len(lines):
        line = lines[index]
        match = pattern.search(line)
        if match is None or "<<" not in line:
            output.append(line)
            index += 1
            continue
        output.append(line[: match.start()].rstrip())
        word = match.group(2)
        index += 1
        while index < len(lines) and lines[index].strip() != word:
            index += 1
        index += 1
    return "\n".join(output)


def _strip_shell_comments(script: str) -> str:
    output: list[str] = []
    for line in script.splitlines():
        output.append(_strip_unquoted_hash(line))
    return "\n".join(output)


def _strip_unquoted_hash(text: str) -> str:
    quote = ""
    for index, char in enumerate(text):
        if quote:
            if char == quote and text[index - 1 : index] != "\\":
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == "#":
            return text[:index].rstrip()
    return text


def _strip_arrays(script: str) -> str:
    return re.sub(
        r"([A-Za-z_][A-Za-z0-9_]*)=\((.*?)\)",
        lambda match: match.group(1) + "=()",
        script,
        flags=re.S,
    )


def _parse_arrays(script: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for match in re.finditer(
        r"([A-Za-z_][A-Za-z0-9_]*)=\((.*?)\)",
        script,
        re.S,
    ):
        found[match.group(1)] = _tokenize(match.group(2))
    return found


def _join_continuations(lines: Sequence[str]) -> list[str]:
    joined: list[str] = []
    buffer = ""
    pending = False
    for line in lines:
        if pending:
            buffer += " " + line
        else:
            buffer = line
        if buffer.endswith("\\"):
            buffer = buffer[:-1]
            pending = True
            continue
        joined.append(buffer)
        buffer = ""
        pending = False
    if pending:
        joined.append(buffer)
    return joined


def _split_statements(line: str) -> list[str]:
    parts: list[str] = []
    start = 0
    quote = ""
    index = 0
    while index < len(line):
        char = line[index]
        if quote:
            if char == quote and line[index - 1 : index] != "\\":
                quote = ""
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        if char in {";", "&", "|"}:
            parts.append(line[start:index])
            if line.startswith("&&", index) or line.startswith("||", index):
                index += 2
            else:
                index += 1
            start = index
            continue
        index += 1
    parts.append(line[start:])
    return [part.strip() for part in parts if part.strip()]


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    quote = ""
    current = ""
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            current += char
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            current += char
            index += 1
            continue
        if char.isspace():
            if current:
                tokens.append(current)
                current = ""
            index += 1
            continue
        current += char
        index += 1
    if current:
        tokens.append(current)
    cleaned: list[str] = []
    for token in tokens:
        if re.fullmatch(r"\d*>&?\d+", token) or token in {">", ">>", "<"}:
            continue
        if len(token) >= 2 and token[0] == token[-1] and token[0] in {"'", '"'}:
            cleaned.append(token[1:-1])
        else:
            cleaned.append(token)
    return cleaned


def _expand_tokens(tokens: Sequence[str], arrays: Mapping[str, list[str]]) -> list[str]:
    expanded: list[str] = []
    pattern = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\[@\]\}$")
    for token in tokens:
        match = pattern.match(token)
        if match is None:
            expanded.append(token)
            continue
        name = match.group(1)
        if name not in arrays:
            expanded.append(token)
            continue
        expanded.extend(arrays[name])
    return expanded


class _Parser:
    def __init__(self, text: str) -> None:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        self.lines = normalized.split("\n")
        self.index = 0

    def parse(self) -> object:
        self._skip_noise()
        if self.index >= len(self.lines):
            return None
        return self._parse_block(self._indent(self.lines[self.index]))

    def _parse_block(self, indent: int) -> object:
        self._skip_noise()
        if self.index >= len(self.lines):
            return None
        if self._is_list_item(self.lines[self.index], indent):
            return self._parse_list(indent)
        return self._parse_map(indent)

    def _parse_map(self, indent: int) -> dict[str, object]:
        mapping: dict[str, object] = {}
        while self.index < len(self.lines):
            self._skip_noise()
            if self.index >= len(self.lines):
                break
            line = self.lines[self.index]
            current = self._indent(line)
            if current < indent or self._is_list_item(line, indent):
                break
            if current > indent:
                raise GuardParseError(
                    f"line {self.index + 1}: unexpected indent in a mapping"
                )
            key, inline = self._split_key(line[indent:])
            self.index += 1
            mapping[str(key)] = self._value(inline, indent)
        return mapping

    def _parse_list(self, indent: int) -> list[object]:
        items: list[object] = []
        while self.index < len(self.lines):
            self._skip_noise()
            if self.index >= len(self.lines):
                break
            line = self.lines[self.index]
            if not self._is_list_item(line, indent):
                break
            rest = line[indent + 1 :].strip()
            self.index += 1
            if rest == "":
                self._skip_noise()
                if (
                    self.index < len(self.lines)
                    and self._indent(self.lines[self.index]) > indent
                ):
                    items.append(
                        self._parse_block(self._indent(self.lines[self.index]))
                    )
                else:
                    items.append(None)
                continue
            if self._looks_like_key(rest):
                key, inline = self._split_key(rest)
                child = indent + 2
                mapping: dict[str, object] = {str(key): self._value(inline, child)}
                mapping.update(self._parse_map(child))
                items.append(mapping)
                continue
            items.append(self._scalar(rest))
        return items

    def _value(self, inline: str, key_indent: int) -> object:
        text = inline.strip()
        if re.fullmatch(r"[|>][+-]?\d*", text):
            return self._block_scalar(text, key_indent)
        if text.startswith(("[", "{")):
            return self._flow(text)
        if text == "":
            self._skip_noise()
            if (
                self.index < len(self.lines)
                and self._content_indent(self.lines[self.index]) > key_indent
            ):
                return self._parse_block(self._indent(self.lines[self.index]))
            return None
        return self._scalar(text)

    def _block_scalar(self, indicator: str, key_indent: int) -> str:
        folded = indicator.startswith(">")
        chomp = "clip"
        body = indicator[1:]
        if body.startswith("+"):
            chomp = "keep"
            body = body[1:]
        elif body.startswith("-"):
            chomp = "strip"
            body = body[1:]
        explicit = int(body) if body else None
        content_indent: int | None = None
        if explicit is not None:
            content_indent = key_indent + explicit
        collected: list[str] = []
        while self.index < len(self.lines):
            line = self.lines[self.index]
            if line.strip() == "":
                collected.append("")
                self.index += 1
                continue
            indent = self._indent(line)
            if content_indent is None:
                if indent <= key_indent:
                    break
                content_indent = indent
            if indent < content_indent:
                break
            collected.append(line[content_indent:])
            self.index += 1
        while collected and collected[-1] == "":
            if chomp == "keep":
                break
            collected.pop()
        if folded:
            text = self._fold(collected)
        else:
            text = "\n".join(collected)
        if chomp == "strip":
            return text.rstrip("\n")
        if chomp == "keep":
            return text if text.endswith("\n") else text + "\n"
        if text == "":
            return ""
        return text.rstrip("\n") + "\n"

    def _fold(self, lines: Sequence[str]) -> str:
        paragraphs: list[str] = []
        buffer: list[str] = []

        def flush() -> None:
            if buffer:
                paragraphs.append(" ".join(buffer))
                buffer.clear()

        for line in lines:
            if line == "":
                flush()
                paragraphs.append("")
            else:
                buffer.append(line)
        flush()
        return "\n".join(paragraphs)

    def _flow(self, first: str) -> object:
        text = first.strip()
        while not self._balanced(text):
            if self.index >= len(self.lines):
                raise GuardParseError("unclosed flow collection")
            text += " " + self.lines[self.index].strip()
            self.index += 1
        return self._parse_flow(text)

    def _parse_flow(self, text: str) -> object:
        if text[0] == "[":
            inner = text[1:-1].strip()
            if inner == "":
                return []
            return [self._scalar(part.strip()) for part in self._split_flow(inner)]
        inner = text[1:-1].strip()
        mapping: dict[str, object] = {}
        if inner == "":
            return mapping
        for part in self._split_flow(inner):
            key, value = self._split_key(part.strip())
            mapping[str(key)] = self._scalar(value) if value else None
        return mapping

    def _split_flow(self, text: str) -> list[str]:
        parts: list[str] = []
        start = 0
        depth = 0
        quote = ""
        for index, char in enumerate(text):
            if quote:
                if char == quote:
                    quote = ""
                continue
            if char in {"'", '"'}:
                quote = char
                continue
            if char in "[{":
                depth += 1
            elif char in "]}":
                depth -= 1
            elif char == "," and depth == 0:
                parts.append(text[start:index])
                start = index + 1
        parts.append(text[start:])
        return parts

    def _balanced(self, text: str) -> bool:
        stack: list[str] = []
        quote = ""
        pairs = {"]": "[", "}": "{"}
        for char in text:
            if quote:
                if char == quote:
                    quote = ""
                continue
            if char in {"'", '"'}:
                quote = char
                continue
            if char in "[{":
                stack.append(char)
            elif char in "]}":
                if not stack or stack[-1] != pairs[char]:
                    return False
                stack.pop()
        return not stack and quote == ""

    def _split_key(self, text: str) -> tuple[object, str]:
        quote = ""
        for index, char in enumerate(text):
            if quote:
                if char == quote:
                    quote = ""
                continue
            if char in {"'", '"'}:
                quote = char
                continue
            if char == ":" and (
                index + 1 == len(text) or text[index + 1] in {" ", "\t", "|", ">"}
            ):
                key = self._scalar(self._strip_comment(text[:index]).strip())
                rest = self._strip_comment(text[index + 1 :]).strip()
                if not isinstance(key, str) or key == "":
                    raise GuardParseError(f"line {self.index + 1}: empty key")
                return key, rest
        raise GuardParseError(f"line {self.index + 1}: expected a key: {text}")

    def _looks_like_key(self, text: str) -> bool:
        try:
            self._split_key(text)
        except GuardParseError:
            return False
        return True

    def _scalar(self, text: str) -> object:
        value = text.strip()
        if value in {"null", "Null", "NULL", "~"}:
            return None
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            inner = value[1:-1]
            if value[0] == "'":
                return inner.replace("''", "'")
            return (
                inner.replace("\\n", "\n")
                .replace("\\t", "\t")
                .replace('\\"', '"')
                .replace("\\\\", "\\")
            )
        return value

    def _strip_comment(self, text: str) -> str:
        quote = ""
        for index, char in enumerate(text):
            if quote:
                if char == quote:
                    quote = ""
                continue
            if char in {"'", '"'}:
                quote = char
                continue
            if char == "#":
                return text[:index]
        return text

    def _is_list_item(self, line: str, indent: int) -> bool:
        if line.strip() == "" or line.lstrip().startswith("#"):
            return False
        if self._indent(line) != indent:
            return False
        rest = line[indent:]
        return rest == "-" or rest.startswith("- ")

    def _skip_noise(self) -> None:
        while self.index < len(self.lines):
            line = self.lines[self.index]
            stripped = line.strip()
            if stripped == "" or stripped.startswith("#"):
                self.index += 1
                continue
            if stripped == "---" and self._indent(line) == 0:
                self.index += 1
                continue
            break

    def _indent(self, line: str) -> int:
        spaces = 0
        for char in line:
            if char == " ":
                spaces += 1
                continue
            if char == "\t":
                raise GuardParseError(f"line {self.index + 1}: tab indentation")
            break
        return spaces

    def _content_indent(self, line: str) -> int:
        if line.strip() == "":
            return 0
        return self._indent(line)


def loads(text: str) -> object:
    return _Parser(text).parse()


def _git(root: Path, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    env = {
        "GIT_TERMINAL_PROMPT": "0",
        "PATH": os.environ.get("PATH", ""),
        "LC_ALL": "C",
    }
    home = os.environ.get("HOME")
    if home:
        env["HOME"] = home
    return subprocess.run(
        ["git", *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def resolve_scope(root: Path, event: str, before: str) -> tuple[bool, tuple[str, ...]]:
    """Changed paths for one event, or unresolved.

    Never substitutes the repository root or ``tests/`` when the diff cannot
    be named. A push uses the previous commit only when that object is local.
    """

    if event in PR_EVENTS:
        if _git(root, ["rev-parse", "--verify", "--quiet", "HEAD^1"]).returncode != 0:
            return False, ()
        if _git(root, ["rev-parse", "--verify", "--quiet", "HEAD^2"]).returncode != 0:
            return False, ()
        diff = _git(root, ["diff", "--name-only", "HEAD^1", "HEAD^2"])
        if diff.returncode != 0:
            return False, ()
        return True, tuple(line for line in diff.stdout.splitlines() if line)
    if event == "push":
        if not re.fullmatch(r"[0-9a-f]{40}", before) or before == "0" * 40:
            return False, ()
        if _git(root, ["cat-file", "-e", f"{before}^{{commit}}"]).returncode != 0:
            return False, ()
        diff = _git(root, ["diff", "--name-only", before, "HEAD"])
        if diff.returncode != 0:
            return False, ()
        return True, tuple(line for line in diff.stdout.splitlines() if line)
    return False, ()


def main() -> int:
    event = os.environ.get("GH_EVENT", "").strip()
    if event == "":
        sys.stdout.write(
            "MALFORMED_EVENT\nheavy=false\nsuite_pass=false\nselected_suite=none\nreceipt=static\n"
        )
        return 1
    root = Path(os.environ.get("GUARD_WORKSPACE", "."))
    if event not in JUDGED_EVENTS:
        decision = judge(root, event=event, scope_resolved=True)
    else:
        resolved, files = resolve_scope(root, event, os.environ.get("GUARD_BEFORE", ""))
        decision = judge(
            root,
            event=event,
            scope_resolved=resolved,
            changed_files=files,
        )
    sys.stdout.write(protocol(decision))
    print("static judge is not a hosted test receipt", file=sys.stderr)
    for line in decision.findings[:20]:
        print(line, file=sys.stderr)
    return decision.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
