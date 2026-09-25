# Agent Instructions — `l9-ci-core`

This file is the in-repo agent SSOT for the active L9 CI architecture.

Read before changing Core:

1. `.l9/architecture.yaml`
2. `.l9/ownership.yaml`
3. `.l9/org-runtime-contract.yaml`
4. `.l9/org-runtime-interface.yaml`
5. `.l9/sdk-compatibility.yaml`
6. `.l9/release-plane.yaml`

## 1. Cardinal architecture

`l9-ci-core` is the **central CI orchestrator** for Quantum-L9.

> A Quantum-L9 repository may describe itself, but it may not define how
> Quantum-L9 CI governs it.

The authority split is:

```text
GitHub organization rulesets
    ├─ target repositories
    └─ require Core's organization workflow
                 │
                 ▼
Quantum-L9/l9-ci-core
    ├─ orchestration
    ├─ central governance defaults
    ├─ permissions and trust boundaries
    ├─ SDK/tool version selection
    ├─ enforcement
    └─ publication
                 │
                 ▼
Quantum-L9/l9-ci-sdk
    ├─ repository capability detection
    ├─ provider execution
    ├─ canonical evidence/findings
    ├─ technical gate evaluation
    └─ deterministic projections
```

`Quantum-L9/.github` is **not** a CI distribution plane. Do not seed or sync L9
CI workflows or governance packs from it.

There is no separate `l9-ci-control-plane` in the active architecture.

## 2. Organization entrypoint

The organization enforcement source is:

`.github/workflows/org-ci.yml`

It declares:

- `pull_request` for normal required PR execution;
- `merge_group` for merge-queue execution;
- `workflow_dispatch` for controlled canaries;
- `workflow_call` only as a compatibility/test surface.

Normal organization enforcement is through a GitHub organization ruleset that
requires this workflow. Consumers do not copy it.

For direct ruleset events Core owns the event/profile mapping:

- `pull_request` → `pr_fast`
- `merge_group` → `merge`

Manual canaries default to `nightly`, which is advisory in the central defaults.

## 3. Consumer repository contract

A consumer repository may optionally contain `.l9/ci.json` using
`l9.ci-consumer/v1`.

The only allowed fields are:

```json
{
  "schema": "l9.ci-consumer/v1",
  "owner": "Quantum-L9/platform",
  "repo_class": "auto",
  "waiver_refs": []
}
```

`repo_class` is `auto`, `python`, or `typescript` and is only a consistency
assertion against SDK capability detection. It cannot enable a capability the
SDK does not observe.

Consumer metadata may never set:

- organization workflow source or revision;
- Core or SDK revision for required CI;
- provider enablement or requiredness;
- `blocking` / `advisory` / `shadow` / `disabled` mode;
- tool versions;
- workflow permissions;
- organization policy;
- required checks.

Unknown metadata fields fail closed.

## 4. Central governance

The standard organization governance bundle is shipped with the pinned
`resolve-governance` action under:

`.github/actions/resolve-governance/defaults/`

Exactly six documents are permitted:

- `execution-profiles.yaml`
- `rule-modes.yaml`
- `provider-requiredness.yaml`
- `quality-thresholds.yaml`
- `waivers.yaml`
- `promotion-policy.yaml`

`resolve-governance` uses `@core-defaults` for the normal organization path.
The consumer checkout is not expected to contain `.github/governance` or a
copied Core defaults directory.

A repository may reference a centrally issued waiver ID in `.l9/ci.json`; the
reference is valid only if the central waiver registry says that waiver applies
to the repository/ref/profile/provider. A consumer cannot mint a waiver.

## 5. Core ⇄ SDK boundary

Dependency direction is one-way:

`l9-ci-core → l9-ci-sdk`

Core must not implement SDK-owned semantics.

Core owns:

- GitHub Actions topology and permissions;
- exact-revision checkout;
- immutable SDK provisioning and compatibility probing;
- central execution/profile/provider governance;
- exact CI tool pins;
- artifact routing, retention, and publication;
- organization enforcement of the SDK technical gate.

