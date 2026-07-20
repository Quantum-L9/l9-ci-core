# L9 CI instantiation pack

Drop-in governance for a repository adopting l9-ci-core **v2**. Copy the six
`*.yaml` files in this directory into your repo at **`.github/governance/`** —
that is the path `resolve-governance` and `validate-governance` read.

This pack is **language-agnostic**: it works unchanged for **Python and
Node.js** repos. `semgrep` is the single provider the pinned SDK normalizes,
and semgrep scans Python, JavaScript, and TypeScript alike. The only
per-language difference lives in the caller workflow's semgrep `--config`
rulesets (see [`../l9-analysis.yml`](../l9-analysis.yml)), never in this pack.

> **Format gotcha — these are JSON.** The resolver parses each file with
> `json.loads`, so the `.yaml` files must be **valid JSON**: double-quoted
> keys, no comments, no trailing commas. Keep them as JSON objects.

## The six files

| File | You set | Hard rules enforced |
|---|---|---|
| `execution-profiles.yaml` | The profile set and each profile's `sdk_profile`, `strict`, `default_mode`, `providers`, `allowed_events` | Profile set must be **exactly** `pr_fast, merge, nightly, release, supply_chain`. `sdk_profile ∈ {ci_fast, ci_deep}`. `strict` boolean. `default_mode` ∈ the four modes. The resolved provider must appear in `providers`; the event must appear in `allowed_events`. |
| `provider-requiredness.yaml` | Per profile, is each provider required (`semgrep: true/false`) | Every profile must carry a **boolean** for each provider it declares. A required provider may not resolve to `disabled`. |
| `rule-modes.yaml` | `defaults` mode per profile; optional `provider_overrides` | `allowed_modes` must equal **exactly** `blocking, advisory, shadow, disabled`. Effective mode = provider override → profile default → profile `default_mode`. |
| `waivers.yaml` | Time-boxed waivers | Empty `[]` is valid. Each entry needs `id, owner, reason, created, expires` (ISO-8601 dates) and a `scope`. **Malformed, duplicate-id, or expired waivers are fatal.** |
| `promotion-policy.yaml` | Allowed mode `transitions` + promotion evidence `requirements` | Transition sources/targets must be valid modes; **self-transitions are prohibited**. |
| `quality-thresholds.yaml` | `sdk_policy` file to select per profile | Must be a string. Empty = no policy. If set, the path must exist; **Core validates existence only — the SDK owns threshold semantics.** |

## Resolved behavior of this pack

| Profile | Event | sdk_profile | Default mode | semgrep required |
|---|---|---|---|---|
| `pr_fast` | `pull_request` | ci_fast | blocking | yes |
| `merge` | `push` | ci_fast | blocking | yes |
| `nightly` | `schedule` | ci_deep | advisory | no |
| `release` | `push` | ci_deep | blocking | yes |
| `supply_chain` | `schedule` | ci_deep | blocking | yes |

Validated with Core's own `validate-governance` (`status: valid`).

## Rolling a provider out safely (shadow → advisory → blocking)

Start a new provider or a stricter policy in **`shadow`** (runs, artifacts
retained as promotion evidence, **no** GitHub check), then promote per
`promotion-policy.yaml`:

`disabled → shadow → advisory → blocking`

Change the mode in `rule-modes.yaml` `defaults` (or a `provider_overrides`
entry). `promotion-policy.yaml` records the evidence bar for each hop
(observation runs/days, zero contract/artifact failures, approval).

## Example waiver

`waivers.yaml` ships empty. To suppress a *gate* temporarily (Core never
suppresses findings — this only affects requiredness/mode gating), add an
entry like this (remember: valid JSON, no comments):

```json
{
  "schema": "l9.waivers/v1",
  "waivers": [
    {
      "id": "WAIVER-2026-001",
      "owner": "platform-team",
      "reason": "semgrep p/typescript false positive under review upstream",
      "created": "2026-07-18",
      "expires": "2026-08-01",
      "scope": {
        "repositories": ["Quantum-L9/your-repo"],
        "refs": ["refs/heads/main"],
        "profiles": ["pr_fast"],
        "providers": ["semgrep"]
      }
    }
  ]
}
```

Any empty scope list means "match all". Once `expires` is in the past the
whole resolve step **fails closed** — remove or extend expired waivers.

## Selecting an SDK policy (optional)

To raise or lower gates, point `sdk_policy` at a policy file the pinned SDK
understands, e.g.:

```json
"pr_fast": { "sdk_policy": ".github/governance/policies/pr-fast.policy.json" }
```

Commit that file; Core checks it exists and passes the path to the SDK. Core
never reads or evaluates its contents.

## Python vs Node.js — what changes, what doesn't

| Concern | Python repo | Node.js repo |
|---|---|---|
| This governance pack | identical | identical |
| Provider | semgrep | semgrep |
| semgrep rulesets (in the caller) | `--config p/python` | `--config p/javascript --config p/typescript` |
| Generic lint/test (separate) | ruff / mypy / pytest — see [`../l9-lint-test.yml`](../l9-lint-test.yml) | eslint / `tsc --noEmit` / `vitest run` — see [`../l9-lint-test-node.yml`](../l9-lint-test-node.yml) |

The analysis pipeline (this pack + semgrep + the SDK) is identical across
languages. Only the semgrep ruleset and your out-of-band lint/test suite differ.

### TypeScript / Node preset (strict TS repo)

For a strict-TypeScript service (e.g. eslint + `tsc --noEmit` + `vitest run`):

1. Copy the governance pack unchanged into `.github/governance/`.
2. Copy `l9-lint-test-node.yml`. It runs three independent required gates:
   `eslint .`, `tsc --noEmit` (type soundness, honors `strict: true`, emits no
   JS), and `vitest run` (one-shot — never bare `vitest`, which is watch mode).
   Package manager is auto-detected from the lockfile.
3. Copy `l9-analysis.yml` and drop the Python ruleset — keep only:
   `semgrep scan --config p/javascript --config p/typescript`.
4. Keep your existing `tsconfig.json`, `.eslintrc*`, and `vitest.config.ts` as
   the source of truth — the templates invoke your tools, they do not replace
   your configs.
5. Mark `ESLint`, `tsc --noEmit`, and `Vitest` as required checks in branch
   protection; roll semgrep out `shadow → advisory → blocking`.

## Wiring

1. Copy this directory's six files to `.github/governance/`.
2. Copy [`../l9-analysis.yml`](../l9-analysis.yml) to
   `.github/workflows/l9-analysis.yml` and set the semgrep `--config` line for
   your language.
3. (Optional) copy the matching lint/test template for your language:
   [`../l9-lint-test.yml`](../l9-lint-test.yml) (Python) or
   [`../l9-lint-test-node.yml`](../l9-lint-test-node.yml) (Node/TypeScript).

Pin Core by the immutable commit `f88116503430aa18992b70d8d31063e34ff97ef1`
(or the `v2.0.0` tag once published — never `@main`).
