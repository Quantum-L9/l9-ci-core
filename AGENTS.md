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

**Workflow set is frozen** at exactly seven files
(`tests/workflows/test_phase_scope.py` enforces this):

```
self-ci.yml
sdk-contract-check.yml
normalize-semgrep-report.yml
governance-ci.yml
profile-normalize-semgrep.yml
publish-analysis.yml
release-validation.yml
```

Adding an eighth reusable workflow (or a new analysis kernel) is a scope
change, not a normal edit — it requires an explicit, authorized plan, not an
opportunistic PR.

---

## 2. How it works

Request flow for the analysis path (`profile-normalize-semgrep.yml` /
`publish-analysis.yml`), in order:

1. **Provision SDK** (`actions/provision-sdk`) — clones the pinned SDK
   revision from `.l9/sdk-compatibility.yaml`, installs its `requirements.txt`
   into an isolated venv, verifies the CLI responds, emits `executable`.
2. **Resolve governance** (`actions/resolve-governance`) — reads the
   consumer's `.github/governance/*.yaml`, resolves `{profile, provider,
   event}` to `{mode, enabled, strict, required-provider, sdk-policy,
   governance-digest}` per `rule-modes.yaml` / `provider-requiredness.yaml` /
   `execution-profiles.yaml`.
3. **Invoke SDK** (`actions/invoke-sdk`) — a safe adapter over exactly four
   public SDK CLI operations (see §6, no shell evaluation, no arbitrary
   commands): `semgrep-normalize`, `bundle-validate`,
   `bundle-project-agent-payload`, `compatibility-check`.
4. **Route artifacts** (`actions/route-artifacts`) → **build manifest**
   (`actions/build-artifact-manifest`) → upload.
5. **Publish** (`publish-analysis.yml` → `actions/render-publication` +
   `actions/publish-check`) — renders the SDK's agent-review projection into
   a workflow summary + bounded PR annotations, then publishes the GitHub
   check per the resolved mode (`blocking` publishes a real conclusion;
   `shadow` retains artifacts with **no** check).

`workflow_call` (reusable) vs self-only:

| Workflow | Callable by consumers? |
|---|---|
| `profile-normalize-semgrep.yml`, `publish-analysis.yml` | Yes — `workflow_call` |
| `self-ci.yml`, `sdk-contract-check.yml`, `normalize-semgrep-report.yml`, `governance-ci.yml`, `release-validation.yml` | No — self-only (Core's own CI/release gates) |

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

- **SSOT lives here**, in [`docs/templates/`](docs/templates/): the six
  governance files, `l9-analysis.yml`, `l9-lint-test.yml`,
  `l9-lint-test-node.yml`.
- **`Quantum-L9/.github/l9-ci-pack/`** is the org-wide distribution copy of
  that same surface (mirrored via a sync script), plus an agent-first
  `README.md` so a consumer/agent never has to browse this repo to
  instantiate Core.
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
language-agnostic provider the pinned SDK normalizes. The only per-language
difference is the semgrep `--config` ruleset inside the copied
`l9-analysis.yml`.

1. **Copy governance** — the six files in
   [`docs/templates/governance/`](docs/templates/governance/) (or the org
   pack's `l9-ci-pack/governance/`) → your repo's `.github/governance/`.
   ⚠️ These are JSON-in-`.yaml`: double-quoted keys, no comments, no trailing
   commas (the resolver parses them with `json.loads`).
2. **Copy the analysis caller** —
   [`docs/templates/l9-analysis.yml`](docs/templates/l9-analysis.yml) →
   `.github/workflows/l9-analysis.yml`. Set the semgrep ruleset:
   - Python: `--config p/python`
   - Node/TypeScript: `--config p/javascript --config p/typescript`
3. **Optional hygiene** — copy
   [`docs/templates/l9-lint-test.yml`](docs/templates/l9-lint-test.yml)
   (Python: ruff/mypy/pytest) or
   [`docs/templates/l9-lint-test-node.yml`](docs/templates/l9-lint-test-node.yml)
   (Node: eslint/`tsc --noEmit`/vitest). These are generic dev-tool templates
   you own outright — Core does not call or gate on them.
4. **Set profile / rollout** — pick `pr_fast` / `merge` / `nightly` /
   `release` / `supply_chain` in `execution-profiles.yaml`; roll a new
   provider or stricter policy out `shadow → advisory → blocking` via
   `rule-modes.yaml` + `promotion-policy.yaml`. `checks: write` permission is
   only needed on the job that calls `publish-analysis.yml`.
5. **Verify** — artifact uploaded, GitHub check published (or shadow evidence
   retained), lint/test green if adopted.

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

## 7. Dormant SDK CLI surface (not yet wired into Core)

`invoke-sdk` allowlists exactly four SDK operations today: `semgrep-normalize`,
`bundle-validate`, `bundle-project-agent-payload`, `compatibility-check`. The
pinned SDK exposes more that Core does **not** call:

| SDK CLI | Wired into `invoke-sdk`? | Status |
|---|---|---|
| `gate evaluate` | No | Dormant — SDK owns gate semantics; Core's publish path uses workflow conclusion/mode instead |
| `providers list` | No | Dormant — no inventory action |
| `providers detect` | No | Dormant — no capability-driven provider selection |
| `semgrep detect` | No | Dormant — no SDK preflight for binary/version |
| `semgrep normalize --derive-snapshot` | Flag unused | Core always passes an explicit `snapshot-id` |
| `SemgrepProvider.execute` (SPI) | No CLI / no Core caller | Consumers run `semgrep scan` outside the SDK |

**Default:** documented here, wiring deferred until after a verified
`v2.0.0` cut — expanding `invoke-sdk`'s allowlist or the frozen workflow set
mid-verification churns the candidate SHA. If a dormant op is explicitly
authorized before a release cut, wire it into `invoke-sdk` + the publication
path, update `required_cli_paths` / the integration contract, and add tests
before re-locking the candidate SHA.

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
  entries for any file you change so it stays honest.

## Edit-time constraints (unchanged)

1. Read `.l9/architecture.yaml`, `.l9/ownership.yaml`,
   `.l9/sdk-compatibility.yaml` before changing files.
2. Preserve the one-way dependency from Core to SDK.
3. Do not implement SDK-owned behavior in Core.
4. Do not introduce floating dependencies.
5. Do not add analysis workflows beyond the frozen seven without explicit
   authorization.
6. Run the complete standard-library test suite: `python3 -m unittest
   discover tests`.

A change that duplicates SDK behavior is invalid even when all functional
tests pass.