SDK owns:

- repository capability detection (`l9-ci providers detect`);
- provider execution and provider-native parsing;
- canonical evidence/findings and identity;
- canonical bundle validation;
- technical gate evaluation;
- agent-review and SARIF projections.

`gate evaluate` is a **technical SDK gate**, not the final organization
Assurance decision. The future constellation boundary is: SDK observes,
Assurance decides, Core publishes/enforces the authoritative decision.

## 6. SDK pinning

`.l9/sdk-compatibility.yaml` is the only Core compatibility allowlist.

SDK revisions must be full 40-character commit SHAs. Floating refs, branches,
tags, short SHAs, and unlisted revisions are refused.

Compatibility selection has no ambient package bootstrap. `provision-sdk` parses
the bounded compatibility manifest with the Python standard library, verifies
the selected checkout's `requirements.txt` digest, and installs only a
Core-owned, platform-specific wheel closure with `--require-hashes` and
`--only-binary :all:`. Never install the SDK checkout requirements file. Generate
locks with `.github/actions/provision-sdk/lock_runtime.py`; land lock and manifest
support before a successor commit activates new workflow pins.

The active Core path requires these SDK capabilities, including:

- `providers detect`
- `semgrep run`
- `gate evaluate`
- `bundle validate`
- `bundle project-agent-payload`
- `bundle project-sarif`
- `compatibility check`

When an SDK candidate is promoted, update the compatibility manifest only after
its public contract and tests prove the required surface.

## 7. Workflow inventory

`tests/workflows/test_phase_scope.py` locks the workflow inventory.

The central public organization surface is `org-ci.yml`.

Other reusable workflows remain implementation, compatibility, or historical
surfaces until separately retired. Do not create a new consumer distribution
path or another organization entrypoint as an opportunistic change.

Legacy copy-first surfaces under `docs/templates/`, `presets/`, starter
workflows, and `skills/l9-ci-activation*` are frozen. They receive no new
features and should be removed after the central required-workflow canary is
proven. Do not distribute them from `Quantum-L9/.github`.

## 8. Cross-repository execution rules

A ruleset-required workflow executes against a consumer repository checkout.
Therefore:

- Core composite actions referenced from the central workflow use fully
  qualified immutable Core SHAs.
- Never assume a Core repository file exists in `GITHUB_WORKSPACE`.
- Data that is part of Core policy must ship inside the pinned Core action or
  be produced by Core itself.
- Consumer-relative paths must remain inside `GITHUB_WORKSPACE`.
- External actions are SHA-pinned.
- The integrity checker scans workflow and composite-action `.yml` and `.yaml`
  recursively; every remote `uses:` edge is a full lowercase commit SHA.
- Composite actions are local leaf adapters and may not nest a remote
  `Quantum-L9/l9-ci-core` action edge.
- Never use floating `@main` references.

## 9. Publication and failure semantics

A technical finding and an infrastructure/contract failure are not equivalent.

- `blocking`: a non-passing SDK technical gate fails the required workflow.
- `advisory`: findings are visible without blocking; contract/infrastructure
  failures remain fatal.
- `shadow`: retain evidence without treating it as a required decision.
- `disabled`: provider is not invoked.

The publication job must execute with `always()` after an enabled analysis so a
blocking failure still produces the failure publication. Never derive a green
publication solely because a scanner process was allowed to exit zero.

## 10. MANIFEST.sha256

`MANIFEST.sha256` records SHA-256 digests for tracked contract/runtime files.
Regenerate entries for every changed listed file and remove entries for deleted
files.

Both `make validate` and `tests/tools/test_manifest_integrity.py` verify the
manifest. `L9_MANIFEST_CHECK=0` exists only for a bounded salvage/bisect command;
it is not an acceptance path.

## 11. Repository execution runtime

