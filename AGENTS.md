# Agent Instructions — `l9-ci-core`

This file is the in-repo agent SSOT for **what this repo is**, **how it
works**, **how the template flow moves**, and **exactly what a consumer repo
must do to integrate**. Read it before changing anything here, and read it
before wiring Core into a downstream repo.

Read these three files before changing any code in this repo:

1. [`.l9/architecture.yaml`](.l9/architecture.yaml) — role, phase status, the
   Core⇄SDK dependency direction, owned-vs-not-owned split.
2. [`.l9/ownership.yaml`](.l9/ownership.yaml) — the detailed ownership
   boundary (`core_owns` / `sdk_owns` / `prohibited_in_core`).
3. [`.l9/sdk-compatibility.yaml`](.l9/sdk-compatibility.yaml) — the exact SDK
   revision(s) Core is allowed to provision, and the policy flags governing
   how it may be resolved (no floating refs, no branches, no tags, no short
   SHAs).

---

## 1. What this repo is

`l9-ci-core` is a **thin GitHub Actions control plane**. It owns
orchestration, not analysis:

- GitHub Actions workflow/job topology, permissions, and concurrency.
- Immutable provisioning of `l9-ci-sdk` (git-pinned to a full 40-char SHA;
  see `.l9/sdk-compatibility.yaml`).
- Governance resolution (execution profile → mode → provider requiredness).
- Artifact routing, retention, and manifesting.
- Publication: GitHub check runs, workflow summaries, bounded PR annotations,
  release-readiness validation.

It does **not** own analysis semantics. That line is drawn in
`.l9/ownership.yaml`:

| Core owns | SDK owns |
|---|---|
| orchestration, security/trust boundary, provisioning, publication | provider execution/SPI, provider-native report parsing, canonical evidence/findings/classification/coverage, identity resolution, policy classification, schema/semantic validation, deterministic serialization, agent-payload projection |

**Dependency direction is one-way and locked:** `l9-ci-core → l9-ci-sdk`. The
reverse is prohibited. Core never re-implements SDK behavior — see
`prohibited_in_core` in `.l9/ownership.yaml` (no provider parsers, no
canonical finding/evidence model, no copied SDK schema, no rule-identity or
severity-normalization logic, no scanner adapters, no AST/Tree-sitter/graph
engine).

**Workflow inventory** is enforced by
`tests/workflows/test_phase_scope.py`. Categories:

```
# Self PR CI / contract gates
self-ci.yml
sdk-contract-check.yml
governance-ci.yml
release-validation.yml

# Self-only dogfood callers (authorized stubs — not a consumer surface)
self-analysis.yml          # thin caller → analyze-semgrep.yml
self-security.yml          # thin caller → security.yml (v1 kernel)

# Reusable analysis / publication kernels (workflow_call)
analyze-semgrep.yml        # preferred consumer kernel
profile-normalize-semgrep.yml
publish-analysis.yml
baseline-ratchet.yml
normalize-semgrep-report.yml   # nested helper (not a standalone consumer entry)

# v1 compatibility kernels (callable; historical @v1 contracts)
pr-pipeline.yml
pre-commit-ci.yml
nightly.yml
release-publish.yml
trio-governance.yml
security.yml
scorecard.yml
sbom.yml

# Maintenance
regenerate-identity-maps.yml
```

Adding a new analysis kernel (or expanding the consumer-callable set) is a
scope change — it requires an explicit, authorized plan, not an opportunistic
PR. Self-only dogfood stubs (`self-analysis.yml`, `self-security.yml`) are
allowed when `test_phase_scope.py` is updated in the same change.

---

## 2. How it works

