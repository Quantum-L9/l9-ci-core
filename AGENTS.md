# Agent Instructions — `l9-ci-core`

This file is the in-repo agent SSOT for the active L9 CI architecture.

Read before changing Core:

1. `.l9/architecture.yaml`
2. `.l9/ownership.yaml`
3. `.l9/org-runtime-contract.yaml`
4. `.l9/org-runtime-interface.yaml`
5. `.l9/sdk-compatibility.yaml`

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

Preserve:

- protected-branch refusal;
- clean-tree requirements;
- no-force policy;
- single-flight locking;
- evidence emission;
- argv-only command execution;
- the Core → SDK dependency boundary.

A change duplicating SDK behavior is invalid even if functional tests pass.

<!-- BEGIN L9 FORMATTER OWNERSHIP (generated — do not edit) -->

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