The repository execution contract is `.l9/repo-workflow.json`, validated by
`.l9/repo-workflow.schema.json`.

Before declaring a Core change complete:

1. `make validate`
2. `make change-policy`
3. `make check`
4. `python3 -m unittest discover tests`
5. `make agent-check`

The repo-local runtime preserves:

- evidence emission;
- argv-only command execution;
- toolchain resolution through the workspace interpreter: `commands.check` and
  `self-ci.yml`'s lint job both run ruff and mypy as `python -m <tool>`, so
  `PATH` cannot decide which code the gate executes, and
  `tools/check_toolchain_versions.py` runs first in each to assert the
  resolved versions against `toolchain-lock.json`, the canonical version
  owner. It also resolves each module under the gate's own `sys.path` — `-m`
  puts the working directory first, so metadata proves what is installed, not
  what would be imported. `make setup` alone does not remediate a shadowed
  `PATH`: installing the pin and resolving it are different problems. See
  `docs/repository-execution-runtime.md`;
- deterministic change-policy behavior;
- non-mutation of the worktree during validation;
- single-flight locking (`reconcile` is the one remaining mutating target);
- generated-facade parity between `Makefile` and `tools/l9_make/Makefile.template`;
- the Core → SDK dependency boundary.

Cursor-Governance owns, and this runtime must not reimplement:

- branch publication policy;
- protected-branch publication denial;
- push semantics;
- pull-request creation and reuse;
- publication single-flight and overlap policy;
- publication remediation.

A change duplicating SDK behavior is invalid even if functional tests pass.
A change reintroducing publication into this runtime is invalid for the same
reason: it would recreate a second publication authority.

### The generated adapter and local extension layers

The root `Makefile` is generated by `tools/l9_make` from
`tools/l9_make/Makefile.template`; it owns only the universal bootstrap
vocabulary and Governance delegation.
`tools/l9_make` validates the authoritative `l9.make-plan/v1` schema and
renders deterministic `Repo.mk` output from a resolved capability plan.

`tools/l9_repo/Makefile.template` is a byte-identical compiler-generated
compatibility projection retained only for the currently pinned Organization CI
action. It is not source authority and must be rendered and checked together
with `Makefile`; remove it only when the CI pin advances.

Standard capability names are `doctor`, `setup`, `build`, `lint`, `test`,
`validate`, `package`, `generate`, `benchmark`, `status`, and `clean`; inspect
their explicit `supported`, `not_required`, or `unsupported` state with
`make capabilities`. `make check` is a documented compatibility alias during
the V2 migration. `NOT_REQUIRED` returns zero and `UNSUPPORTED` returns two.
Never silently omit a known standard capability.

`Repo.mk` is generated and must never be hand-edited. `Repo.local.mk` is an
optional repository-authored extension layer; when present, it may add
Core-native extensions but may not override a generated or universal target, a
reserved Governance target, `pr`, `push`, `release`, or `deploy`. Neither layer
may implement organization governance. The generated `Repo.mk` include is
mandatory; a missing optional local extension is valid for a newly adopting
repository. If either generated artifact drifts, run
`python3 -m tools.l9_repo reconcile`; use `make make-render` for the explicit
two-artifact render, and `make make-check` to verify schema, rendered bytes, and
local-layer ownership.

The Core compiler does not detect repository capabilities, infer languages, or
consume a provider at render time. The permanent downstream plan path is
`.l9/make-plan.json`, but it remains unavailable until the SDK/Core owner
approves a versioned immutable envelope. Until then, no hand-authored plan may
pretend to be SDK evidence; this Core reference uses its checked-in plan only.
Governance verbs (`make start`, `make pr`, `make workspace-clean`,
`make wiring-check`) route only to the `l9` dispatcher.

### The contract shape is pinned