**Preferred consumer path** is `analyze-semgrep.yml`: SDK-owned `semgrep run`
→ bundle validate → `gate evaluate` → agent-payload + SARIF projection →
artifact route/manifest/upload → nested `publish-analysis.yml`. Consumers
(and Core's own `self-analysis.yml`) should be thin `workflow_call` stubs
into that kernel.

Legacy normalize-only path (`profile-normalize-semgrep.yml` /
`publish-analysis.yml`) remains for callers that already produce a raw
semgrep report outside the kernel. Request flow for the preferred kernel, in
order:

1. **Resolve governance** (`actions/resolve-governance`) — reads the
   consumer's `.github/governance/*.yaml`, resolves `{profile, provider,
   event}` to `{mode, enabled, strict, required-provider, sdk-policy,
   governance-digest}` per `rule-modes.yaml` / `provider-requiredness.yaml` /
   `execution-profiles.yaml`.
2. **Provision SDK** (`actions/provision-sdk`) — clones the pinned SDK
   revision from `.l9/sdk-compatibility.yaml`, installs its `requirements.txt`
   into an isolated venv, verifies the CLI responds, emits `executable`.
3. **Invoke SDK** (`actions/invoke-sdk`) — a safe adapter over the allowlisted
   public SDK CLI operations (no shell evaluation, no arbitrary commands):
   `semgrep-run`, `semgrep-normalize`, `bundle-validate`,
   `bundle-project-agent-payload`, `bundle-project-sarif`,
   `compatibility-check`. The analyze kernel also runs `gate evaluate`
   (SDK verdict) and installs the pinned `semgrep` binary before
   `semgrep-run`.
4. **Route artifacts** (`actions/route-artifacts`, including SARIF) →
   **build manifest** (`actions/build-artifact-manifest`) → upload.
5. **Publish** (`publish-analysis.yml` → `actions/render-publication` +
   `actions/publish-check`) — renders the SDK's agent-review projection into
   a workflow summary + bounded PR annotations, uploads SARIF when present,
   then publishes the GitHub check per the resolved mode (`blocking`
   publishes a real conclusion; `shadow` retains artifacts with **no**
   check).

`workflow_call` (reusable) vs self-only:

| Workflow | Callable by consumers? |
|---|---|
| `analyze-semgrep.yml`, `profile-normalize-semgrep.yml`, `publish-analysis.yml`, `baseline-ratchet.yml`, v1 kernels (`pr-pipeline.yml`, `pre-commit-ci.yml`, `nightly.yml`, `release-publish.yml`, `trio-governance.yml`, `security.yml`, `scorecard.yml`, `sbom.yml`) | Yes — `workflow_call` |
| `self-ci.yml`, `sdk-contract-check.yml`, `governance-ci.yml`, `release-validation.yml`, `self-analysis.yml`, `self-security.yml`, `regenerate-identity-maps.yml` | No — self-only (Core's own CI/dogfood/release/maintenance) |
| `normalize-semgrep-report.yml` | Nested helper — not a standalone consumer entry |

---

## 3. Template flow (SSOT → org distribution → consumer)

```
docs/templates/            (SSOT — this repo)
      │  sync
      ▼
Quantum-L9/.github/l9-ci-pack/   (org distribution mirror + README)
      │  copy
      ▼
