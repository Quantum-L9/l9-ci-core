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
- the single toolchain-version authority,
  `.github/actions/install-consumer-ci/toolchain-lock.json`, every Core copy of
  which is test-bound to it;
- the consumer-facing Biome check (`install-biome`, `run-biome-check`) and
  advisory toolchain-alignment reporting (`check-toolchain-alignment`);
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

`MANIFEST.sha256` is not a required tracked ledger. Do not add one, and do not
regenerate digest entries as a merge requirement. §10.1 records the retirement.

### 10.1 Retired (2026-10-20) — supersedes §10 above and the manifest sentence in §14

`MANIFEST.sha256` is no longer tracked and no acceptance path verifies it.
Every concurrent pull request rewrote adjacent lines of the digest ledger, and
the resulting merge conflicts outweighed what it added on top of SHA-pinned
actions and the hash-locked toolchain. `L9_MANIFEST_CHECK` is now opt-in:
`L9_MANIFEST_CHECK=1` makes `core-validate` and
`tests/tools/test_manifest_integrity.py` verify a locally generated manifest
during a salvage or bisect. The `manifest-integrity` companion rule and the
manifest entries of the other companion rules in `.l9/core-repo-policy.json`
are gone; the `self-ci.yml` reconciliation-hints step is gone. Do not
reintroduce a tracked digest ledger as a merge requirement.

## 11. Repository execution runtime

The repository execution contract is `.l9/repo-workflow.json`, validated by
`.l9/repo-workflow.schema.json`.

Before declaring a Core change complete:

1. `make validate`
2. `make change-policy`
3. `make check`
4. `make test`
5. `make agent-check`

`make test` is `repo-test`, the explicit PR unittest subset. `make test-deep`
is the full `unittest discover` over `tests/`.

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
- **A tool-version bump is one human pin-file PR** editing
  `toolchain-lock.json` together with its test-bound copies and any new
  `install-semgrep` / `install-biome` lock file. Merging it is production for
  `org-ci.yml`; `tools/publish_consumer_ci_tag.sh` then moves `v2` for
  `install-consumer-ci@v2` users. No Dependabot entry maintains the lock.
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

## 13. Repository contract admission (2026-10-15)

`.github/actions/run-repository-verification` is the organization admission
point for `.l9/repo-workflow.json` and owns three decisions; see
`docs/repository-execution-runtime.md` § Organization admission of repository
contracts.

- Contract generation is classified by Core: a `schema` key means Repository
  Execution V2, validated only by `tools/l9_repo/contract.py`; it never falls
  through to V1. Do not add a second V2 validator.
- A V2 consumer is admitted only with a byte-exact `make-v1` facade and an
  unconditional `Repo.mk` ABI, verified before any phase runs.
- Whether a contract is required is `contract-policy.json` in the pinned
  action, keyed by `github.repository`. Never add an action input, consumer
  metadata field, or contract field that selects or weakens the mode.
- (2026-10-20) "Unconditional `Repo.mk` ABI" includes implementation, not
  only declaration: each required leaf must carry an unconditional recipe or
  prerequisite. A bare `.PHONY` leaf with neither, or one whose only recipe is
  inside a conditional, exits zero without doing anything and is rejected at
  admission. This remains structural; proving that a recipe did real test
  work is a separate Core enforcement concern, never a `Repo.mk` text rule.

## 14. Repository Execution V2 (2026-09-25) — supersedes §11 except its completion sequence

This section supersedes all of §11 except the completion sequence at its top
(`make validate`, `make change-policy`, `make check`, `make test`,
`make agent-check`) and the Cursor-Governance ownership list, which are
unchanged. Superseded in particular: the contract being "validated by
`.l9/repo-workflow.schema.json`", `commands.check`, the parity invariant
naming `tools/l9_make/Makefile.template`, "The generated adapter and local
extension layers", and "The contract shape is pinned". Toolchain resolution
through the interpreter now lives in `Repo.mk`'s `repo-check` leaf.

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
`repo-test`, each `.PHONY`, and may not redefine a facade verb. `repo-test`
is the explicit PR unittest subset: `python -m unittest --verbose` of the
named modules, not `unittest discover`. It fails if any listed path is missing
or if collection is zero. `test-deep` is the full discover: `python -m
unittest discover --start-directory tests --pattern 'test_*.py' --verbose`.
Everything else in it is this repository's business: Core keeps `test-deep`,
`lint`, `doctor`, `clean`, `status`, `change-policy`, `agent-check`,
`core-validate`, `reconcile`, `attest-control-plane`, and
`check-release-writers` there as Core-local targets outside the portable ABI.
`python3 -m tools.l9_repo reconcile` regenerates only `Makefile`,
idempotently; it never writes `Repo.mk`.

### Core-local policy

Policy that is Core's and not a consumer's — the comparison ref, clean paths,
the reconcile lock, change gates and companion rules, agent-contract wiring,
evidence reporting, and authority paths — lives in
`.l9/core-repo-policy.json` (`l9.core-repository-policy/v1`). `make validate`
runs the portable V2 validation (`validate`) and then `core-validate`, which
adds contract wiring and authority-path checks. The checksum manifest is not
a required tracked ledger; `core-validate` verifies a locally generated
manifest only when `L9_MANIFEST_CHECK=1` (§10.1). The
Repository Execution V2 test suite is `tests/repo_execution/`; the
`repository-execution` change gate runs it whenever `tools/l9_repo/`,
`tests/repo_execution/`, `.l9/repo-workflow.json`, `.l9/core-repo-policy.json`,
`Makefile`, or `Repo.mk` changes.

### The V1 compatibility path

`RepositoryWorkflow` in `tools/l9_repo/__main__.py` is retained only for the
admission bridge, which delegates a repository still declaring
`schema_version: 1` to it in `migration` mode (§13). The bridge pinned in
`org-ci.yml` imports its *own* checkout's `RepositoryWorkflow`, so this bounded
executor takes effect for V1 consumers only once the pin advances to a
revision of this tree. It runs that contract's `commands` matrices argv-only
under the executable allowlist and nothing more; the superseded V1 structural
validation is retired with the compiler it depended on. Core itself is a V2
consumer.

## 15. Legacy surfaces retired (2026-10-22) — supersedes §7 paragraphs 3–4, the §2 trigger list, and the §9 publication-job sentence

The central required-workflow canary is proven: organization ruleset
`L9 canonical CI required` (id 21895545) has been `active` since 2026-08-31,
binding every governed repository's default branch to
`Quantum-L9/l9-ci-core` / `main` / `.github/workflows/org-ci.yml` with the
required check `Analyze (central Core)`, and that check has been green on
`pull_request` and `push` evaluations across the fleet. The copy-first
surfaces §7 froze are therefore removed rather than evolved:

- `presets/python`, `presets/typescript/.github`, `starter-workflows/`,
  `docs/templates/`, and `skills/l9-ci-activation*` are gone. Only the locked
  consumer Biome contract (`presets/typescript/biome.json`, `stamp.sh`)
  remains; see `docs/ACTIVATION.md` for how a consumer activates.
- The v1 reusable kernels `pr-pipeline.yml`, `pre-commit-ci.yml`,
  `nightly.yml`, `release-publish.yml`, `trio-governance.yml`,
  `scorecard.yml`, `sbom.yml`, and the consumer-concept `baseline-ratchet.yml`
  are deleted from `main`. A consumer that pinned one at a full SHA keeps
  resolving that revision; none may be referenced from Core again.
- The dead `.github/governance/` dogfood pack (which overrode `pr_fast` and
  `merge` Semgrep to advisory and was read by no runtime path) is deleted.
  The one governance pack is `.github/actions/resolve-governance/` (§4);
  `self-analysis.yml` resolves the same `@core-defaults` consumers get.
- `tests/workflows/test_phase_scope.py` locks the reduced inventory and
  asserts the retired names and trees do not return.
- Historical migration evidence (`docs/central-ci-bridge/`,
  `docs/org-runtime/baseline-inventory.md`, `docs/v1-compatibility.md`,
  `docs/pipeline-runs/`) is removed from the tree; git history keeps it.

§2 is corrected: `org-ci.yml` also declares a native `push` trigger, narrowed
at runtime to the repository's own default branch and resolved to the `merge`
profile (`.l9/org-runtime-contract.yaml`). §9's "publication job with
`always()`" describes `analyze-semgrep.yml` / `publish-analysis.yml`; the
required `org-ci.yml` surface is read-only on `pull_request` and its job
conclusion is the enforcement signal.

Still retained, deliberately: `security.yml` (dogfooded by
`self-security.yml`), `analyze-semgrep.yml` + `publish-analysis.yml` (the
self-analysis dogfood and the bounded Cognitive Runtime `v2` channel in
`.l9/release-plane.yaml`), and the Phase 2/4 reusable surfaces
`normalize-semgrep-report.yml`, `profile-normalize-semgrep.yml`,
`publish-sarif.yml` (declared capabilities of `.l9/publication-contract.yaml`;
retire them only with a contract change).

## 16. Enforcement capability and the pin-advance step (2026-10-22)

Organization CI is merge-blocking only where every layer says so: the
organization ruleset (`active`, strict required status checks — recorded as
code in `Quantum-L9/.github` `rulesets/l9-canonical-ci-required.json`), the
central rule mode (`blocking` for `pr_fast`/`merge`), the finding policy's
`mode` for the rule that fired, and the step that enforces the result. Before
this section the finding policy defaulted every Semgrep finding to `advisory`,
so "blocking mode" could never fail a job on a finding; the Biome check had
only an `advisory` mode; and repository verification tolerated an absent
contract everywhere. This section records the capability that closes those
gaps and the one step that activates it.

- `.github/actions/resolve-governance/semgrep-policy.yaml` (version 2):
  `defaults.mode: blocking`. A rule is advisory only by explicit entry with
  a `policy_key`, after org review; the entries and their reasons are in the
  file. Fleet evidence at the flip (latest `org-ci.yml` bundles): Core 1
  finding (the env-sourced-argv audit rule, classified advisory),
  `l9-ci-sdk` 0, `l9-deploy` 2 (`broad-except`, advisory),
  `l9-semantic-compiler-engine` 2 medium `insecure-hash-algorithm-sha1`
  findings that will block that repository's next evaluation unless fixed or
  classified. `sdk-contract-check.yml` now proves the blocking verdict end to
  end (`tests/workflows/test_sdk_contract_smoke_verdict.py`).
- `run-biome-check` defines `advisory` and `blocking`; `blocking` fails the
  step on findings after the record and summary are written, and
  infrastructure failures stay fatal in both. The mode is set in
  `org-ci.yml` by Core, never by a consumer (AGENTS-ADDENDUM.md).
- `run-repository-verification/contract-policy.json` enrols
  `Quantum-L9/l9-ci-core` in `required` mode: Core's own contract must be
  present, valid, and pass `make setup → validate → check → test`. The fleet
  default stays `migration`; a repository is enrolled by a Core PR once it
  declares the contract (`docs/ACTIVATION.md`).
- `docs/release/rulesets/core-main-protection.json` is the evidence-backed
  payload for the Core `main` ruleset `.l9/release-plane.yaml` expects
  (`pull_request` with code-owner review, strict required self-CI checks).
  Applying it is an organization-admin action; `make attest-control-plane`
  proves it.

### Activation is a pin advance

Composite actions and their bundled data reach the fleet only at the
revision `org-ci.yml` pins (§8). The changes above are therefore inert until
the pins advance to a `main` revision that carries them. The procedure is
one mechanical PR, after this section's change has merged:

```bash
python3 tools/advance_core_action_pins.py <merge sha of this change>
# and, in the same PR, switch run-biome-check to blocking:
#   org-ci.yml:  mode: advisory  ->  mode: blocking
python3 tools/advance_core_action_pins.py <merge sha> --check
make validate && make change-policy    # workflow gate + tests/workflows
```

`tools/advance_core_action_pins.py` rewrites only the SHA on
`uses: Quantum-L9/l9-ci-core/.github/actions/<name>@<sha>` lines, refuses
anything but a full lowercase SHA, and `--check` proves every Core action pin
is at one revision. Never pin a branch or tag; never advance to a revision
that is not an ancestor of `main`.