`.l9/repo-workflow.json` still declares `push` and `pull_request`. No code path
reads them except `pull_request.base`, which survives as the comparison ref.
They stay because `org-ci.yml` pins `run-repository-verification@<sha>` to a
Core checkout whose validator requires them and rejects an unknown
`repository.default_branch`. **Do not remove them, and do not add
`default_branch`, until that pin advances** — either change fails organization
CI. See `docs/repository-execution-runtime.md` for the removal trigger.

## 12. Release plane

Contract: `.l9/release-plane.yaml` (`l9.release-plane/v1`), asserted by
`tests/workflows/test_release_plane.py`. Runbook: `docs/release/README.md`.

- **`main` is the production CI runtime.** The GitHub organization ruleset
  binds governed repositories to `Quantum-L9/l9-ci-core` / `main` /
  `.github/workflows/org-ci.yml` directly. A merge to `main` is production
  for the next governed `pull_request` or `merge_group` evaluation. Nothing
  is propagated to or pinned in a consumer.
- **Releases are immutable audit anchors** (`vMAJOR.MINOR.PATCH`): audit,
  provenance, rollback identity, release notes. They carry no runtime
  authority and distribute nothing.
- **No moving major release alias.** Core releases never create or move
  `v2`. `refs/tags/v2` is a mutable compatibility tag, not a Core release;
  it serves `install-consumer-ci@v2` and the narrowly declared optional
  Cognitive Runtime release integration, and is moved only by
  `tools/publish_consumer_ci_tag.sh` after review.
- **The release gate reads the version from `.l9/repo-spec.yaml`.** Do not
  hard-code a release number in `release-validation.yml`.
- **Ruleset events are `pull_request` and `merge_group` only.** Core's
  native `push` trigger is not organization-wide push fanout.
- **SDK promotion is one governed Core PR** editing
  `.l9/sdk-compatibility.yaml` at an exact 40-character SHA. Never a
  downstream change.
- The L9-ORG-008 clarification (ruleset source-branch binding is not a
  reusable-workflow `@main` reference) is recorded in the contract as a
  proposal. It is organization law only once Cursor-Governance records it.
- **One writer per tag namespace.** `release_writers` in the contract names
  the single authorized writer for exact `vX.Y.Z` releases
  (`docs/release/tag-and-release.sh`) and for the transitional `v2` installer
  tag (`tools/publish_consumer_ci_tag.sh`); neither may mutate the other's
  namespace. `tools/check_release_writers.py` (`make check-release-writers`,
  and part of the `unittest` suite) proves it. Do not add a second executable
  surface that creates, moves, or pushes either namespace.
- **Live GitHub state is attested, never assumed.**
  `tools/verify_control_plane.py` (`make attest-control-plane`, workflow
  `.github/workflows/control-plane-attestation.yml`) compares GitHub against
  `core_main_protection`, `production.source`, and `core_release.immutable`
  in the contract. It is read-only — only `GET` requests, and it mutates no
  ruleset, branch, setting, release, or tag. `UNKNOWN` is not `PASS`: report
  what it reported, and never restate an unverified state as confirmed.
- **External action pin discovery covers `.yml` and `.yaml`.** GitHub loads a
  workflow or composite action from either spelling; do not add a second
  discovery mechanism that reads only one.

<!-- BEGIN L9 FORMATTER OWNERSHIP (generated — do not edit) -->

## 13. Repository Execution V2 (2026-09-25) — supersedes §11's generated-adapter text

This section supersedes, in §11, "The generated adapter and local extension
layers", "The contract shape is pinned", and the two invariants naming
`tools/l9_make` and `make capabilities`. The completion sequence at the top of
§11 (`make validate`, `make change-policy`, `make check`, the full `unittest`
suite, `make agent-check`) is unchanged.

### The consumer contract

`.l9/repo-workflow.json` is the exact three-field Repository Execution V2
declaration and nothing else:

```json
{
  "schema": "l9.repo-execution/v2",
  "facade": "make-v1",
  "required_phases": ["setup", "validate", "check", "test"]
}
```