consumer repo .github/           (what actually runs)
```

- **SSOT lives here**: [`docs/templates/`](docs/templates/) (governance,
  analysis, Python lint) and [`presets/typescript/`](presets/typescript/)
  (locked Biome contract + Node lint caller). `docs/templates/l9-lint-test-node.yml`
  is the org-sync fallback and must stay Biome-owned.
- **`Quantum-L9/.github/l9-ci-pack/`** is the org-wide distribution copy of
  that same surface (mirrored via `ops/sync-v2-starters.sh`), plus an
  agent-first `README.md` so a consumer/agent never has to browse this repo
  to instantiate Core. The org seeder copies the pack into consumers
  (missing-only), including `biome.json`.
- **Consumers copy from the pack** (or directly from `docs/templates/` if
  working against this repo). Do not invent parallel/ad-hoc workflows in Core
  to serve a single consumer — extend the templates instead.

**Not Core's job:** org issue/PR templates. Those are owned solely by
`Quantum-L9/.github` community-health files (`.github/ISSUE_TEMPLATE/*`,
root `PULL_REQUEST_TEMPLATE.md` in that repo). Core does not ship an
`ISSUE_TEMPLATE.md` / `PULL_REQUEST_TEMPLATE.md` under `docs/templates/` —
they were removed as legacy leftovers with no deliverable role; do not
recreate them here or sync them into the org pack.

---

## 4. What a consumer repo must do to integrate

Works identically for **Python** and **Node.js** — `semgrep` is the single,
language-agnostic provider. The preferred caller is a thin stub into
`analyze-semgrep.yml`; the only per-language difference is `L9_LANGUAGE`
(`python` or `typescript`). The SDK owns ruleset selection — consumers author
no `--config` list.

1. **Copy governance** — the six files in
   [`docs/templates/governance/`](docs/templates/governance/) (or the org
   pack's `l9-ci-pack/governance/`) → your repo's `.github/governance/`.
   ⚠️ These are JSON-in-`.yaml`: double-quoted keys, no comments, no trailing
   commas (the resolver parses them with `json.loads`).
2. **Copy the analysis caller** —
   [`docs/templates/l9-analysis.yml`](docs/templates/l9-analysis.yml) →
   `.github/workflows/l9-analysis.yml` (or the matching preset under
   `presets/`). Set `L9_LANGUAGE` to `python` or `typescript`, pin Core by
   full SHA on the `uses:` line, and grant `checks: write` +
   `security-events: write` on the job that calls `analyze-semgrep.yml`.
3. **Optional hygiene** — copy
   [`docs/templates/l9-lint-test.yml`](docs/templates/l9-lint-test.yml)
   (Python: ruff/mypy/pytest) or
   [`docs/templates/l9-lint-test-node.yml`](docs/templates/l9-lint-test-node.yml)
   (Node: SDK Biome + `tsc --noEmit` + package test script). Stamp
   `presets/typescript/stamp.sh` for `biome.json` — do not invent it. These
   are generic dev-tool templates you own outright — Core does not call or
   gate on them.
4. **Set profile / rollout** — pick `pr_fast` / `merge` / `nightly` /
   `release` / `supply_chain` in `execution-profiles.yaml`; roll a new
   provider or stricter policy out `shadow → advisory → blocking` via
   `rule-modes.yaml` + `promotion-policy.yaml`.
5. **Verify** — artifact uploaded, GitHub check published (or shadow evidence
   retained), SARIF uploaded when enabled, lint/test green if adopted.

---

## 5. Pinning rules

- Pin Core by **full 40-char commit SHA**, or `@v2.0.0` / `@v2` once
  published. **Never `@main`.**
- Never pin `l9-ci-sdk` by a floating ref — Core's own provisioning already
  enforces this (`.l9/sdk-compatibility.yaml`: `floating_git_references_allowed:
  false`, `branches_allowed: false`, `tags_allowed: false`,
  `short_git_revisions_allowed: false`).
- `.l9/sdk-compatibility.yaml` (`default.revision` plus every
  `supported[].revision`) is the single source of truth for the SDK pin; when
  bumping it, keep every mirror copy in sync — `provision-sdk/action.yml`,
  `publish-analysis.yml`, `normalize-semgrep-report.yml`,
  `sdk-contract-check.yml`, and the `.l9` contract docs — and pin only a
  commit whose SDK `.l9/integration-contract.yaml` still exposes `semgrep
  normalize`, `bundle validate`, `bundle project-agent-payload`, and
  `compatibility check` (`sdk-contract-check.yml` verifies this on every PR).
- If you see the Core pin `54a2f2fc8d060674d544fab14388bb5eff6b8e78` anywhere,
  it is **stale** — it predates two provisioning fixes (`98f012f`: install SDK
  `requirements.txt`, incl. PyYAML, into the isolated venv; `d2c2cd7`:
  `_load_yaml_module()` so the allowlist loader can read
  `.l9/sdk-compatibility.yaml`) and will fail non-shadow publish with
  `ModuleNotFoundError: No module named 'yaml'`. It is not a missing SDK
  feature — it is a stale Core pin. Replace it with the current candidate/
  release pin.

## 6. Legacy `@v1`

Org `@v1` kernel starters (historical, frozen at a fixed SHA) exist only so
already-imported wrappers keep resolving. They are **not** the integration
path for new work — new work always starts from `docs/templates/` /
`l9-ci-pack/README.md` (v2). Do not restore retired v1 kernels onto `main`.

> `@v1.0.0` freeze: `978cf948133fa4d9cd6b78ecbb383295869cb70f` (PR #44
> v1-compat). `@v1` is a moving compatibility tag and may be ahead of that
> freeze — see [`docs/v1-compatibility.md`](docs/v1-compatibility.md). Tag
> create/verify scripts live in `Quantum-L9/.github` (`ops/tag-v1.sh`,
> `ops/verify-v1-anchor.sh`).

## 7. SDK CLI surface (wired vs dormant)

`invoke-sdk` allowlists: `semgrep-run`, `semgrep-normalize`, `bundle-validate`,
`bundle-project-agent-payload`, `bundle-project-sarif`, `compatibility-check`.
`analyze-semgrep.yml` additionally runs `gate evaluate` as an SDK-owned step
(exit code is the verdict; Core publishes it, never re-decides it).

| SDK CLI | Status |
|---|---|
| `semgrep run` | **Wired** — `invoke-sdk` `semgrep-run` in `analyze-semgrep.yml` |
| `gate evaluate` | **Wired** — `analyze-semgrep.yml` gate step (SDK verdict) |
| `bundle project-sarif` | **Wired** — `invoke-sdk` `bundle-project-sarif` in `analyze-semgrep.yml` |
| `semgrep normalize` / `bundle validate` / `bundle project-agent-payload` / `compatibility check` | **Wired** — `invoke-sdk` (+ normalize/profile paths) |
| `providers list` | Dormant — no inventory action |
| `providers detect` | Dormant — no capability-driven provider selection |
| `semgrep detect` | Dormant — no SDK preflight for binary/version |
| `semgrep normalize --derive-snapshot` | Flag unused — Core always passes an explicit `snapshot-id` |
| `SemgrepProvider.execute` (SPI) | No CLI / no Core caller — SDK `semgrep run` owns execution |

Expanding `invoke-sdk`'s allowlist or adding a new analysis kernel mid-cut
churns the candidate SHA. If a dormant op is explicitly authorized, wire it
into `invoke-sdk` + the publication path, update `required_cli_paths` / the
integration contract, and add tests before re-locking the candidate SHA.

---

## 8. Cross-repo action references & MANIFEST

Consumers call Core's actions and reusable workflows from a different
repository, so their checkout does not contain Core's `.github/actions/`.
Therefore:

- Inside a composite action or a reusable workflow, reference a sibling Core
  action by its fully-qualified pinned form
  (`Quantum-L9/l9-ci-core/.github/actions/<name>@<sha>`), never a relative
  `uses: ./...` path — the relative form resolves against the caller's
  workspace and fails with "Can't find action.yml" for every consumer.
- A `run:`-based step that copies a file must tolerate source == destination
  (a consumer may already have written the artifact at its routed location).
- `MANIFEST.sha256` records the sha256 of tracked files; regenerate the
  entries for any file you change so it stays honest. Both `make validate` and
  `tests/tools/test_manifest_integrity.py` verify it, so drift fails on the
  pull request that introduces it rather than surfacing later in release
  validation. `L9_MANIFEST_CHECK=0` disables verification for bisects and
  salvage work on a knowingly drifted tree — it is not a way to land a change
  without regenerating the manifest.

## Edit-time constraints (unchanged)

1. Read `.l9/architecture.yaml`, `.l9/ownership.yaml`,
   `.l9/sdk-compatibility.yaml` before changing files.
2. Preserve the one-way dependency from Core to SDK.
3. Do not implement SDK-owned behavior in Core.
4. Do not introduce floating dependencies.
5. Do not add analysis kernels without explicit authorization. Self-only
   dogfood stubs (`self-analysis.yml`, `self-security.yml`) are OK when
   `test_phase_scope.py` is updated in the same change.
6. Run the complete standard-library test suite: `python3 -m unittest
   discover tests`.

A change that duplicates SDK behavior is invalid even when all functional
tests pass.

---

## 9. Repository execution runtime

The local repository-execution contract is [`.l9/repo-workflow.json`](.l9/repo-workflow.json), validated by [`.l9/repo-workflow.schema.json`](.l9/repo-workflow.schema.json). Operator behavior and failure recovery are documented in [`docs/repository-execution-runtime.md`](docs/repository-execution-runtime.md).

- Run `make validate` after changing the runtime policy, implementation, generated Makefile, authority wiring, or checksum manifest.
- Run `make change-policy` to inspect targeted gates and companion obligations for the current change set.
- Run `make agent-check` before declaring work complete, committing, pushing, or opening a pull request. Targeted gates add evidence; they never replace the full configured check and test matrices.
- Keep the root `Makefile` generated and delegation-only. Runtime behavior belongs in `tools/l9_repo/`; executable policy belongs in `.l9/repo-workflow.json`.
- Do not bypass or weaken the completion proof, protected-branch refusal, clean-tree requirement, no-force policy, single-flight lock, or evidence emission.
- Exit `1` means blocking repository findings. Exit `2` means invalid configuration, infrastructure, comparison context, authority wiring, or repository state.

<!-- BEGIN L9 FORMATTER OWNERSHIP (generated — do not edit) -->

## Formatter ownership

Workspace class: `biome_default` — Default for every governed workspace: Biome owns JS/TS/JSON, Ruff owns Python.

Exactly one formatter owns each language. Do not reformat a file with a tool other than its owner, and do not add config for a competing formatter: the result is a diff that churns on every save.

| Languages | Owner | Note |
|---|---|---|
| `javascript`, `javascriptreact`, `typescript`, `typescriptreact`, `json`, `jsonc` | **biome** | bound by the governed IDE profile |
| `python` | **ruff** | bound by the governed IDE profile |

Generated from `environment/ide/policy.json` in the governance clone by `ops/scripts/adapters/agentdocs.sh`. Edit the policy, not this block.

<!-- END L9 FORMATTER OWNERSHIP -->
