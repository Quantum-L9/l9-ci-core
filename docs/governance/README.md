# Core governance

## Purpose

Central governance controls when providers run, whether providers are
required, which SDK finding policy is selected, and how results are rolled
out. It does not inspect findings or calculate gates: the SDK classifies and
the SDK gate decides; Core enforces the decision in the organization
workflow.

## Where it lives

The one governance pack is bundled with the pinned `resolve-governance`
action under `.github/actions/resolve-governance/`:

| Document | Decides |
|---|---|
| `defaults/execution-profiles.yaml` | SDK profile, strictness, providers, admitted event classes per profile |
| `defaults/rule-modes.yaml` | organization rollout mode per profile and provider |
| `defaults/provider-requiredness.yaml` | whether a provider's absence or failure is fatal |
| `defaults/quality-thresholds.yaml` | which bundled finding policy a profile hands the SDK |
| `defaults/waivers.yaml` | centrally issued, scoped, time-bounded waivers |
| `defaults/promotion-policy.yaml` | admitted mode transitions and their evidence requirements |
| `semgrep-policy.yaml` | the `l9.finding-policy/v1` the SDK classifies Semgrep findings with |
| `identity-maps/<language>.yaml` | provider rule id → canonical rule id |

A consumer checkout carries none of these. `org-ci.yml` resolves
`@core-defaults` from the pinned action on every governed event, so the
documents a repository is judged by are exactly the ones on Core `main` at
the pinned revision. There is no second copy: the former `.github/governance/`
dogfood pack was retired on 2026-10-22 (`AGENTS.md` §15).

## Profiles

`pull_request` → `pr_fast`, `merge_group` and default-branch `push` → `merge`,
manual canaries → `nightly`, plus `release` and `supply_chain`.

## Modes

- **Blocking** — a non-passing SDK technical gate fails the required
  workflow, which the organization ruleset turns into a merge block.
- **Advisory** — findings are visible without blocking; invalid artifacts,
  incompatible contracts, malformed provider reports, and internal SDK or
  Core failures remain fatal in every mode.
- **Shadow** — artifacts are retained for evaluation and never published as
  a required decision.
- **Disabled** — the provider is not invoked. A required provider cannot be
  disabled.

Mode names the rollout posture of the *gate*; whether a particular finding
can fail that gate is the finding policy's `mode` for its rule (default or
explicit). Both must say `blocking` for a finding to block.

## Waivers

Waivers are scope- and time-bounded governance records. They do not edit or
remove canonical findings. Expired or malformed waivers fail validation.
Every waiver requires a unique identifier, owner, reason, creation date,
expiration date, and explicit scope. A consumer may only reference a waiver
id in `.l9/ci.json`; it cannot mint one.

## Promotion

A mode moves one rung at a time through `promotion-policy.yaml`
(`disabled → shadow → advisory → blocking`, reversible), with the
observation evidence it names. Changing the defaults is a governed Core PR;
the change reaches the fleet when the pinned action revision in `org-ci.yml`
advances to it.

## Policy boundary

Core selects an SDK policy path. The SDK validates and interprets the policy.
Core does not parse policy semantics or reconstruct policy classifications.