It carries no commands, no policy, and no metadata. Any additional field,
another schema, an unsupported facade, or a phase list that is not exactly
`setup, validate, check, test` in that order fails closed, both in the
organization admission bridge (`.github/actions/run-repository-verification`)
and in `tools/l9_repo`. `.l9/repo-workflow.schema.json` is retired; validation
is standard-library only.

### The Make ownership model

```text
Makefile   generated, Core-owned: the released make-v1 facade, byte-for-byte
           setup -> repo-setup   validate -> repo-validate
           check -> repo-check   test     -> repo-test
Repo.mk    repository-owned, hand-maintained, never generated or rewritten
```

`make-v1` is a real version: `tools/l9_repo/facades/make-v1.mk` is mapped
explicitly by `FACADE_TEMPLATES` in `tools/l9_repo/contract.py` and is
immutable once released. An incompatible facade change is `make-v2`, never a
silent edit of `make-v1`. The portable facade exposes exactly `help` plus the
four phases. It contains no publication or Governance target and no `l9`
dispatcher variable: Cursor-Governance owns its own commands, and
`l9-ci-core` does not proxy them. `Repo.local.mk`, the capability-plan
compiler (`tools/l9_make`), the generated `Repo.mk`, and the
`tools/l9_repo/Makefile.template` projection are retired.

`Repo.mk` must declare `repo-setup`, `repo-validate`, `repo-check`, and
`repo-test`, each `.PHONY`, and may not redefine a facade verb. Everything
else in it is this repository's business: Core keeps `lint`, `doctor`,
`clean`, `status`, `change-policy`, `agent-check`, `core-validate`,
`reconcile`, `attest-control-plane`, and `check-release-writers` there as
Core-local targets outside the portable ABI. `python3 -m tools.l9_repo
reconcile` regenerates only `Makefile`, idempotently; it never writes
`Repo.mk`.

### Core-local policy

Policy that is Core's and not a consumer's — the comparison ref, clean paths,
the reconcile lock, change gates and companion rules, agent-contract wiring,
evidence reporting, and authority paths — lives in
`.l9/core-repo-policy.json` (`l9.core-repository-policy/v1`). `make validate`
runs the portable V2 validation (`validate`) and then `core-validate`, which
adds the checksum manifest, contract wiring, and authority-path checks. The
Repository Execution V2 test suite is `tests/repo_execution/`; the
`repository-execution` change gate runs it whenever `tools/l9_repo/`,
`tests/repo_execution/`, `.l9/repo-workflow.json`, `.l9/core-repo-policy.json`,
`Makefile`, or `Repo.mk` changes.

### The V1 compatibility path

`RepositoryWorkflow` in `tools/l9_repo/__main__.py` is retained only for the
pinned admission bridge, which delegates a repository still declaring
`schema_version: 1` to it. It runs that contract's `commands` matrices
argv-only under the executable allowlist and nothing more; the superseded V1
structural validation is retired with the compiler it depended on. Core itself
is a V2 consumer.

## Formatter ownership

Workspace class: `biome_default` — Default for every governed workspace: Biome owns JS/TS/JSON, VS Code JSON language features owns JSONC (the Biome extension cannot format jsonc), Ruff owns Python, Prettier owns Markdown (format-on-save off so governance docs do not churn).

Exactly one formatter owns each language. Do not reformat a file with a tool other than its owner, and do not add config for a competing formatter: the result is a diff that churns on every save.

| Languages | Owner | Note |
|---|---|---|
| `javascript`, `javascriptreact`, `typescript`, `typescriptreact`, `json` | **biome** | bound by the governed IDE profile |
| `jsonc` | **vscode-json** | bound by the governed IDE profile |
| `python` | **ruff** | bound by the governed IDE profile |
| `markdown` | **prettier** | bound by the governed IDE profile |

Generated from `environment/ide/policy.json` in the governance clone by `ops/scripts/adapters/agentdocs.sh`. Edit the policy, not this block.

<!-- END L9 FORMATTER OWNERSHIP -->
