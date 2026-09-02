# Central CI migration matrix

Per-repository capability ownership as measured on 2026-09-02, and what may be
removed once — and only once — central CI is green.

**Nothing in this matrix has been executed.** The Phase 6 canary gate is not
met (central CI fails on every Python consumer; see
`CENTRAL_CI_BRIDGE_FINDINGS.md` §4). This is the plan, not a changelog.

## Measured state

`core-pins` / `sdk-pins` count 40-hex first-party action pins under
`.github/workflows/`.

| Repository | Org ruleset | `l9-analysis.yml` | `.github/governance/` | core-pins | sdk-pins | Class |
|---|---|---|---|---|---|---|
| `l9-ci-debt-intelligence` | active | YES | YES | 8 | 1 | central consumer (canary) |
| `l9-ci-debt-lsp` | active | YES | YES | 8 | 0 | central consumer (canary) |
| `l9-ci-debt-resolver` | active | YES | YES | 8 | 0 | central consumer (canary) |
| `Cursor-Governance` | active | YES | YES | 8 | 1 | central consumer (fleet) |
| `Enrichment.Inference.Engine` | active | – | – | 1 | 0 | already central-only |
| `Constellation.Gate` | active | – | – | 1 | 0 | already central-only |
| `Gate_SDK` | active | – | – | 0 | 0 | already central-only |
| `l9-repo-template` | active | – | – | 0 | 0 | already central-only |
| `l9-cognitive-runtime` | active | – | YES | 2 | 0 | central-only + residual pack |
| `l9-ci-core` | active | – | YES | 23 | 0 | **control plane — never migrate** |
| `l9-ci-sdk` | active | YES | YES | 5 | 0 | **capability owner — self-CI, not a consumer** |
| `IB-Odoo_19` (`cryptoxdog`) | n/a | YES | YES | 9 | 0 | **out of org — not governed** |

`Quantum-L9/.github` — not inspectable from this session; class `UNKNOWN`.

## Classification rules applied

A workflow is **not** legacy merely because it references Core. Each edge was
classified by the capability it provides:

* **Organization enforcement (removable once central is green).**
  `.github/workflows/l9-analysis.yml` and the `.github/governance/**` control
  documents it reads. Central `org-ci.yml` supplies the same capability from
  `@core-defaults`, at `blocking` rather than the consumers' `shadow`.
* **Repository-owned (must survive).** Phase workflows (`phase-*.yml`,
  `LSP-P*`, `RESOLVER-P*`, `Intelligence Phase *`), lint/test, release,
  container, deploy, and repo-specific external Actions. These are *not*
  organization CI and are out of scope for deletion.
* **`UNKNOWN` — do not touch.** `l9-cognitive-runtime`'s residual
  `.github/governance/` with no `l9-analysis.yml` caller: the consumer of that
  pack was not identified. Resolve before migrating.

## Per-repository plan (blocked on the canary gate)

### Canaries — `l9-ci-debt-intelligence`, `l9-ci-debt-lsp`, `l9-ci-debt-resolver`

Preconditions, **all** required before any deletion:

1. `Organization CI (Core)` concludes **success** on a real PR in ≥ 2 canaries.
2. That run reaches the SDK technical gate (`GATE_STATUS` non-empty) — today it
   dies before it.
3. The required status check `Analyze (central Core)` is emitted and green.
4. The failure path is demonstrated at least once (a real finding blocks).

Then, per repository:

* delete `.github/workflows/l9-analysis.yml`;
* delete `.github/governance/**` **only after** the identity map it contains is
  centrally owned (see below) — deleting it first destroys reviewed content;
* remove the 8 first-party `l9-ci-core` action pins that exist solely for that
  caller;
* keep every `phase-*.yml`, lint/test and release workflow untouched;
* update repo classification/profile so a copied caller cannot regrow.

### The identity map is a migration blocker, not a side quest

`l9-ci-debt-intelligence/.github/governance/semgrep-identity-map.yaml` holds
**151 reviewed rules**. The SDK ships **15**. Core's `@core-defaults` ships
**none**, and `org-ci.yml` never passes `invoke-sdk`'s existing `identity-map:`
input.

Deleting consumer governance packs before that map is promoted centrally would
delete the only broad, human-reviewed identity coverage in the organization.
Promote first, delete second. `l9-ci-debt-lsp` and `l9-ci-debt-resolver` have no
map at all, which is consistent with their central runs failing on identity.

### Already central-only

`Enrichment.Inference.Engine`, `Constellation.Gate`, `Gate_SDK`,
`l9-repo-template` own no organization CI caller. **No action.** Their remaining
single Core pin is a repository-owned use, not org enforcement — verify before
touching.

### Never migrate

* `l9-ci-core` — the control plane; its 23 pins are self-referential.
* `l9-ci-sdk` — the capability owner; its `l9-analysis.yml` is self-CI.
* `IB-Odoo_19` — `cryptoxdog` org, not targeted by the Quantum-L9 ruleset.

## Regression guard (to add once migration lands)

A central consumer must not contain:

* `.github/workflows/l9-analysis.yml`;
* first-party `l9-ci-core` action pins used for org enforcement;
* a consumer-selected SDK pin used for org enforcement;
* copied central governance ownership.

The control plane must:

* reference `Quantum-L9/l9-ci-core` `.github/workflows/org-ci.yml`;
* pin an **immutable SHA** — today it pins `refs/heads/main`, which violates the
  contract and is repaired in `Quantum-L9/.github` (not delivered here);
* support desired-vs-live read-back verification.