What stays advisory on purpose: toolchain-alignment reporting
(`check-toolchain-alignment`) — it reports consumer pin drift against the
lock and is a drift *report*, not a test of the repository. Promote it only
with a governed change to the action and this section.

## 17. Required repositories are a projection of the L9 registry (2026-11-02) — supersedes the §16 enrolment sentence

`run-repository-verification/contract-policy.json` `required_repositories` is
no longer enrolled by hand. It is a projection of the canonical L9 member list,
`semantics/repository_registry.yaml` in `Quantum-L9/.github`
(`l9.repository-registry/global@1`), where "a repository is a member because it
is listed here with a class_ref" and an unlisted repository has no implied
membership. Every member whose `lifecycle` is `current` is required;
`superseded` and `retired` coordinates are historical and are not. The policy's
`schema` and `default_mode` (`migration`, for non-members) are unchanged.

- Generate with `python3 tools/project_required_repositories.py --ref main`; it
  reads the registry over git at an exact commit, fails closed on any entry it
  cannot classify, and writes the policy plus
  `required-repositories.receipt.json` (source repository, path, commit,
  sha256, rule, members, members digest). The receipt is a non-canonical
  projection record; the registry stays the authority.
- `--ref <sha> --check` compares the shipped files with the registry at that
  commit; `--verify` checks offline that the policy list is exactly the
  receipt's members (`tests/tools/test_project_required_repositories.py`).
  Never hand-edit `required_repositories`: change the registry, then
  re-project.
- `required` admits only a valid V2 contract (§14) and fails an absent one.
  Projecting a member therefore makes its organization CI fail until it
  declares that contract. As projected from `Quantum-L9/.github@133f243`,
  33 members are required; at that time only Core declared V2.
- Activation is still the §16 pin advance, to the merge revision of the
  projection, never to an unmerged commit.

## 18. Fleet activation (2026-11-02) — the §16 pin advance, applied

`org-ci.yml`'s 16 Core action pins advance to `1bc4df6` (the merge of the
§17 projection, an ancestor of `main`), and `run-biome-check` switches from
`mode: advisory` to `mode: blocking`. What this makes live for every governed
repository's next evaluation:

- repository verification is `required` for every current member of the
  `Quantum-L9/.github` registry (33 at `133f243`): an absent or V1
  `.l9/repo-workflow.json` fails `Analyze (central Core)`. At activation only
  Core declared V2;
- Semgrep findings block by default (`semgrep-policy.yaml` v2);
- Biome findings block where a `biome.json`/`biome.jsonc` exists; a
  repository without one is not applicable, and infrastructure failures stay
  fatal.

`analyze-semgrep.yml`'s `install-semgrep` pin moves with it, to `1bc4df6`,
so every Semgrep-installing workflow keeps one locked install path
(`tests/workflows/test_workflow_toolchain_pins.py`); the action's bytes are
identical between `def55c5` and `1bc4df6`, so nothing it installs changes.

Prove the pins with `python3 tools/advance_core_action_pins.py <sha> --check`.

## 19. SDK `6d70e829` activation (2026-11-05) — the §16 pin advance, applied

