"""The Core tool-ownership manifest: one place that says which CI tool Core owns.

``l9.tool-ownership/v1`` joins, per tool, the version Core's
``toolchain-lock.json`` pins, the Core-owned consumer config paths the stamp
writes for it, the stamp profiles that carry it, the hook a stamped
``.pre-commit-config.yaml`` runs, and the Core workflow steps that run it.
It also states, per language, which tool formats, lints and type-checks it,
and records the tools Core deliberately does not own (``AGENTS.md`` §26).

The manifest is a *derived* artifact (L9-DERIVED-001): its authorities are
``toolchain-lock.json`` (versions), ``core-owned-tools.json`` (tool identity),
``stamp.py`` (profiles and files), and the authored ``TOOLS`` table below
(where each tool runs). It records the sha256 of each input so a reader can
tell which inputs it was rendered from (L9-PROV-001). It is rendered by
``python3 tools/render_tool_ownership.py`` and checked by ``--check`` and by
``tests/tools/test_tool_ownership.py``; it is never edited by hand.

A downstream policy — Cursor-Governance's IDE formatter-owner table — is meant
to be generated *from* the ``languages`` section here rather than declared a
second time.

Standard library only.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from collections.abc import Mapping
from typing import Any

from .contract import ContractError
from .stamp import (
    CORE_ROOT,
    PRE_COMMIT_PATH,
    PROFILE_FILES,
    PROFILES,
    TOOLCHAIN_LOCK,
    render_file,
)

SCHEMA = "l9.tool-ownership/v1"
MANIFEST_PATH = (
    CORE_ROOT / ".github" / "actions" / "install-consumer-ci" / "tool-ownership.json"
)
CORE_OWNED_TOOLS = (
    CORE_ROOT / ".github" / "actions" / "install-consumer-ci" / "core-owned-tools.json"
)
STAMP_SOURCE = CORE_ROOT / "tools" / "l9_repo" / "stamp.py"
OWNERSHIP_SOURCE = pathlib.Path(__file__).resolve()

JS_TS_JSON = ("javascript", "javascriptreact", "typescript", "typescriptreact", "json")

#: Stamped files that configure no tool: the V2 contract and its facade.
NON_TOOL_FILES: tuple[str, ...] = (".l9/repo-workflow.json", "Makefile")

#: Authored ownership facts. ``configs`` are Core-owned consumer paths written
#: by the stamp; ``runs`` are ``(workflow, job, step name)`` triples that exist
#: in Core's workflows (test-verified); ``formats`` / ``lints`` /
#: ``type_checks`` feed ``languages``.
TOOLS: Mapping[str, Mapping[str, Any]] = {
    "bandit": {
        "configs": (),
        "runs": (("security.yml", "security", "Python static security scan (bandit)"),),
    },
    "biome": {
        "configs": ("biome.json", ".biomeignore", ".editorconfig"),
        "runs": (
            ("org-ci.yml", "analyze", "Install Biome (digest-locked central pin)"),
            ("org-ci.yml", "analyze", "Run Biome check (blocking)"),
        ),
        "formats": JS_TS_JSON,
        "lints": JS_TS_JSON,
    },
    "gitleaks": {
        "configs": (".gitleaks.toml",),
        "runs": (("security.yml", "security", "Secret scan (gitleaks)"),),
    },
    "mypy": {
        "configs": ("mypy.ini",),
        "runs": (("self-ci.yml", "lint", "mypy"),),
        # Strict mypy is the single organization CI type gate (AGENTS.md §26).
        "type_checks": ("python",),
    },
    "pip": {
        "configs": ("requirements-ci.txt",),
        "runs": (),
    },
    "pip-audit": {
        "configs": (),
        "runs": (("security.yml", "security", "Python dependency audit (pip-audit)"),),
    },
    "pre-commit": {
        "configs": (PRE_COMMIT_PATH,),
        "runs": (),
    },
    "pytest": {
        "configs": ("pytest.ini",),
        "runs": (),
    },
    "python": {
        "configs": (".python-version",),
        "runs": (
            ("org-ci.yml", "analyze", "Set up locked Python for the central toolchain"),
        ),
    },
    "ruff": {
        "configs": ("ruff.toml",),
        "runs": (
            ("self-ci.yml", "lint", "ruff check"),
            ("self-ci.yml", "lint", "ruff format --check"),
        ),
        "formats": ("python",),
        "lints": ("python",),
    },
    "semgrep": {
        "configs": (),
        "runs": (
            ("org-ci.yml", "analyze", "Install Semgrep (hash-locked central pin)"),
            ("org-ci.yml", "analyze", "Run + normalize Semgrep (SDK)"),
        ),
    },
}

#: Tools Core deliberately does not own, with the decision and its trigger
#: (``AGENTS.md`` §26). A tool moves to ``TOOLS`` only together with a lock key
#: and something Core runs.
NOT_OWNED: Mapping[str, Mapping[str, str]] = {
    "actionlint": {
        "decision": "not locked: no Core workflow or stamped profile runs it",
        "evidence": "l9-ci-sdk runs actionlint in its own reusable workflow, not enforced",
        "adopt_when": "Core runs actionlint in a workflow or a stamped hook",
    },
    "eslint": {
        "decision": "not locked: Biome is the organization's JS/TS linter and formatter",
        "evidence": (
            "org-ci.yml runs only Biome; l9-meta-injector runs ESLint as a "
            "repository-local check beside blocking Biome (its ADR-095, Proposed)"
        ),
        "adopt_when": "Core runs ESLint centrally; until then ESLint is product-local",
    },
    "markdownlint": {
        "decision": "not locked: no Core workflow or stamped profile runs it",
        "evidence": "no governed repository read runs it",
        "adopt_when": "Core runs markdownlint in a workflow or a stamped hook",
    },
    "node": {
        "decision": "not locked: Core runs no Node program",
        "evidence": "Biome is a standalone binary (install-biome)",
        "adopt_when": "Core runs a Node program (tsc, vitest, ESLint) for consumers",
    },
    "pyright": {
        "decision": (
            "not locked, never a gate: strict mypy is the single organization "
            "CI type gate (CTO decision, 2026-11-15)"
        ),
        "evidence": (
            "every governed Python repository read runs mypy; l9-conformance "
            "runs Pyright strict and migrates to the stamped mypy gate"
        ),
        "adopt_when": (
            "never as a CI gate; a repository may use it as optional editor "
            "tooling outside Core and org CI"
        ),
    },
    "prettier": {
        "decision": "not locked: Core runs no Markdown formatter",
        "evidence": (
            "no governed repository read carries Prettier; Cursor-Governance's "
            "IDE policy names it for Markdown with format-on-save off"
        ),
        "adopt_when": "Core formats or checks Markdown in a workflow",
    },
    "typescript": {
        "decision": "not locked: tsc and tsconfig.json are product-owned",
        "evidence": "Core runs no tsc; the only TypeScript consumer read pins its own",
        "adopt_when": "Core runs tsc for consumers (a stamped tsconfig base follows)",
    },
    "uv": {
        "decision": "not locked: no Core workflow installs uv",
        "evidence": (
            "l9-repo-template's repo-setup runs uv when present, so repository "
            "verification depends on the runner image's uv"
        ),
        "adopt_when": "Core installs uv for repository verification",
    },
    "vitest": {
        "decision": "not locked: the TypeScript test runner is product-owned",
        "evidence": "Core runs no vitest; the only TypeScript consumer read pins its own",
        "adopt_when": "Core installs and runs the TypeScript test runner",
    },
    "yamllint": {
        "decision": "not locked: no Core workflow or stamped profile runs it",
        "evidence": "l9-ci-sdk runs yamllint in its own hooks and reusable workflow",
        "adopt_when": "Core runs yamllint in a workflow or a stamped hook",
    },
}

#: How organization CI reaches the consumer-run tools (ruff, the type checker,
#: pytest, pre-commit): it executes the consumer's make-v1 phases, and which
#: stamped tool a phase invokes is the product's ``Repo.mk``.
CONSUMER_EXECUTION: Mapping[str, str] = {
    "workflow": "org-ci.yml",
    "job": "analyze",
    "step": "Run repository verification contract",
    "phases": "setup validate check test",
}

#: Languages a governed IDE might format that no Core tool owns in CI.
UNOWNED_LANGUAGES: Mapping[str, str] = {
    "jsonc": "the stamped biome.json excludes **/*.jsonc; no Core tool checks it",
    "markdown": "Core runs no Markdown formatter or linter (see not_owned.prettier)",
}


def _digest(path: pathlib.Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _relative(path: pathlib.Path) -> str:
    return path.resolve().relative_to(CORE_ROOT).as_posix()


def _load_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError(f"unreadable {_relative(path)}: {error}") from error
    if not isinstance(data, dict):
        raise ContractError(f"{_relative(path)} must be a JSON object")
    return data


def _hooks_by_profile() -> dict[str, list[str]]:
    """The tool each stamped pre-commit hook runs, per profile that has one."""

    hooks: dict[str, list[str]] = {}
    for profile in PROFILES:
        if PRE_COMMIT_PATH not in PROFILE_FILES[profile]:
            continue
        text = render_file(PRE_COMMIT_PATH).decode("utf-8")
        tools: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("entry: python -m "):
                tool = stripped.removeprefix("entry: python -m ").split()[0]
                if tool not in tools:
                    tools.append(tool)
        hooks[profile] = tools
    return hooks


def _requirements_by_profile() -> dict[str, list[str]]:
    """The lock keys each stamped ``requirements-ci.txt`` installs."""

    installed: dict[str, list[str]] = {}
    for profile in PROFILES:
        if "requirements-ci.txt" not in PROFILE_FILES[profile]:
            continue
        text = render_file("requirements-ci.txt").decode("utf-8")
        names = []
        for line in text.splitlines():
            if line and not line.startswith("#"):
                names.append(line.split("==", 1)[0].split("[", 1)[0])
        installed[profile] = names
    return installed


def build_manifest() -> dict[str, Any]:
    """Render the manifest from its authorities; raise on any inconsistency."""

    lock = _load_json(TOOLCHAIN_LOCK)
    identity = _load_json(CORE_OWNED_TOOLS)
    identity_names = {tool["name"] for tool in identity.get("tools", [])}
    non_dependency = set(identity.get("non_dependency_lock_keys", {}))
    if set(lock) != set(TOOLS):
        raise ContractError(
            "every lock key needs exactly one ownership entry: "
            f"lock-only {sorted(set(lock) - set(TOOLS))}, "
            f"entry-only {sorted(set(TOOLS) - set(lock))}"
        )
    if identity_names | non_dependency != set(lock):
        raise ContractError("core-owned-tools.json does not name exactly the lock keys")
    if set(NOT_OWNED) & set(TOOLS):
        raise ContractError("a tool cannot be both owned and not owned")

    stamped = {path for files in PROFILE_FILES.values() for path in files}
    claimed: dict[str, str] = {}
    for name, entry in TOOLS.items():
        for path in entry["configs"]:
            if path not in stamped:
                raise ContractError(f"{name}: {path} is not a Core-stamped file")
            if path in claimed:
                raise ContractError(f"{path} is claimed by {claimed[path]} and {name}")
            claimed[path] = name
    unclaimed = stamped - set(claimed) - set(NON_TOOL_FILES)
    if unclaimed:
        raise ContractError(f"stamped files with no owning tool: {sorted(unclaimed)}")

    hooks = _hooks_by_profile()
    requirements = _requirements_by_profile()
    tools: list[dict[str, Any]] = []
    for name in sorted(TOOLS):
        entry = TOOLS[name]
        configs = list(entry["configs"])
        profiles = [
            profile
            for profile in PROFILES
            if any(path in PROFILE_FILES[profile] for path in configs)
            or name in requirements.get(profile, ())
        ]
        tools.append(
            {
                "name": name,
                "version": lock[name],
                "configs": configs,
                "profiles": profiles,
                "pre_commit_hook_profiles": [
                    profile for profile, used in hooks.items() if name in used
                ],
                "runs": [
                    {"workflow": workflow, "job": job, "step": step}
                    for workflow, job, step in entry["runs"]
                ],
            }
        )

    languages: dict[str, dict[str, Any]] = {}
    for name in sorted(TOOLS):
        entry = TOOLS[name]
        for role, key in (
            ("formats", "formatter"),
            ("lints", "linter"),
            ("type_checks", "type_checker"),
        ):
            for language in entry.get(role, ()):
                slot = languages.setdefault(language, {})
                if key in slot:
                    raise ContractError(f"{language} has two {key}s")
                slot[key] = name
    for language in UNOWNED_LANGUAGES:
        if language in languages:
            raise ContractError(f"{language} is both owned and unowned")

    return {
        "schema": SCHEMA,
        "owner": "Quantum-L9/l9-ci-core",
        "derived": True,
        "sources": [
            {"path": _relative(path), "sha256": _digest(path)}
            for path in (
                TOOLCHAIN_LOCK,
                CORE_OWNED_TOOLS,
                STAMP_SOURCE,
                OWNERSHIP_SOURCE,
            )
        ],
        "profiles": list(PROFILES),
        "non_tool_files": list(NON_TOOL_FILES),
        "consumer_execution": dict(CONSUMER_EXECUTION),
        "tools": tools,
        "languages": {language: languages[language] for language in sorted(languages)},
        "unowned_languages": dict(sorted(UNOWNED_LANGUAGES.items())),
        "not_owned": {name: dict(NOT_OWNED[name]) for name in sorted(NOT_OWNED)},
    }


def render_manifest() -> bytes:
    return (json.dumps(build_manifest(), indent=2) + "\n").encode("utf-8")