Two-step promotion per §16, never to an unmerged commit. Step 1
(`0e23a1b`, PR #232) made `l9-ci-sdk` `6d70e829` the default compatible
revision in `.l9/sdk-compatibility.yaml` (bc678190 kept as the rollback) and
regenerated the provision-sdk runtime lock. This step activates it:

- `org-ci.yml`'s 16 Core action pins advance to `0e23a1b`, an ancestor of
  `main`, and its `sdk-revision` fallback moves from `bc678190` to `6d70e829`;
- `analyze-semgrep.yml`'s `install-semgrep` pin moves with it, to `0e23a1b`,
  keeping one locked Semgrep install path. Its bytes and `invoke-sdk`'s are
  identical between `1bc4df6` and `0e23a1b`, so nothing it installs changes.

Between `1bc4df6` and `0e23a1b` the only action changes are `provision-sdk`
(it now admits `6d70e829`) and `resolve-governance/semgrep-policy.yaml` v3
(PR #231: `python-logger-credential-disclosure` is advisory). What this makes
live for every governed repository's next evaluation:

- `l9-ci-sdk` `6d70e829` rulesets (its ADR-0018): AST-LOGGING-001/002/003
  match secret-like names in code positions, not message prose, and
  AST-ROUTING-004 reports outbound `/execute` dispatch, not a node's own
  ingress route. Detection of real secret logging and outbound dispatch is
  kept or widened;
- the format-string credential heuristic no longer blocks; its findings are
  still reported.

`publish-analysis.yml`, `publish-sarif.yml` and `normalize-semgrep-report.yml`
keep their own pins; they are not part of required Organization CI.

Prove the pins with `python3 tools/advance_core_action_pins.py 0e23a1b9404babacb87481289b694d860076d5f6 --check`.

## 20. SDK `d09d3963` activation (2026-11-06) — the §16 pin advance, applied

Two-step promotion per §16, never to an unmerged commit. Step 1
(`de4b514`, PR #235) made `l9-ci-sdk` `d09d3963` the default compatible
revision in `.l9/sdk-compatibility.yaml` (`6d70e829` kept as the rollback) and
regenerated the provision-sdk runtime lock (header only: `requirements.txt` is
byte-identical). This step activates it:

- `org-ci.yml`'s 16 Core action pins advance to `de4b514`, an ancestor of
  `main`, and its `sdk-revision` fallback moves from `6d70e829` to `d09d3963`;
- `analyze-semgrep.yml`'s `install-semgrep` pin moves with it, to `de4b514`,
  keeping one locked Semgrep install path. Its bytes and `invoke-sdk`'s are
  identical between `0e23a1b` and `de4b514`, so nothing it installs changes.

Between `0e23a1b` and `de4b514` the only action change is `provision-sdk` (it
now admits `d09d3963`). What this makes live for every governed repository's
next evaluation, from the `l9-ci-sdk` `d09d3963` rulesets (its ADR-0019):

- AST-LOGGING-001/002/003 stop reporting LLM token-usage counts
  (`tokens=total_tokens`, `prompt_tokens=...`) unless the name also carries a
  credential qualifier;
- the f-string form of L9-PYTHON-SQL-STRING-FORMAT is reported unless every
  interpolation is a proven string constant (UPPER_CASE names are not proof);
- AST-ROUTING-001/002 and AST-TRANSPORT-004 report dispatch to an L9
  node/peer/Gate destination or `/execute` route, including client instances,
  `url=` and put/patch/request/stream, instead of every requests/httpx post;
- AST-HANDLER-002 requires handler identity; AST-ROUTING-003 now matches
  `https://<host>.internal`.

Witness (rule, file and line, against `6d70e829`): the 26 SDK-rule false
positives that blocked `Enrichment.Inference.Engine` at `12d8b351` clear;
nothing else clears in Cognitive.Engine.Graphs, l9-ci-core, l9-ci-sdk or
l9-constellation-topology; two findings appear in
l9-constellation-topology `evidence/live-qualify-2026-08-02/run_gate_qualify.py`
(a raw dict posted straight to Gate `/execute`, already suppressed there for
AST-ROUTING-004 only). `python.lang.security.audit.insecure-file-permissions`
stays blocking: it becomes advisory only after the SDK's replacement rule
(L9-PYTHON-WRITABLE-FILE-MODE, l9-ci-sdk#109) is promoted and proven blocking.

`publish-analysis.yml`, `publish-sarif.yml` and `normalize-semgrep-report.yml`
keep their own pins; they are not part of required Organization CI.

Prove the pins with `python3 tools/advance_core_action_pins.py de4b5147f5ad97e9972ea584f078fcdad5f20a32 --check`.

## 21. Gate routing rules bind declared Constellation nodes only (2026-11-08) — amends §3 and §4

Organization CI ran the `l9-ci-sdk` Gate routing rules
(`l9_ci/rulesets/semgrep/python/l9-routing.yml`) as blocking on every governed
repository. Those rules enforce one obligation of the Quantum-L9/.github
ProductKind `node` (`semantics/product_kinds.yaml`, `l9.product-kinds/global@1`):
a node *forbids* `direct_peer_routing_when_gate_is_required` and *requires*
`gate_routed_inter_product_work`. A repository that is not a Constellation node
behind Constellation.Gate (Gate_SDK, Cursor-Governance, Core itself) carries no
such obligation, yet was failed by it.

- §3 is amended: `.l9/ci.json` may also carry `product_kind`, one of the
  admitted ProductKinds `node` or `dependency`. It is descriptive — a
  repository says what it is. An unadmitted kind (including the unresolved
  `sdk`), an empty string, or a non-string fails closed. Absent means not
  declared.
- §4 is amended: `resolve-governance` takes a `product-kind` input and emits
  `constellation-scope` (`node` | `non-node`). The Core-owned
  `.github/actions/resolve-governance/constellation-node-rules.json`
  (`l9.constellation-node-rules/v1`, beside `semgrep-policy.yaml`, so the
  exact-six `defaults/` rule holds) names the admitted kinds, the kind bound to
  Gate (`node`), the four routing rule IDs, and the `.github` commit whose
  ProductKind semantics it mirrors.
- A declared `node` receives `semgrep-policy.yaml` byte-for-byte: the routing
  rules block exactly as before. Every other repository receives the same
  policy with only those four rules classified `disabled` under policy key
  `routing.constellation-node-only`. They still run and are reported in the
  bundle; the SDK technical gate fails only on `blocking` findings. No other
  rule changes, and the scoped bytes are bound into `governance-digest`.
- A consumer still cannot select a mode: the mapping from kind to rules is
  Core's file, not the consumer's. Declaring `node` is the only way to receive
  the routing rules, so a Constellation node that has not declared it is not
  held to them — declaration is the node's obligation.
- Witness (`l9-ci-sdk` `d09d3963`, Core's `.l9/runtime/sdk/source` layout,
  `semgrep-policy.yaml` v3): Gate_SDK `main` produces 3 routing findings
  (`hardcoded-peer-node-url` ×2, `httpx-direct-node-dispatch` ×1); `gate
  evaluate` exits 1 under the node policy and 0 under the scoped policy.
- Tests: `tests/governance/test_constellation_scope.py` and
  `tests/governance/test_consumer_metadata.py`.

### Activation

This change is inert until the §16 pin advance, to its merge revision, which
also passes the declaration through in `org-ci.yml`:

```yaml
# resolve-governance step
product-kind: ${{ steps.consumer.outputs['product-kind'] }}
```

Order matters for nodes: once the pins advance, an undeclared node stops being
held to the routing rules. Constellation nodes (Enrichment.Inference.Engine,
Cognitive.Engine.Graphs, and every other repository behind
Constellation.Gate) add `"product_kind": "node"` to `.l9/ci.json` immediately
after the advance merges; before it, the field is an unknown key and fails
closed.

## 22. Constellation node scope activation (2026-11-08) — the §16 pin advance, applied

Two-step promotion per §16, never to an unmerged commit. Step 1
(`53d422d`, PR #237, §21) taught `resolve-consumer-metadata` to accept and
emit `product_kind` and `resolve-governance` to scope the Gate routing rules
to declared Constellation nodes. This step activates it:

- `org-ci.yml`'s 16 Core action pins advance to `53d422d`, an ancestor of
  `main`, and the `resolve-governance` step now passes
  `product-kind: ${{ steps.consumer.outputs['product-kind'] }}`;
- `analyze-semgrep.yml`'s `install-semgrep` pin moves with it, to `53d422d`,
  keeping one locked Semgrep install path.

Between `de4b514` and `53d422d` the only action changes are
`resolve-consumer-metadata` and `resolve-governance` (§21); `install-semgrep`,
`invoke-sdk` and `provision-sdk` are byte-identical, and the SDK revision
stays `d09d3963`. What this makes live for every governed repository's next
evaluation:

- `.l9/ci.json` may declare `"product_kind": "node"` or `"dependency"`; an
  unadmitted value fails the consumer-metadata step;
- a repository that declares `node` is held to the four `l9-routing.yml`
  rules exactly as before; every other repository receives them `disabled`
  (still run and reported, never blocking). Gate_SDK, Cursor-Governance and
  Core are not nodes and stop being failed by them.

Follow-up owed by every Constellation node behind Constellation.Gate
(Enrichment.Inference.Engine, Cognitive.Engine.Graphs, and the rest): declare
`"product_kind": "node"` in `.l9/ci.json`. Until a node does, it is not held to
the routing rules.

Prove the pins with `python3 tools/advance_core_action_pins.py 53d422d302f01484eac3202f89b90f34e2ec6ba8 --check`.

## 23. Semgrep policy v4 activation (2026-11-08) — the §16 pin advance, applied

`e69de8b` (PR #239) moved `resolve-governance/semgrep-policy.yaml` to version 4.
That version classifies the Semgrep audit rule `insecure-file-permissions` as
`advisory`. The rule flags `os.chmod` / `lchmod` / `fchmod` when the literal
mode satisfies `$BITS >= 0o650`. That is a comparison of size, not of
permission bits: owner-only `0o700` is reported as widely permissive while
`0o555` passes. It was the last blocking finding in Organization CI for
Quantum-L9/Enrichment.Inference.Engine (issue #287: `os.fchmod(stage_fd,
0o700)`, an owner-only staging directory). This step activates it:

- `org-ci.yml`'s 16 Core action pins advance to `e69de8b`, an ancestor of
  `main`;
- `analyze-semgrep.yml`'s `install-semgrep` pin moves with it, to `e69de8b`,
  keeping one locked Semgrep install path.

Between `53d422d` and `e69de8b` the only action change is
`resolve-governance/semgrep-policy.yaml`. Every other Core action is
byte-identical, and the SDK revision stays `d09d3963`. What this makes live
for every governed repository's next evaluation:

- `insecure-file-permissions` findings are still reported but no longer block.
  This includes the rule's bit-based stat-constant branches (`S_IWOTH`,
  `S_IRWXO`, …), which are reviewed per finding like the other advisory
  classifications. The blocking default is unchanged.

Prove the pins with `python3 tools/advance_core_action_pins.py e69de8b4bb4a1d0a732596ac968c22aadc483947 --check`.

## 24. Core-owned consumer files are stamped, and the live registry raises required mode (2026-11-13) — amends §3, §11/§14 tooling, §16 and §17

Decision record for campaign L9-NODE-PIPELINE-01 lane P1. Before this section
every Core-owned file in a newly born repository was hand-made: the V2
contract, the `make-v1` `Makefile`, the Biome trio, and `requirements-ci.txt`
copied from `toolchain-lock.json`. Core had no Python tooling preset, and a
repository became `required` only through a Core PR re-projecting §17 plus a
§16 pin advance.

### One stamp, one proof

`tools.l9_repo` gains `stamp`; `validate` gains the byte-identity proof. No
second tool.

```bash
python3 -m tools.l9_repo --workspace <consumer> stamp --profile <python|typescript|python+typescript>
python3 -m tools.l9_repo --workspace <consumer> validate
```

- Core-owned consumer files (closed set, `tools/l9_repo/stamp.py`): every
  profile writes `.l9/repo-workflow.json` and `Makefile`; `python` adds
  `requirements-ci.txt` (rendered from `toolchain-lock.json`; same pins and
  order as `install-consumer-ci/requirements-consumer-ci.txt`), `ruff.toml`,
  `mypy.ini`, `pytest.ini` from `presets/python-tooling/`; `typescript` adds
  `biome.json`, `.biomeignore`, `.editorconfig` from `presets/typescript/`;
  `python+typescript` is the union. Product-owned files (`Repo.mk`,
  `pyproject.toml`, `package.json`, source) are never written. The Python
  preset is standalone files because each tool reads them ahead of
  `pyproject.toml`; Core never edits a consumer's `pyproject.toml`.
- The directory is `presets/python-tooling/`, not `presets/python/`: the §15
  tripwires keep the retired copy-first `presets/python` tree from returning,
  and this tree holds configuration only, never a workflow.
- The stamp record is `.l9/core-stamp.json`, schema `l9.core-stamp/v1`:
  exactly `schema`, `core` (`repository: Quantum-L9/l9-ci-core`, 40-hex
  `revision`), `profile`, and `files` (each profile path → `sha256:<hex>`).
  `stamp` records the Core checkout's `HEAD` and refuses to run when any stamp
  source (`presets/`, `tools/l9_repo/`, `toolchain-lock.json`) differs from
  `HEAD`. It is idempotent; it overwrites a drifted Core-owned file.
- `validate` re-renders each recorded file from the Core checkout it runs
  from and fails on a differing byte, a missing file, or a record digest that
  does not match. A record from another Core revision is not itself drift:
  equal bytes are the proof, the revision is the attribution. A consumer
  without a record is validated exactly as before.
- Python preset policy is derived, not invented: `l9-repo-template` (the
  birth template, a required consumer) supplies Ruff line-length 100 and
  `E F B I UP` / ignore `E501`, a superset of Core's own `E4 E7 E9 F`; the
  interpreter target follows `toolchain-lock.json` (`py312` / mypy 3.12); mypy
  is `strict = True`, the L9 standard the template already runs. Core's own
  `ruff.toml` and `[tool.mypy]` are unchanged.

### Required mode without a hand edit

`run-repository-verification` resolves the mode as: the static projection
(§17) says `required` → `required`, with no network read; otherwise the action
reads `semantics/repository_registry.yaml` from `Quantum-L9/.github`
`refs/heads/main` over Git, and a repository the registry names with
`lifecycle: current` is `required`; otherwise `default_mode`.

- The key is the existing §17 rule, `lifecycle == current`. The registry has
  no birth/factory field and explicitly does not own `repository_birth_profile`
  (`does_not_own`); membership, not birth, is the obligation. A born node is
  `required` the moment its registry entry lands on `.github` `main`.
- The registry can only raise the mode; nothing in the consumer and no action
  input selects or lowers it. Matching is case-insensitive, like the static
  list.
- Fail closed: an unreachable, unparseable, or non-canonical registry fails the
  run as `infrastructure` (`contract-mode: unresolved`). The reader is standard
  library only, validates registry identity and every entry's `id`,
  `class_ref`, `lifecycle` and GitHub coordinate, and skips additional entry
  keys so an additive registry field does not break the fleet.
- The static projection and its receipt are kept: they are the audit trail and
  the no-network fast path. Re-projection remains the way to record a member
  in Core; it is no longer the way to make one required.

### Stamp drift mode

For a V2 consumer carrying `.l9/core-stamp.json`, after the contract, facade
and `Repo.mk` admission and before any phase runs:

| Mode | Stamp aligned | Stamp drift | Malformed record |
|---|---|---|---|
| `required` | pass | `fail`, `failure-kind: contract`, no phase runs | `fail`, `contract` |
| `migration` | pass | `::warning::` annotation per file; phases run | `fail`, `contract` |

`check-toolchain-alignment` stays advisory (§16); the blocking stamp check
lives where the mode is known.

### Activation

Inert until the §16 pin advance moves `org-ci.yml`'s
`run-repository-verification` pin to this change's merge revision. Stamp from
the revision `org-ci.yml` pins: organization CI compares bytes with that
revision. Tests: `tests/repo_execution/test_stamp.py`,
`tests/workflows/test_registry_required_mode.py`.

## 25. The stamp covers every CI-tool config, and stamp drift blocks in every mode (2026-11-14) — amends §24

Decision record for lane C1. §24 stamped Ruff, mypy, pytest, Biome,
`.editorconfig` and `requirements-ci.txt`. However, `toolchain-lock.json` also
pins the interpreter and pre-commit, and §24 let stamp drift through as a
warning in `migration` mode.

### Stamped files per profile

| Profile | Core-owned files |
|---|---|
| `python` | `.l9/repo-workflow.json`, `Makefile`, `requirements-ci.txt`, `.python-version`, `.pre-commit-config.yaml`, `ruff.toml`, `mypy.ini`, `pytest.ini` |
| `typescript` | `.l9/repo-workflow.json`, `Makefile`, `biome.json`, `.biomeignore`, `.editorconfig` |
| `python+typescript` | the union |

- `.python-version` is the lock's exact `python` (`3.12.14`).
- `.pre-commit-config.yaml` is rendered from the lock. It contains only
  `repo: local` hooks (`ruff check --fix`, `ruff format`, `mypy`, each
  `python -m <tool>`) with `language: unsupported`, pre-commit 4.4.0's name for
  `system`. `minimum_pre_commit_version` is the lock's `pre-commit`. No hook
  carries a `rev`, so the only tool versions are the ones `requirements-ci.txt`
  installs. The TypeScript profile has no pre-commit file because pre-commit is
  not part of its toolchain. Biome is checked centrally by `run-biome-check`.
- Semgrep is not stamped. Its rulesets belong to `l9-ci-sdk`, and their modes
  are `resolve-governance` data. Both run centrally in `org-ci.yml`, so a
  consumer Semgrep file would be a second source (L9-OWNER-001).
- Pyright is not stamped. The Python profile type-checks with strict mypy, and
  the lock pins no Pyright. Adding Pyright is a §12 pin-file change, not a
  stamp change.

### Drift semantics

`tools/l9_repo` `validate` and the organization admission bridge
(`run-repository-verification`) both fail on any stamp finding. That covers a
differing byte, a missing file, a record digest mismatch, and a profile file
absent from the record. They fail in every enforcement mode. Supersedes the
§24 table's `migration` warning row. A stamp record is the repository's own
claim that those files are Core's, so the claim is held to. A record that names
a file outside its profile, or no file at all, is malformed. A record from an
earlier Core revision that lacks a newly owned file is drift, not malformed:
re-stamp.

**Fleet safety:** a repository with no `.l9/core-stamp.json` is never
stamp-checked. Existing unstamped repositories see no change.

Re-stamp from the Core revision `org-ci.yml` pins:

```bash
python3 -m tools.l9_repo --workspace <consumer> stamp --profile <python|typescript|python+typescript>
python3 -m tools.l9_repo --workspace <consumer> validate
```

### Repository-specific settings

Not provided yet. A stamped file is the repository's whole policy for that tool,
so a product conforms to it. Examples are pytest `asyncio_mode` and extra
markers. A Core-owned overlay would change `l9.core-stamp/v1` and needs its own
decision; it is not built here.

### Activation

This change is inert until the §16 pin advance. The pin must be this change's
merge revision, never an unmerged commit. The `run-repository-verification`
pin at `e69de8b` predates §24, and pinning to `110f173` would reject an
expanded record as malformed. Run:

```bash
python3 tools/advance_core_action_pins.py <merge sha of this change>
python3 tools/advance_core_action_pins.py <merge sha> --check
```

Tests: `tests/repo_execution/test_stamp.py`,
`tests/workflows/test_registry_required_mode.py`.

## 26. Core owns every CI tool: lock, stamp, and the tool-ownership manifest (2026-11-14) — amends §5, §12, §24 and §25

Decision record for lane C2. Operator decision: `l9-ci-core` owns every CI
tool a repository uses, both the version (`toolchain-lock.json`) and the
config content (stamp profiles). A tool is locked only when Core runs it in a
workflow step or a stamped profile configures and runs it; a tool nothing
runs is not added (L9-UNKNOWN-001: absent evidence is not ownership).

### Per-tool decisions

| Tool | Decision | Version | Core-owned config | Profiles | Where Core runs it |
|---|---|---|---|---|---|
| gitleaks | **locked** (was an unbound literal) | 8.24.3 | `.gitleaks.toml` (`[extend] useDefault = true`) | all | `security.yml` "Secret scan (gitleaks)" |
| bandit | **locked** (was an unbound literal) | 1.8.6 | — | — | `security.yml` "Python static security scan (bandit)" |
| pip-audit | **locked** (was an unbound literal) | 2.9.0 | — | — | `security.yml` "Python dependency audit (pip-audit)" |
| pyright | **not locked, never a gate** (CTO decision) | — | — | — | nowhere; strict mypy is the single type gate |
| uv | **locked** (CTO decision, 2026-11-15) | 0.13.8 | `uv.toml` (`required-version = "==<lock>"`) | python, python+typescript | installed by `install-consumer-ci` and the stamped `requirements-ci.txt` |
| node | not locked | — | — | — | Core runs no Node program; Biome is a standalone binary |
| eslint (+ typescript-eslint) | not locked: **Biome alone owns JS/TS lint and format** | — | — | — | `org-ci.yml` runs Biome only |
| prettier | not locked | — | — | — | Core runs no Markdown formatter |
| typescript (tsc), tsconfig | not locked; product-owned | — | — | — | Core runs no tsc |
| vitest | not locked; product-owned | — | — | — | Core runs no TS test runner |
| actionlint, markdownlint, yamllint | not locked | — | — | — | no Core workflow or stamped hook runs them |

The existing tools (ruff, mypy, pytest, pip, pre-commit, python, biome,
semgrep) are unchanged. The full joined table is
`.github/actions/install-consumer-ci/tool-ownership.json`.

- The `security.yml` literals for gitleaks, bandit and pip-audit stay where
  GitHub needs them and are now test-bound copies of the lock
  (`tests/workflows/test_workflow_toolchain_pins.py`). The install-consumer-ci
  pin file does not gain them: semgrep, bandit and pip-audit are each
  installed by exactly one other Core surface
  (`tests/actions/test_install_consumer_ci.py` `NOT_INSTALLER_TOOLS`).
- `check-toolchain-alignment` now also reports a consumer's own bandit,
  pip-audit and gitleaks pins against the lock (advisory, §16).
- **ESLint migration note** (`l9-meta-injector` ADR-095, Proposed): Biome
  already blocks there through `org-ci.yml`. ESLint is not a Core-owned tool;
  it remains a product-local check under the repository's own `Repo.mk`, and
  the organization standard for JS/TS lint is Biome. Retiring the injector's
  ESLint is that repository's decision; Core stamps no `eslint.config.js`.

### Type checker: strict mypy is the single organization CI type gate

CTO decision (2026-11-15, operator decision): strict mypy is the only CI type
gate in the organization. Core keeps locking mypy and stamping strict
`mypy.ini` for the `python` and `python+typescript` profiles, byte-identical
to §25. Pyright is a gate nowhere: Core does not lock it, stamps no
`pyrightconfig.json`, and org CI never runs it. A repository may still use
Pyright as optional editor tooling outside Core. Evidence: every governed
Python repository read (l9-repo-template, Gate_SDK, l9-constellation-topology,
l9-ci-sdk) runs mypy. Consumer follow-ups, owned by those repositories:

- `l9-conformance` migrates from Pyright strict to the stamped strict mypy
  gate;
- `Constellation.Gate` adopts the stamped mypy gate (`stamp --profile python`).

### uv is Core-owned (CTO decision, 2026-11-15)

uv drives every Python install and lock check in the fleet: l9-repo-template's
`repo-setup` runs `uv sync` whenever uv is present, so repository verification
otherwise depends on whichever uv the runner image carries. Core therefore
locks uv (0.13.8), installs it with the other consumer CI pins
(`install-consumer-ci/requirements-consumer-ci.txt` and the stamped
`requirements-ci.txt`), and stamps `uv.toml` with
`required-version = "==<lock>"` in the Python profiles, so any other uv
refuses to run. `check-toolchain-alignment` reports a consumer's own `uv==`
pins against the lock. A `uv.toml` takes precedence over `[tool.uv]` in
`pyproject.toml`, which uv then ignores; none of the governed repositories
read carries a `[tool.uv]` table. A product that needs uv settings conforms to
the stamped file (no overlay, below).

### No per-repository overlay (CTO decision, 2026-11-15)

There is no repository-specific extension point for stamped files. Products
conform to them. This confirms §25 "Repository-specific settings"; a future
overlay would change `l9.core-stamp/v1` and needs its own decision.

### `.gitleaks.toml` in every profile

Gitleaks' built-in rules for the locked version, nothing else. A reviewed false
positive goes in the repository's own `.gitleaksignore` (fingerprints, read by
gitleaks from the repository root), never in the stamped file. There is still
no general per-repository overlay (§25).

### The tool-ownership manifest (`l9.tool-ownership/v1`)

One machine-readable view of Core tool ownership, derived (L9-DERIVED-001)
from `toolchain-lock.json` (versions), `core-owned-tools.json` (identity),
`tools/l9_repo/stamp.py` (profiles and files) and
`tools/l9_repo/tool_ownership.py` (where each tool runs, the not-owned
decisions). It records each input's sha256 (L9-PROV-001). Per tool: version,
config paths, profiles, the profiles whose stamped pre-commit runs it, and the
Core workflow steps that run it; per language: formatter, linter and type
checker (mypy for Python); plus `unowned_languages` (`jsonc`,
`markdown`) and `not_owned`.

```bash
python3 tools/render_tool_ownership.py          # regenerate after any input change
python3 tools/render_tool_ownership.py --check  # also asserted by tests/tools/test_tool_ownership.py
```

The tests prove one entry per lock key, one owning tool per stamped file,
every named workflow step exists, and every owned tool is run or stamped.

**Consumer change owed by Cursor-Governance** (not made here):
`environment/ide/policy.json` declares formatter ownership a second time.
Generate its `governance`-authority rows from this manifest's `languages`
(ruff for python; biome for javascript, javascriptreact, typescript,
typescriptreact, json) at the Core revision `org-ci.yml` pins, and keep only
IDE-local choices (`jsonc`, `markdown`, which Core owns no tool for) declared
there.

### Fleet safety

No repository carries `.l9/core-stamp.json` on its default branch (code search,
2026-11-14), so the new `.gitleaks.toml` stamp file turns nobody red. A
repository stamped from the §25 revision is drift until it re-stamps (§25
semantics). Unstamped repositories are untouched. Activation is the §16 pin
advance, to this change's merge revision.

Tests: `tests/repo_execution/test_stamp.py`, `tests/tools/test_tool_ownership.py`,
`tests/workflows/test_workflow_toolchain_pins.py`,
`tests/actions/test_install_consumer_ci.py`,
`tests/actions/test_check_toolchain_alignment.py`.

## 27. Private SDK and registry reads (2026-11-17) — amends §5, §8 and §17

`Quantum-L9/l9-ci-sdk` and `Quantum-L9/.github` went private on 2026-11-16.
From then on `provision-sdk` failed every governed repository's
`Analyze (central Core)` with `could not read Username` (an anonymous fetch of
the SDK), and the §17 live registry read would fail the same way.

- `provision-sdk` and `run-repository-verification` take a
  `github-read-token` input (default empty: anonymous, as before). The value
  is the organization secret `L9_GITHUB_READ_TOKEN` (fine-grained, read-only:
  Contents + Metadata on the organization's repositories).
- The token reaches git only as a per-command `http.https://github.com/.extraheader`
  through `GIT_CONFIG_COUNT/KEY/VALUE` environment: never argv, a URL or a
  file. `provision-sdk` pops it at import so no child process inherits it.
- `run-repository-verification` runs the consumer's `make` targets, so its
  verify step must never hold the token. A separate `registry` step, which runs
  no consumer code, reads the registry first and writes a run-scoped snapshot
  under `$RUNNER_TEMP`; the verify step reads only that snapshot and, without
  one, falls back to the anonymous live read and fails closed as before.
- Suite: `tests/provisioning/test_private_repository_auth.py`.

Activation is the §16 pin advance: after this merges, one PR advances
`org-ci.yml`'s Core action pins to the merge revision and passes
`github-read-token: ${{ secrets.L9_GITHUB_READ_TOKEN }}` to both steps. The
secret must be visible to every governed repository, public ones included
(organization secret repository access: all repositories).

## 28. Private-read activation (2026-11-17) — the §16 pin advance, applied

`org-ci.yml`'s 16 Core action pins advance to `44d85a6` (the merge of §27,
Quantum-L9/l9-ci-core#259, an ancestor of `main`), and both private reads
receive `github-read-token: ${{ secrets.L9_GITHUB_READ_TOKEN }}`:
`provision-sdk` and `run-repository-verification`. Core's own self-CI gets the
same token: `sdk-contract-check.yml` (local action), and `analyze-semgrep.yml`,
whose `provision-sdk` and `install-semgrep` pins advance to `44d85a6` with it
and which now declares the optional `L9_GITHUB_READ_TOKEN` secret that
`self-analysis.yml` passes. The pin constants in
`tests/workflows/test_org_repository_verification.py` and
`tests/workflows/test_workflow_toolchain_pins.py` move with them.

`L9_GITHUB_READ_TOKEN` must be visible to every governed repository, this
public one included. Prove the pins with
`python3 tools/advance_core_action_pins.py 44d85a66149b0d22d1b298af9b0ee14f1ee24473 --check`.

## 29. Core's own pull requests run Core's own contract (2026-11-17) — amends §12 and §14

Three defects, found at `9bd8863`:

- **A.** `self-ci.yml`'s `Unit tests` job set up no Python and installed
  nothing: the suite ran on the runner image's system `python3`, so the cases
  needing PyYAML, jsonschema or the pinned ruff/mypy depended on whatever the
  image shipped, and the `REQUIRES_REAL_TOOLCHAIN` cases in
  `tests/tools/test_check_toolchain_versions.py` skipped there on the false
  premise that `make check` runs that module (it runs
  `tools/check_toolchain_versions.py`, not the tests). Those cases ran nowhere
  in CI.
- **B.** No self-CI job ran Core's own repository contract
  (`.l9/repo-workflow.json`, make-v1: `make setup → validate → check → test`)
  at the pull request's revision; `make validate` was reached only through
  org-ci's pinned `run-repository-verification`.
- **C.** `docs/release/rulesets/core-main-protection.json` required the bare
  context `validate`, which both `governance-ci.yml` and
  `release-validation.yml` produce, and required no contract, analysis or
  security check.

Fix:

- `Unit tests` uses the lint job's SHA-pinned `actions/setup-python`
  (Python 3.12), installs `requirements-ci.txt` and
  `requirements-repo-runtime.txt`, and runs the same discovery with that
  interpreter. The real-toolchain cases now execute on every Core pull request.
  Their skip guard stays, restated to its one real condition: Phase 4 release
  validation (`.github/actions/validate-release/validate_release.py`) runs the
  same suite on the runner's bare `python3`, which has no ruff or mypy.
- New `self-ci.yml` job `Repository contract` checks out the event revision,
  sets up the same Python and runs `make setup`, `make validate`, `make check`,
  `make test` as separate steps, stopping at the first failure. It adds nothing
  to the Makefile; it uses the `Repo.mk` `repo-*` leaves.
- `governance-ci.yml`'s job is named `Governance validate`.
- The ruleset payload requires `Lint and Type Check`, `Unit tests`,
  `Repository contract`, `Governance validate`, `sdk-contract`,
  `Analyze (semgrep) / Analyze (semgrep -> SDK)` and `Security scan / security`.
  The last two are reusable-workflow callers, so their check runs carry the
  `<caller> / <called>` name GitHub actually emits. Applying the payload is an
  organization-admin action this change does not perform
  (`docs/release/rulesets/README.md`).
- Suite: `tests/workflows/test_self_ci_gating.py`.

Out of scope here, a follow-up: Core pull requests are still evaluated by
pinned older actions — `analyze-semgrep.yml` pins `resolve-governance`,
`detect-language` and `invoke-sdk` to `a0c7178` rather than the revision
under review.

## 30. Proposed CI control-plane decisions live in `docs/adr/` (2026-11-20)

Decision record for where two proposed decisions are kept. It decides nothing
else and repeats none of their text.

- `docs/adr/0001-top-down-ci-control-and-runtime-inheritance.md` and
  `docs/adr/0002-governed-promotion-native-provider-boundaries-and-cost.md`
  are **Proposed**. `docs/adr/README.md` is their index and carries the status.
  Neither is in force until its owner admits it; until then §1 to §29 are
  unchanged.
- Their scoped statements are `.l9/governance-contract.yaml`
  `ci_runtime_invariants`, `L9-CI-CORE-001` to `006`. A statement there is a
  proposal, not enforcement.
- Witnesses: `tests/governance/test_ci_runtime_invariants.py`, one known-good
  and one known-bad per ID, over `tools/ci_runtime_invariants.py`. The `002`
  and `003` witnesses fail closed on a named missing capability. The `004` and
  `006` witnesses prove gate logic only; no live canary, ruleset read or hosted
  run is claimed.
- This file stays the one decision trail. A later decision that realizes or
  admits either record is a new dated section here that points at it.

## 31. Required instances are aggregated by one action; activation is a pin advance (2026-11-20)

Decision record for the first realization step of the proposed
`docs/adr/0001-top-down-ci-control-and-runtime-inheritance.md` decision 4
(`L9-CI-CORE-003`). The ADR stays Proposed; this section records what exists.

- `.github/actions/aggregate-required-instances/` takes Core's plan of the
  provider/language instances for one evaluated revision
  (`l9.core-required-instances/v1`) and one Core record per executed instance
  (`l9.core-instance-result/v1`), and emits one aggregate
  (`l9.core-instance-aggregate/v1`): `fail` if a required instance failed,
  else `incomplete` if any required instance is missing, skipped, timed out,
  cancelled, duplicated, unreadable or bound to another repository, revision
  or SDK revision, else `pass`. An empty plan passes only with a stated reason
  for non-applicability. Exit codes follow the SDK gate (0, 1, 6).
- An instance is `provider:target`, with the target in the SDK inventory's
  grammar (`language:<name>` or `framework:<name>`). An instance whose SDK
  prerequisite is not `applicable`, and a plan marked `unresolved`, can never
  aggregate to `pass`. An instance the plan marks optional never gates.
- `.github/actions/plan-required-instances/` builds that plan from the SDK's
  `l9.capability-inventory/v1` record. The record carries no requiredness;
  Core adds it. Every `applicable` prerequisite becomes an instance, so no
  language is chosen over another. An instance is required when central
  governance resolved its provider as required and Core executes the target
  (`executable-targets.json`: today `language:python` and
  `language:typescript`, the identity maps Core ships). An applicable target
  Core cannot execute is planned and visible, and not required. An inventory
  that is `failed`, `incomplete` or bound to another revision makes the plan
  `unresolved`. Unsupported languages are listed, not planned.
- Both actions read only the inventory and Core's own records. They open no
  SDK bundle, finding or gate result and derive no verdict: an instance's
  `pass` or `fail` is the status the SDK technical gate returned (§3,
  `.l9/governance-contract.yaml` `prohibited`).
- Proof: `tests/actions/test_plan_required_instances.py` over
  `tests/fixtures/capability-inventory/` (four records the SDK inventory
  command printed, held as contract fixtures; Core's SDK pin is unchanged and
  no Core job runs an unmerged SDK),
  `tests/actions/test_aggregate_required_instances.py` over
  `tests/fixtures/required-instances/mixed/` (Python plus TypeScript), and
  three steps in `sdk-contract-check.yml` that run both languages through the
  provisioned SDK, aggregate them with the action at the event revision, then
  aggregate the same records with one instance removed and require
  `incomplete`.
- **Not active.** `org-ci.yml` still resolves one language
  (`detect-language`) and does not call this action. Calling it is a pin
  advance after this change is on `main` (§16), and it needs an SDK pin that
  carries `l9-ci providers inventory`. Until then
  `tests/governance/test_ci_runtime_invariants.py` keeps the
  `required-instance-scheduling` witness failing closed.

## 32. A stamp record may inherit tool configuration from Core at runtime (2026-11-20) — amends §24 and §25

Decision record for the first realization step of the proposed
`docs/adr/0001-top-down-ci-control-and-runtime-inheritance.md` decision 3
(`L9-CI-CORE-002`). The ADR stays Proposed. §25 "Repository-specific
settings" said an overlay would change `l9.core-stamp/v1` and needed its own
decision; this is that decision for inheritance only. It adds no
repository-specific setting.

- **Record.** `l9.core-stamp/v2` is v1 plus `inherited`: a non-empty list of
  the profile's paths the repository does not carry. `files` holds digests
  for the carried paths only. The key set is exact per schema. A v1 record is
  read exactly as before, and `stamp` without `--inherit` still writes v1.
- **What may be inherited.** Every Core-owned file of the profile except
  `.l9/repo-workflow.json` and `Makefile`, which are what make a repository
  verifiable, and `biome.json`, `.biomeignore` and `.editorconfig`.
  `org-ci.yml`'s blocking Biome check reads those three from the checkout
  before the bridge runs and treats a repository without `biome.json` as not
  applicable, so inheriting them would switch off the only JS/TS gate. They
  become inheritable when that check resolves its configuration from Core,
  which is an `org-ci.yml` change. Today that leaves the Python tool
  configuration and `.gitleaks.toml`.
- **Resolution.** `tools/l9_repo/inherited_config.py` `resolve` returns the
  bytes this Core revision renders for the inherited paths, one sha256 per
  file and one configuration digest over the set. Nothing in the consumer
  selects content.
- **Drift.** An inherited path that is absent is aligned, whatever Core's
  bytes are, so a Core preset change no longer fails a repository that
  inherits it. A profile path that is neither carried nor inherited is still
  drift, and a carried path is still byte-checked (§25).
- **Override.** A local file at an inherited path is accepted only when it is
  Core's exact bytes. Any other content is a finding that names the central
  and the local digest, in every mode. The same applies to a symlink.
  A consumer cannot change version, mode or ignore scope this way.
- **Bridge.** `run-repository-verification` prints the resolved configuration
  digest after admission and writes the absent inherited files into the
  workspace before the first phase. `tools.l9_repo` gains `materialize` for
  the same step on a developer machine.
- **Adoption.** A repository adopts inheritance by re-stamping with
  `--inherit`. `stamp` then removes the Core-owned copy at each inherited path
  (this revision's bytes, or the bytes the previous record stamped), because a
  tracked copy left behind would become an override at the next Core change.
  It refuses to convert a path whose content is anything else. It deletes
  nothing outside the inherited paths.
- **Not decided.** No repository is migrated to v2 here and no stamped file
  is retired. That needs the canary the ADR names.

Activation: inert until the §16 pin advance moves `run-repository-verification`
to this change's merge revision; a v2 record evaluated by an older pin is
rejected as malformed, so no repository should carry one before that.

Tests: `tests/repo_execution/test_inherited_config.py`,
`tests/governance/test_ci_runtime_invariants.py` (`L9-CI-CORE-002`).

## 33. Nightly deep runs are change-gated; apply is closed by default (2026-11-20)

Operator requirement: expensive checks run once a night, only for
repositories that changed since their last successful nightly, never on
every pull request and never on every repository every night.

- **Plan.** `org-nightly-dispatch.yml` plans from two committed inputs and no
  network: the registry projection
  (`.github/actions/run-repository-verification/required-repositories.receipt.json`,
  digest checked) and the admitted deep entry points
  (`.github/nightly/deep-entrypoints.json`). While apply is closed, a dispatch
  entry point is `SKIPPED_APPLY_CLOSED`, not unknown. Only an unreadable input
  alerts.
- **Core's own suite.** `nightly-deep.yml` skips a scheduled run, recording
  the reason, when main's HEAD already has a successful scheduled run.
  Unreadable history runs the suite.
- **Apply.** `tools/nightly_apply.py` dispatches ELIGIBLE entry points through
  `workflow_dispatch`, and only when `L9_NIGHTLY_APPLY=open`. No workflow sets
  that switch or holds its credential. Opening it is an operator decision.
  Apply dispatches; it never merges.
- **Consumers.** The contract each consumer exposes, and the schedules it
  replaces, are in `docs/nightly-deep-consumer-contract.md`. No consumer
  schedule is retired before CURSOR_CANARY_PASS.
- **PR expense.** No Core pull-request check is heavy enough to move
  (`docs/reports/L9-CORE-PR-CHECK-EXPENSE-2026-11-20.md`).

Tests: `tests/workflows/test_org_nightly_dispatch.py`,
`tests/workflows/test_nightly_apply.py`.

## 34. Core self-pins name commits `main` contains; SDK `dc783c64` promoted (2026-11-20) — amends §16

Operator decision, 2026-11-20 (recorded by the CTO for lane L2f): Core may
re-pin `org-ci.yml`, wire the required job through the instance planner and
aggregator, and advance the SDK pin.

- **Orphaned pins.** A squash merge orphans the commits of the branch it
  merged. `org-ci.yml` pinned `run-repository-verification@043f50a`, a #280
  branch commit that `main` never contained, and `publish-analysis.yml` and
  `publish-sarif.yml` pinned `59cb364`. Every `org-ci.yml` Core pin now names
  `10464765` (`main`, after #286, #287, #290 and #291); the publish workflows
  name `75c74f16`, whose action bytes are identical to `59cb364`.
- **Guard.** `tools/check_core_pin_ancestry.py` fails when a Core self-pin in a
  required-path workflow (`org-ci.yml`, `analyze-semgrep.yml`,
  `publish-analysis.yml`, `publish-sarif.yml`, `normalize-semgrep-report.yml`)
  is not an ancestor of `main`, or names a commit the clone does not have.
  self-ci.yml's Repository contract job fetches `main`'s commit graph
  (`--filter=tree:0`) and runs it. §16's rule "never advance to a revision
  that is not an ancestor of `main`" is now checked, not only stated.
- **SDK step one.** `l9-ci-sdk` `dc783c64` (SDK #122–#127) is the default
  compatible revision, with `providers inventory` in its required CLI paths;
  `9fd4efe7` is the tested rollback. The runtime lock changes only its header.
  `sdk-contract-check.yml` now inventories a real mixed Python and TypeScript
  snapshot with that SDK and plans it (L9-CI-CORE-003 across owners).
- **Not yet live.** `org-ci.yml`'s `sdk-revision` fallback stays `d09d3963`:
  `provision-sdk` reads the compatibility manifest from its own pinned
  revision, and `10464765` does not list `dc783c64`. Step two, after this
  merges, pins `org-ci.yml` to this change's merge revision, moves the
  fallback to `dc783c64`, and wires `Analyze (central Core)` through
  `plan-required-instances` → per-instance execution →
  `aggregate-required-instances`, which needs `providers inventory`.

Tests: `tests/tools/test_check_core_pin_ancestry.py`,
`tests/provisioning/test_compatibility_manifest.py`,
`tests/workflows/test_org_repository_verification.py`.

## 35. SDK `dc783c64` activation and the required-instance verdict (2026-11-20) — the §16 pin advance, applied; step two of §34

- `org-ci.yml`'s Core action pins advance to `8554ce96` (#292's merge, an
  ancestor of `main`), and its `sdk-revision` fallback moves from `d09d3963`
  to `dc783c64`. Between `d09d3963` and `dc783c64` the fleet gains
  `9fd4efe7`'s L9-PYTHON-WRITABLE-FILE-MODE rule (group or other write
  permission on chmod) and the `providers inventory` command; `semgrep run`
  otherwise scans the same rules.
- `Analyze (central Core)` keeps its name and becomes the L9-CI-CORE-003
  verdict. The SDK inventories the snapshot before repository verification
  can touch the tree; `plan-required-instances` plans every applicable
  provider/language instance (requiredness from central governance's
  `required-provider`); the detected language reuses its full pipeline's gate
  status and every other planned language runs `semgrep run` → `bundle
  validate` → `gate evaluate` with the same policy, identity map and
  strictness; `aggregate-required-instances` turns the records into one
  status. The blocking-mode enforcement reads that aggregate: a required
  instance that is missing, skipped or unfinished is `incomplete` (exit 6),
  never pass, and no aggregate at all is `incomplete`.
- An SDK execution error in a second language (`semgrep run` or `bundle
  validate` failing, or `gate evaluate` exiting outside 0/1/6) fails the job in
  every mode, as the detected language's own steps do; it is never folded into
  an advisory aggregate.
- Only the detected language's bundle is published (artifact set, SARIF,
  agent payload); a second language contributes its verdict, not artifacts.

Tests: `tests/workflows/test_org_ci_required_instances.py` (the embedded
runner against a fake SDK, with Core's real planner and aggregator).

## 36. One reusable Sonar step reuses the consumer's coverage (2026-11-20)

CI_CONFIG_FINDINGS row 6 (Cursor-Governance campaign evidence): coverage reached
Sonar in two of six campaign repositories, and each repository carried its own
`sonarcloud.yml`. Core offers the #289 pattern as one step instead.

- `.github/actions/run-sonar/` is a composite action a consumer workflow calls
  after its test run and checkout (`fetch-depth: 0`), passing the organization
  `SONAR_TOKEN` as `sonar-token`. It runs no second test: it imports the
  Cobertura `coverage.xml` that run wrote (`coverage-reports`, default
  `coverage.xml`, relative to `project-base-dir`).
- It fails closed before scanning when the token is blank, or a named report is
  missing, empty, not well-formed XML, not Cobertura (`<coverage>` root), or
  outside the workspace (`check_coverage.py`). A scan that would read 0% because
  coverage never arrived does not run.
- `SonarSource/sonarqube-scan-action@59202af334b4ba7b5db01b701b8e79f8b3bef0f4`
  (v8.5.0) with `scannerVersion: 8.4.0.16527`, the pins of `self-sonar.yml`;
  `tests/actions/test_run_sonar.py` binds the two.
- `quality-gate: advisory` (default) uploads without waiting; `blocking` waits up
  to 300 s and fails the step on a failed gate. The caller decides, and skips the
  step on fork pull requests, which carry no token.
- Not a required check and not an organization gate: nothing in `org-ci.yml`
  calls it. Consumers reach it by full-SHA pin once it is on `main` (§8); moving
  EIE and CEG onto it is their canary agents' work.

Tests: `tests/actions/test_run_sonar.py`.

## 37. `security.yml` scans a consumer on what it changes, with the Core rules (2026-11-20) — step one of the org-ci security wiring

`security.yml` (gitleaks required, pip-audit required for a Python project,
bandit advisory) is about to run for every governed repository through
`org-ci.yml`. Step one (this section) makes it correct for a consumer. Step two
pins it from `org-ci.yml` at this change's merge commit (§16, §34).

- **Core rules plus the consumer's.** The Core preset
  (`presets/security/.gitleaks.toml`) is gitleaks' default rule set. A
  repository's `.gitleaks.toml` is honoured on top of it. A file with no
  `[extend]` gains `useDefault = true` in a runtime copy, so its own rules and
  allowlists apply and the defaults are kept. An `[extend]` naming a `path`, or
  one not setting `useDefault = true`, is refused (no outcome, so
  `not_executed`), never scanned with fewer rules. Reviewed false positives
  go in `.gitleaksignore`.
- **Scope.** `gitleaks-scope: event` scans what the event adds: base..head
  for a pull request or merge group, before..after for a push (the pushed
  commit alone for a new branch), and full history otherwise.
  `gitleaks-log-opts` sets a range explicitly. A range that does not resolve
  in the clone is refused before gitleaks runs, because gitleaks exits 1 for a
  git error as it does for a finding.
- **pip-audit audits the project.** `requirements.txt` when present, else the
  `pyproject.toml` project by path; the runner environment only when neither
  exists. Before this, `pip-audit --desc` audited the runner's own toolchain
  and passed without reading the project.

Tests: `tests/workflows/test_security_consumer_scope.py` (config layering
executed, the scan invocation with a stub gitleaks, event ranges, pip-audit
target selection). Layering was also checked with gitleaks 8.24.3 itself: a
consumer config alone reports only its own rule; the layered copy reports it
and gitleaks' default `github-pat`.

## 38. SDK `fbe14fec` activation (2026-11-20) — the §16 pin advance, applied; P0 step two

Step one (#307) admitted l9-ci-sdk `fbe14fec` (#133 on `9af1a202`, #305): a
required central Semgrep run carries a declared analysis budget, 30 s per rule
and file and `--timeout-threshold 0`. Under Semgrep's default (5 s, a file's
remaining rules dropped after three timeouts) Cursor-Governance's required
Python instance ended `incomplete`, and with it `Analyze (central Core)` on
every Cursor-Governance pull request.

- `org-ci.yml`'s 18 Core action pins advance from `8554ce96` to `eba72ac3`
  (#307's merge, an ancestor of `main`), and the `sdk-revision` fallback moves
  from `dc783c64` to `fbe14fec`. `analyze-semgrep.yml`'s `install-semgrep` pin
  moves with them (identical bytes).
- Between `8554ce96` and `eba72ac3` the actions `org-ci.yml` uses change in two
  places: `provision-sdk` admits `fbe14fec` (and `9af1a202`, the tested
  rollback), and the advisory `check-toolchain-alignment` reports drift on
  every declaration surface (#295). Nothing else that `org-ci.yml` runs
  changes.
- The fleet gains `9af1a202`'s scan-scope planning (#130: a language whose
  files the scan never reaches is not applicable, not a required instance that
  scans nothing) and its incomplete-coverage accounting (#131), plus the B5
  budget.

Tests: `tests/workflows/test_org_repository_verification.py`,
`tests/workflows/test_workflow_toolchain_pins.py`,
`tests/workflows/test_org_ci_required_instances.py`;
`tools/check_core_pin_ancestry.py --main origin/main`.

## 39. Core owns each repository's supply-chain exemptions (2026-11-21) — follow-up to the supply-chain lane

`run-supply-chain` now reads a Core-owned data file per repository,
`.github/actions/run-supply-chain/repositories/<owner>__<name>.json` (lower
case), selected by `$GITHUB_REPOSITORY` (or `--repository`). Schema
`l9.supply-chain-core-repo-policy/v1`: `repository` (must name the file's
repository), `exemptions` (`{purl, license, reason}`) and
`additional_allowed_licenses` (`{license, reason}`). Every entry needs a
reason; a Core-denied licence can never be allowed; a name that is not
`<owner>/<name>` is refused rather than resolved to a path.

- This puts exemption authority in Core (§3): a consumer changes its data by a
  Core pull request. The consumer-authored `.l9/supply-chain-policy.json` is
  still read and merged additively; whether to retire it is an open contract
  question, so this change only adds.
- Data shipped: Cursor-Governance exempts `pkg:pypi/l9-graphite-memory`;
  cognitive.engine.graphs and enrichment.inference.engine exempt
  `pkg:pypi/constellation-node-sdk` — the three findings predicted in
  `docs/supply-chain.md`, all first-party and Proprietary.

Tests: `tests/actions/test_run_supply_chain.py`.

## 40. The promotion canary judges a Core change against the fleet before merge (2026-11-21)

A Core pull request that changes what `Analyze (central Core)` runs is checked
against the current main heads of the repositories declared in
`.l9/promotion-canary.json` (Cursor-Governance, Cognitive.Engine.Graphs,
Enrichment.Inference.Engine, l9-ci-sdk, l9-ci-core) and judged
**differentially**: it fails only when it makes a repository worse than the
base pins do. This is the #308 hand canary made a blocking check,
`Promotion canary (Core)`. A repository already red for its own reasons does
not block the Core change, so the pin advance that fixes it is never
deadlocked.

- **What counts as such a change:** `org-ci.yml`,
  `.l9/sdk-compatibility.yaml`, any `.github/actions/<name>/` that `org-ci.yml`
  calls (read from its `uses:` lines), and the canary itself. Any other pull
  request passes the verdict without running a leg. The workflow has no
  `paths:` filter, so the check always reports.
- **How it runs the pull request's pins:** `org-ci.yml` targets
  `github.repository` at `github.sha` and no caller can redirect it, so the
  canary does not call it. `tools/promotion_canary.py render` generates
  `.github/workflows/promotion-canary.yml` from the `analyze` job's steps,
  verbatim, replacing only the target identity (repository, revision, ref, the
  checkout token, and the matrix id that names the leg's artifacts) with a
  matrix entry. A pull request runs its own workflow files, so the leg runs the
  pull request's steps and pins. A change to `org-ci.yml` must regenerate the
  canary in the same pull request: `tests/workflows/test_promotion_canary.py`
  (self-ci) fails on drift, and `render` refuses an `org-ci.yml` that gains a
  target reference it cannot retarget.
- **The base pins, and the reuse:** the plan compares the `analyze` steps at
  the base tip (`HEAD^1` of the merge commit) and at the pull request by sha256
  digest. When they are equal, the base pins would run the same steps against
  the same heads, so the base result *is* the pull request's: one run per
  repository, nothing cached or guessed. When they differ, the verdict requires
  a base-pins leg record per repository whose digest is the base's, and fails
  closed without one. How that leg runs is an open decision (the CTO question
  of 2026-11-21: AGENTS.md §8 forbids both a composite action nesting Core
  action edges and a floating `@main`).
- **Verdict, per repository, base pins → pull request pins:** a pull-request
  leg that is incomplete, cancelled or errored (no verdict reached) always
  fails, as does a missing leg or one whose steps digest is not the plan's.
  Pass → findings or verification fails; findings → verification fails; a
  blocking finding id (the SDK gate results' `blocking_finding_ids`) present
  at the pull request's pins and absent at the base's fails. Keeping a result
  or improving it (fail → pass) passes. The verdict prints the before/after
  table in the run summary. The leg record names why a leg failed: `findings`,
  `verification`, `incomplete`, `cancelled` (superseded, or over its time
  limit) or `error`.
- **Two-step pins (§16):** a step-one pull request that changes only action
  code leaves `org-ci.yml`'s pins unchanged, so its canary re-runs today's
  behaviour; the change is judged on the step-two pin advance, the #308 case.
- **First live run (#311, run 44848185909):** Cognitive.Engine.Graphs,
  Enrichment.Inference.Engine and l9-ci-core green; Cursor-Governance red (29
  Semgrep findings, CG-CUTOVER); l9-ci-sdk main `0fa99e8` red at Core
  `eba72ac3` on one high `subprocess-injection` finding at
  `l9_ci/providers/semgrep/provider.py:153`, which #308's hand canary did not
  cover. Both reds are pre-existing, so a pull request that leaves them as
  they are passes.
- **Access:** public repositories are read with `github.token`; l9-ci-sdk with
  the existing read-only `L9_GITHUB_READ_TOKEN`. No App token and no write
  access. A fork pull request has no secrets, so its l9-ci-sdk leg cannot
  check out and the canary fails closed.

Tests: `tests/workflows/test_promotion_canary.py`,
`tests/workflows/test_artifact_handoff_producers.py`,
`tests/workflows/test_phase_scope.py`.

## 41. The promotion canary's base pins run inline (2026-11-21) — amends §40

§40 left the base-pins leg undecided ("How that leg runs is an open
decision"), so the verdict failed closed on every pull request that changes
the `analyze` steps. The first one after #311 merged, #312, failed exactly
that way ("no base-pins leg record" for every repository). The leg could not
be a composite action at `@main` (§8 forbids a composite nesting Core action
edges and a floating `@main`), a `workflow_dispatch` of main's copy (it needs
`actions: write` in a pull-request workflow;
`tests/workflows/test_workflow_permissions.py`), or `pull_request_target`
(absent from Core by design).

- **Two inline legs.** `promotion-canary.yml` now carries a `head` job,
  rendered from the tree's `org-ci.yml`, and a `base` job, rendered from
  `origin/main`'s `org-ci.yml` when `tools/promotion_canary.py render` ran
  (between `base pins leg: begin/end` markers). Both run in the pull request's
  own run, read-only, and upload `promotion-canary-<side>-<slug>`; the verdict
  downloads both from that run.
- **The base leg is proven, not trusted.** The plan re-renders the base tip's
  (`HEAD^1`) `analyze` steps and runs the `base` job only when that render is
  byte-identical to the embedded one (`base-current`). Otherwise the verdict
  fails with the fix: fetch `origin/main`, run `render`, push. A pull request
  that leaves the `analyze` steps unchanged never runs the base leg, so an
  embedded base that main has since moved past never blocks it.
- **Every org-ci job, not only `analyze`.** A job `org-ci.yml` adds that calls
  a Core reusable workflow at a full sha (#312's `security`) is inlined into
  each leg ahead of the `analyze` steps, from that workflow at that pin, so a
  pull request that adds a job to Analyze is canaried with that job. The
  inlined steps are retargeted like `analyze`'s, describe a push of the
  evaluated head for event-scoped values (a main head has no pull request
  range), continue on error, and their outcomes become the result `analyze`
  reads as `needs.<job>.result`; the workspace is cleared before the `analyze`
  checkout. Any other job shape (own steps, `needs`, `secrets`, a matrix, a
  multi-job workflow, an unmapped `needs.` read) is refused, never left out.
  The inlined bytes are embedded; the plan, which has the history the pins
  need, re-renders them (`head-current`) and a stale render fails the verdict.
  The differential digest now covers all of org-ci.yml's `jobs:`.
- **`render --check`** (self-ci drift test) verifies the head leg against the
  tree's `org-ci.yml` and the rest of the file against its own embedded base.
- **`enforcement`** joins the leg statuses: verified and analysed, then a later
  gate step failed (for example a security enforcement step). It is a real
  failure of the repository, not `error`.
- **Main was red, and this repairs it.** #302 changed `org-ci.yml` after #311's
  last run, and #311 merged with its earlier render, so main's canary was stale
  and its drift test failed; #311's tool also wrote the required check's
  literal name, which #302's `tests/workflows/test_required_check_name.py`
  forbids. Both are fixed here.

Tests: `tests/workflows/test_promotion_canary.py`.

## 42. Core owns the central finding-waiver ledger (2026-11-21) — step one of N1e-LEDGER

Required Semgrep rules accept no inline `nosemgrep` and no consumer ignore
(l9-ci-sdk #128). A required finding can now be waived only by an entry in
Core's ledger, `.github/actions/resolve-governance/finding-waivers.yaml`
(`l9.finding-waivers/v1`, l9-ci-sdk #138). The SDK owns what an entry means:
it matches one exact finding by repository, provider, provider rule ID, path,
`Finding.fingerprint` and `source_sha256`, never a rule or a line number. It
applies only from `created` through `expires`, and its only effect is to turn
a `blocking` classification into `advisory`. Core owns the file, its review,
and getting it to the SDK.

- **Resolve.** `resolve-governance` checks only the transport envelope: a JSON
  object holding exactly `schema` and `waivers`, the right schema, and entries
  with unique string ids. Anything else fails closed. It stages the ledger in
  `RUNNER_TEMP`, never the workspace, because the SDK refuses a ledger inside
  `--root` and Core scans with `--root` at the workspace. It emits the
  absolute path as `finding-waivers`. The ledger's bytes are now part of the
  governance digest.
- **Invoke.** `invoke-sdk` takes `waivers`, `waiver-repository` and
  `waiver-date` for `semgrep-run` and `semgrep-normalize`. It forwards them as
  `--waivers`, `--repository` and `--waiver-date`, and only when a ledger is
  set. The ledger must be an absolute regular file outside
  `GITHUB_WORKSPACE`. A repository or a date without a ledger is refused.
- **Inert until step two.** Core's pinned SDK has no `--waivers`, so
  `org-ci.yml` does not pass the ledger yet. Step two (§16) advances the pins
  to an admitted SDK revision that contains #138 and wires `waivers` into the
  primary semgrep run and every required instance. `waiver-repository` must be
  the analysed repository, which in a canary leg is `matrix.repository`, not
  `GITHUB_REPOSITORY`.
- **Entries.** The ledger ships empty. Its first planned entry, Django's
  `subprocess-injection` rule applied to code in l9-ci-sdk that is not Django,
  was made unnecessary when l9-ci-sdk #141 merged (the SDK applies Django
  rules only where it detects Django). Each entry needs an owner, a reason and
  an expiry, and is removed once the fix it stands in for is pinned.

Tests: `tests/governance/test_finding_waiver_ledger.py`,
`tests/invocation/test_invoke_sdk_finding_waivers.py`.

## 43. SDK `2796d7a4` activation, and the canary runs the revision a pull request admits (2026-11-21) — amends §40, §41

**Activation (PIN-ADVANCE-1, #315 and #316).** Step one (#315, merged as
`df3b2eaf`) admitted l9-ci-sdk `2796d7a4`: #141 applies framework-scoped
Semgrep rules only where the framework is detected, so the Django
`subprocess-injection` rule no longer fires on code that is not Django. #137
adds a test witness only. Step two (#316, merged as `fb9fa789`) moved
`org-ci.yml`'s 18 Core action pins and `analyze-semgrep.yml`'s
`install-semgrep` pin from `eba72ac3` to `df3b2eaf`, and the `sdk-revision`
fallback from `fbe14fec` to `2796d7a4`.

- Between those Core pins, the actions `org-ci.yml` uses also gained #300 (an
  unresolved merge conflict fails repository verification) and #310
  (housekeeping never blocks). `run-supply-chain` and `install-consumer-ci`
  changed too, but `org-ci.yml` does not call them.
- #316's canary showed the effect. The l9-ci-sdk leg went from `findings` (1
  blocking) to `pass` (0). Cursor-Governance went from 29 blocking findings to
  28: exactly one finding is gone, the one at
  `repository_event_bridge.py:279`, which the SDK now withholds as "django is
  not detected", and no finding is new.

**Row 25: an admission-only pull request was never canaried at the revision
it admits.** #315's verdict passed while both of its legs still ran
`fbe14fec`. Every Core action pin in `org-ci.yml`, `provision-sdk`'s
included, names a commit already on main, and `provision-sdk` reads its
allowlist from its own commit. So a pull request that changes only the
default revision does not move what `org-ci.yml` runs.

- **Admitted leg.** The plan compares the tree's `.l9/sdk-compatibility.yaml`
  default revision with `org-ci.yml`'s `sdk-revision` fallback. When they
  differ, it emits that revision as `admitted` and uploads the pull request's
  `.github/actions/provision-sdk/` and `.l9/sdk-compatibility.yaml` as the
  `canary-admitted-core` artifact.
- **What the leg runs.** The `admitted` job is the head leg with one
  difference: its `id: sdk` step downloads that artifact into `RUNNER_TEMP`,
  never the workspace, and runs the action's own composite steps inline. Its
  inputs are org-ci's `with:` values, with `sdk-revision` set to the admitted
  revision, and the step id stays `sdk`, so every later step reads the same
  outputs. Any action shape the tool cannot map is refused, never left out:
  outputs that are not one-to-one with the `provision` step, an unmapped
  input, or no single `id: sdk` step.
- **Judgement.** The admitted leg is judged against the pull request's own
  pins with §40's rules, and the verdict prints it as its own table. A missing,
  `incomplete`, `cancelled` or `error` admitted leg fails; a regression or a
  new blocking finding fails; `improved` and `kept` pass.
- Replaying the rule on history: on #315's merge it names `2796d7a4` as
  admitted; on #316's merge it names nothing, because org-ci already runs the
  default.

Tests: `tests/workflows/test_promotion_canary.py` (`AdmittedLegTests`,
`AdmittedVerdictTests`).

## 44. CENTRAL-DIFF: findings are judged by what a run brings in (2026-11-21) — PIN-ADVANCE-3

Step one (#323, merged as `284a5eb6`) admitted l9-ci-sdk `4a887396`, and
step two activates it. It brings #144 (CENTRAL-DIFF: `semgrep run
--baseline-commit` classifies findings new or pre-existing, SDK ADR 0023),
#138 (the finding-waiver ledger, §42), #136 (B3: `semgrep consumer-run`, SDK
ADR 0021) and #134 (tests in required scope).

- **Baseline (operator decision 2026-11-21, row 23).** A run judges only the
  code it brings in, and only nightly judges the whole tree.
  - The `baseline` step passes the evaluated revision's first parent for the
    event classes `pull_request`, `merge` and `push`. On a pull request or
    merge group that revision is GitHub's merge commit, whose first parent is
    the target tip (the merge-base, or the group's base). On a push it is the
    previous tip.
  - `nightly`, `release` and `supply_chain` pass no baseline, so the whole
    tree is judged by policy alone.
  - A baseline that does not resolve blocks the job.
  - This supersedes SDK ADR 0023's "omit the option on a push to the default
    branch" for Core. The SDK-side amendment to that ADR belongs in
    l9-ci-sdk.
- **Effect.** A pre-existing blocking finding is reported as advisory. Only
  new blocking findings and incomplete results fail. #134's pre-existing test
  findings (row 26: SDK 104, CEG 91, EIE 19) therefore warn.
  - The advisory step `Report CENTRAL-DIFF pre-existing findings (advisory)`
    prints each bundle's `baseline <sha>: N new, M pre-existing …` count.
  - The SDK fails closed on tracked changes, so the `baseline` step names any
    tracked file that differs from HEAD before the scan.
- **Ledger.** `resolve-governance` stages `finding-waivers.yaml` in
  `RUNNER_TEMP`. The primary run and every instance run pass `--waivers` and
  `--repository` with it.
- **Consumer rulepack (B3).**
  - The inventory runs with `--consumer-rules`.
  - The planner executes `consumer:*` targets, and a declared consumer
    rulepack is a required instance.
  - The instance runner runs it as `semgrep consumer-run`, its own execution
    and bundle. Its gate result is aggregated like a language's.
  - The baseline is not applied to it.
- **Canary.** The head leg is the acceptance run. #317's admitted leg would
  have judged `4a887396` without this wiring had step one made it the default,
  which is why it was admitted as supported but not default.

Tests: `tests/workflows/test_org_ci_required_instances.py`,
`tests/invocation/test_invoke_sdk_baseline_consumer.py`,
`tests/actions/test_plan_required_instances.py`.

## 45. Consumer verification runs under the event it checks out (2026-11-21) — row 27, CANARY-EVENT

The promotion canary checks out a consumer's main SHA inside a Core
`pull_request` run, so the consumer's `make` inherited the Core PR's
`GITHUB_EVENT_NAME=pull_request` (and its `GITHUB_SHA`, refs and event file).
Cursor-Governance's `ops/scripts/lib/ci_pr_scope.sh` correctly refuses a
`pull_request` whose HEAD is not a two-parent merge commit
(`PR_SCOPE_UNKNOWN`), so its leg failed repository verification at both pins
and "kept: already red at base pins" masked every later CG regression.

Decision: **push semantics**, not a synthesized merge commit. A consumer's main
SHA is the state that consumer actually runs after a push to main; it has no
pull request, so a synthesized merge would need an invented base and PR head,
and CG's scope would then test a change set that never exists. Push states
only facts about the checked-out commit.

Mechanism: a workflow `env:` cannot overwrite `GITHUB_*` (GitHub "Variables"
reference: "You can't overwrite the value of the default environment
variables named `GITHUB_*` and `RUNNER_*`"), so `run-repository-verification`
gains inputs `event-name` (empty or `push`) and `event-ref`
(`refs/heads/<branch>`). With `push`, `run.py` sets, before any consumer
command, `GITHUB_EVENT_NAME=push`, `GITHUB_SHA` = workspace HEAD,
`GITHUB_REF`/`GITHUB_REF_NAME`/`GITHUB_REF_TYPE` from the ref,
`GITHUB_REPOSITORY` from the verified identity, clears `GITHUB_BASE_REF` and
`GITHUB_HEAD_REF`, and points `GITHUB_EVENT_PATH` at a push payload written for
that commit. Any other value, or a ref without an event, fails verification as
`infrastructure` before a consumer command runs. Empty changes nothing, so
org-ci's own runs keep their native context.

Step one (this change) ships the inputs. Step two moves org-ci's Core pins to
step one's merge, has org-ci pass `event-name: ''` / `event-ref: ''`, and the
canary retargets those to `push` / `refs/heads/${{ matrix.branch }}` for every
consumer leg, with a canary test that no leg's verification runs under the
Core PR's event.

The CENTRAL-DIFF baseline per event class, a dated amendment of l9-ci-sdk ADR
0023 (operator row 23), is recorded in `docs/central-diff-baseline.md`.

Row 27 step two (2026-11-21): org-ci's Core pins name the change that ships
`event-name`, and org-ci passes `event-name: ''` / `event-ref: ''`. The canary
retargets them to `push` / `refs/heads/${{ matrix.branch }}` for every leg,
through `OPTIONAL_REPLACEMENTS`, since a base leg may render an org-ci.yml
older than them. The current org-ci.yml must carry them
(`ConsumerEventTests`). The same change makes the merge-group baseline
`merge_group.base_sha` (`docs/central-diff-baseline.md`).

## 46. Core's pinned toolchain is audited (2026-11-21) — TOOLCHAIN-AUDIT

Nothing audited the tools Core pins for every governed repository
(`.github/actions/install-consumer-ci/toolchain-lock.json`): a consumer's lock
export excludes them by design, and Core's own lock holds two packages.
`tools/audit_core_toolchain.py` resolves every Python tool that
`core-owned-tools.json` lists under `pip` (pip, pre-commit, ruff, mypy, pytest,
semgrep, bandit, pip-audit, uv) into pinned requirements, from the installs
Core actually performs: the consumer CI toolchain resolved together (as
`install.sh` installs it), Semgrep's hash-locked closure as written, and every
other tool resolved alone (as `security.yml` installs each). Each must be
covered at the lock's version, or the audit refuses. Separate installs can pin
one package at different versions, so the set is written as layers that each
name a package once, and `pip-audit --disable-pip --no-deps` audits every
layer. The log names the count. biome (npm), gitleaks (a binary) and the
interpreter are not Python packages and are out of this audit's scope.

`.github/workflows/self-toolchain-audit.yml` (`Core toolchain audit`) reports
on every pull request and merge group. It audits when the change touches the
lock, an audited install or the audit itself, where a known vulnerability
blocks; otherwise it reports not applicable. It audits nightly and by dispatch
regardless; there a finding fails the run, and the run, its summary and the
`toolchain-audit` artifact are the report. Org-ci does not consume it, so no
two-step activation applies. Making it a required check is a ruleset change
(the ruleset binds contexts from real runs, `.l9/release-plane.yaml`).

**Exceptions and the Semgrep re-lock (2026-11-21, addendum to §46).** The first
audit found 30 known vulnerabilities, all in Semgrep 1.171.0's hash-locked
closure. `locks/semgrep-1.171.0.txt` is re-locked with `lock_semgrep.py`
(urllib3 2.8.0, certifi 2026.11.20 and other patch updates; the engine stays at
1.171.0). Core's one exceptions file,
`.github/actions/install-semgrep/audit-exceptions.yaml`
(`l9.toolchain-audit-exceptions/v1`, a JSON document like the finding-waiver
ledger, whose entries need a Semgrep finding fingerprint and so cannot name an
advisory on a package), excepts one advisory on one `package@version`. Each
entry carries a concrete reason that it is not reachable in Core's use (checked
against how the pinned SDK invokes Semgrep), owner `l9-ci-core`, a creation
date, an expiry at most 14 days later, and a removal trigger. `audit_core_toolchain.py
judge` reads pip-audit's JSON, which is run without any ignore. The audit fails
on any of these:

- an open finding;
- a stale entry;
- an expired or malformed entry;
- a pinned package that pip-audit did not audit.

The log reads `<n> pinned package(s) audited, <m> finding(s) excepted, <k>
open`. pip-audit's readable report takes each exception as `--ignore-vuln`.
The subcommands are `resolve`, `exceptions` and `judge`. Promoting Semgrep
1.186.0 makes the pyjwt and mcp PYSEC entries stale, and they must then be
removed.

**wcmatch exception (2026-11-21, CTO decision).** GHSA-53pm-wgxf-c6qf on wcmatch
8.5.2 is reachable: `semgrep scan` matches the file names a PR author controls
against globs. It is still excepted, because the effect is on availability
only, the time it can take is bounded, and the bound fails closed:

- **Bounded time.** The SDK kills a scan after 300 s, and every Core job that
  runs Semgrep has `timeout-minutes`: 30 for each job, 60 for the nightly sink.
- **Fails closed.** A timed-out scan is a fatal TIMEOUT failure. A cancelled job
  publishes as `cancelled`, which
  `test_a_timed_out_or_cancelled_analysis_never_passes` covers. A cancelled
  canary leg is incomplete.
- **No shipped multi-star glob.** No glob that Core or the SDK ships has 3 or
  more stars.

The exception is removed when Semgrep allows wcmatch 11.2 or later.

**Semgrep 1.186.0 activated (2026-11-21, step B, step two).** The admit step
(#338) added Semgrep 1.186.0. This step makes it the default:

- **Pins.** The Core action pins in org-ci (and so in the inlined canary and
  nightly-sink) and analyze-semgrep's `install-semgrep` advance to `2ee1720b`.
  The only action change these pins pick up is `install-semgrep`.
- **Versions.** Every 1.171.0 default moves to 1.186.0: org-ci, analyze-semgrep,
  sdk-contract-check, `toolchain-lock.json` and the regenerated
  `tool-ownership.json`.
- **Exceptions.** At 1.186.0 the closure locks pyjwt 2.15.1 (no findings) and
  mcp 1.29.0. The 15 pyjwt and 8 mcp@1.23.3 exceptions are therefore stale and
  removed. Five mcp GHSAs, which need mcp 1.30.0, remain on mcp@1.29.0, as do
  opentelemetry-api (2) and wcmatch (1). Each reachability claim was
  re-checked against 1.186.0's source. The audit reads "105 pinned
  package(s) audited, 8 finding(s) excepted, 0 open".
- **Test.** The lock-binding test now skips the canary's base leg, which renders
  `origin/main` and so carries main's version during a version change.

`locks/semgrep-1.171.0.txt` stays for rollback.

## 47. Semgrep runs nightly, not on pull requests (2026-11-21) — amends §35, §40, §44

Operator decision: "the semgrep scan inside analyze must be removed and the
semgrep check moved to nightly". It supersedes row 23 for Semgrep. Record:
[`docs/semgrep-runs-nightly.md`](docs/semgrep-runs-nightly.md).

- **Required check.** `org-ci.yml` `Analyze (central Core)` runs repository
  verification, Biome, toolchain alignment and the security scan. It does not
  provision the SDK or run Semgrep, so no Semgrep result reaches a merge
  verdict. The scan tree and the CENTRAL-DIFF baseline (§44) left with it.
- **One Semgrep source.** `.github/workflows/central-semgrep.yml`
  (`workflow_call`, called by nothing) holds the whole chain: every planned
  instance (§35, L9-CI-CORE-003), the SDK gate, SARIF and artifacts.
- **Nightly.** `nightly-sink.yml` renders its scan from `central-semgrep.yml`
  (`python3 tools/nightly_sink.py render`): default branch, clean checkout,
  whole tree, findings to code scanning and the rolling issue.
- **Canary (§40).** `promotion-canary.yml` keeps the org-ci head and base legs
  and adds the nightly-scan legs `scan` and `scanbase`; the admitted-SDK leg
  (§43) is a scan leg. Scan legs run as a `nightly` event, as the sink does
  (whole tree, advisory), and are judged on concluding and on the findings the
  pull request adds. The verdict prints and judges both tables.

Tests: `tests/workflows/test_central_semgrep_required_instances.py`,
`tests/workflows/test_instance_runner_scan_root_paths.py`,
`tests/workflows/test_promotion_canary.py` (`ScanVerdictTests`),
`tests/tools/test_nightly_sink.py`.

## 48. Biome is its own check, judging incoming code only (2026-11-21) — amends §16

Operator decision. Record: [`docs/biome-check.md`](docs/biome-check.md).

- **Check.** `org-ci.yml` job `biome`, named `Biome`, beside `security` and
  `analyze`; `analyze` no longer installs or runs Biome. Blocking. It always
  reports: no `biome.json` / `biome.jsonc`, or no changed file in Biome's
  scope, is a success that says so.
- **Incoming code only.** A pull request is judged on the files it changes
  against its base, a merge group against `merge_group.base_sha`, a
  default-branch push against `before`; other events judge the whole tree.
  The paths come from `git diff --name-only --diff-filter=d <base> <revision>`
  and go to `biome ci` as arguments (Biome 2.5.8; `--changed --since` needs a
  merge base a shallow checkout cannot promise).
- **Nightly.** `nightly-sink.yml` renders the job's steps on the whole default
  branch, advisory, to code scanning category `l9-central/biome/`.
- **Canary (§40).** org-ci.yml jobs that run their own steps are siblings of
  every leg; their result is part of the leg record (`checks`).
- **Required.** Adding `Biome` to ruleset 21895545 is the operator's; GitHub
  lists the check only after it has reported once.

Tests: `tests/workflows/test_org_ci_biome_job.py`,
`tests/workflows/test_promotion_canary.py` (`StepJobTests`),
`tests/tools/test_nightly_sink.py`.

## 49. gitleaks, pip-audit and npm-audit are checks of their own; bandit is nightly (2026-11-21) — amends §47, §48

Operator decisions ("same approach for all tools"; bandit option A). Record:
[`docs/security-checks.md`](docs/security-checks.md).

- **Checks.** `org-ci.yml` jobs `gitleaks`, `pip-audit` and `npm-audit`, each
  named as its job, beside `Biome` and `analyze`. `analyze` no longer `needs`
  a security job and has no "Enforce security scan" step; `org-ci.yml` no
  longer calls `security.yml` (kept for `self-security.yml`). Each check
  always reports.
- **Incoming code only.** gitleaks scans the commits the event adds;
  pip-audit audits the full resolved lock only when a dependency manifest or
  lock changes, otherwise "dependencies unchanged"; npm-audit stays opt-in
  (off: "not applicable"), and when enabled audits only when `package.json` /
  `package-lock.json` change.
- **Modes unchanged.** gitleaks and pip-audit block; npm-audit is opt-in.
- **Nightly.** `nightly-sink.yml` runs the `gitleaks` check's step on the whole
  tree (`gitleaks dir`, SARIF `l9-central/gitleaks/`, counted), the
  `pip-audit` check's audit (ratcheted), and bandit: nightly only, advisory,
  SARIF `l9-central/bandit/` listed and never counted, the full JSON report
  kept as an artifact, tests excluded at any depth.
- **Signal.** Findings are annotated at file and line; a secret's value is
  never printed (gitleaks `--redact`; only file, line, rule and commit read).
- **Canary (§40, §48).** Each check is a sibling step job of every leg; its
  result is part of the leg record.
- **Required.** Adding `gitleaks`, `pip-audit` and `npm-audit` to ruleset
  21895545 is the operator's; each must report once first.

Tests: `tests/workflows/test_org_ci_security_checks.py`,
`tests/tools/test_nightly_sink.py`, `tests/workflows/test_promotion_canary.py`.

## 50. CodeQL is one org-wide check, judging only what a change introduces (2026-11-21)

Operator decision. Record: [`docs/codeql-check.md`](docs/codeql-check.md).

- **Check.** `org-ci.yml` job `codeql`, named `CodeQL`, beside the other
  checks; `contents: read` only (L9-CI-CORE-005), so it uploads nothing on a
  pull request and does not use `codeql-action`. The CodeQL CLI comes from the
  checksum-pinned official bundle (2.27.5).
- **Only new results.** The revision and the event's base are both analyzed
  (build-mode none, `security-extended`; `actions` for workflow or action
  files, and python, javascript, ruby, java, csharp, rust by their files). A
  result the base lacks (rule, path, `primaryLocationLineHash`) at level error
  or security severity 7.0+ fails; other new results warn. No base (dispatch,
  canary): nothing is new.
- **Nightly.** `nightly-sink.yml` runs the check's steps on the whole tree and
  publishes `l9-central/codeql-<language>/`, counted. Default setup blocks
  CodeQL uploads: the sink publishes every other category first, then fails
  the night naming the refused CodeQL categories.
- **Migration.** (a) the check lands, (b) per-repository CodeQL files are
  deleted, (c) default setup is turned off; file and click lists in the
  record. Requiredness (ruleset 21895545) is the operator's.

Tests: `tests/workflows/test_org_ci_codeql_job.py`,
`tests/tools/test_nightly_sink.py`, `tests/workflows/test_promotion_canary.py`.

## 51. actionlint is one org-wide check, judging changed workflows (2026-11-21)

Operator decision. Record: [`docs/actionlint-check.md`](docs/actionlint-check.md).

- **Check.** `org-ci.yml` job `actionlint`, named `actionlint`, blocking,
  `contents: read`. A pull request, merge group or push judges the workflow
  files it changes (every workflow when an action definition changes);
  other events and the nightly sink judge every workflow. It always reports.
- **Pin.** Core's toolchain lock: `toolchain-lock.json` `actionlint` and
  `install-consumer-ci/locks/actionlint-<version>.json` (sha256), bound to the
  job's literals by test; `tool-ownership.json` now owns actionlint.
- **Deterministic.** shellcheck and pyflakes integrations are off (the runner
  image's copies are unpinned). A repository's `.github/actionlint.yaml`
  applies; Core's ignores only `job.workflow_sha` in `settings-runner.yml`.
- **Generated workflows.** The canary and sink renderers write an undeclared
  `inputs.X || default` as its default and the leg record reads only its own
  leg's steps, so Core's generated workflows pass actionlint.
- **Nightly.** `l9-central/actionlint/`, counted.

Tests: `tests/workflows/test_org_ci_actionlint_job.py`,
`tests/tools/test_nightly_sink.py`, `tests/workflows/test_promotion_canary.py`,
`tests/workflows/test_workflow_toolchain_pins.py`.
