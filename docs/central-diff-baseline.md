# CENTRAL-DIFF baseline per event class — dated amendment of l9-ci-sdk ADR 0023 (Core side)

This is a decision record, kept outside `docs/adr/`. That directory holds
only Core's proposed, invariant-binding ADRs
(`tests/governance/test_ci_runtime_invariants.py`).

## Status

Accepted 2026-11-21. Operator decision row 23; baselines set by the campaign CTO
on 2026-11-21. Additive: this record amends l9-ci-sdk ADR 0023 for Core's
orchestration only. It does not rewrite ADR 0023, which stays the SDK's
authority for what `semgrep run --baseline-commit` does.

**Decision owner:** `Quantum-L9/l9-ci-core` (which commit org-ci passes as the
baseline). **Related:** l9-ci-sdk ADR 0023 (CENTRAL-DIFF), SDK #144; Core
AGENTS.md §44; `.github/workflows/org-ci.yml` step `baseline`.

## Context

ADR 0023 has the SDK classify each finding as `new`, `pre_existing` or
`unknown` against a baseline commit. A pre-existing blocking finding is
reported as advisory. The SDK fails closed on an unresolvable baseline, on
tracked changes, or on an incomplete baseline scan. Which commit is the
baseline is the caller's choice. As written, ADR 0023 omits a baseline on
push, so a push to main is judged on the whole tree.

Operator row 23 replaces that: **a run judges only the code it brings in.**
A push gets a baseline too, and a baseline that cannot be resolved blocks
the run; it never falls back to full-tree judgement.

## Decision

| Event class | Baseline | Meaning |
|---|---|---|
| `pull_request` | merge-base: the first parent of GitHub's merge commit (`github.sha`), i.e. the target tip the PR merges into | findings the PR's merged result adds over its target |
| `merge` (`merge_group`) | `merge_group.base_sha` | findings the queued group adds over the branch it lands on |
| `push` | first parent of the pushed commit (`HEAD^1`) | findings the pushed commit adds over the previous tip |
| `nightly`, `release`, `supply_chain` | none: full tree | every finding is judged by policy alone |

1. The baseline is resolved before Semgrep runs. An event class that takes a
   baseline and has an SDK policy, but whose baseline commit does not
   resolve, fails the run (`::error::no baseline`). It never degrades to
   full-tree or to "no baseline".
2. Pre-existing blocking findings are not hidden. They are reported as
   advisory with counts in the run summary, in the step
   "Report CENTRAL-DIFF pre-existing findings (advisory)".
3. Consumer rulepacks (`semgrep consumer-run`, B3) take no baseline. A
   consumer's own rules are judged on the whole tree until the SDK defines
   diff semantics for them.
4. Nightly keeps the full tree on purpose. It is where every pre-existing
   finding is still seen, so CENTRAL-DIFF cannot turn a known finding into a
   permanent blind spot.

## Implementation state (2026-11-21)

- `pull_request` and `push`: implemented as `HEAD^1` of the evaluated
  revision, fetched at depth 2 (Core #324). On `pull_request` that revision is
  GitHub's merge commit, so `HEAD^1` is the target tip, as decided.
- `merge` (`merge_group`): implemented as `HEAD^1`, which equals
  `merge_group.base_sha` only for the first group in the queue. A group
  queued behind others is built on the previous group's commit, and its
  `HEAD^1` is that commit. Correcting this, by passing and fetching
  `github.event.merge_group.base_sha` explicitly and blocking when it does not
  resolve, is part of the row-27 step-two change (AGENTS.md §45), which edits
  org-ci's baseline step anyway.

## Consequences

- Good: a PR, a merge group or a push is blocked only by what it introduces,
  so an inherited finding can no longer hold every later change red.
  Nightly still sees the whole tree.
- Cost: the baseline scan roughly doubles Semgrep time on baseline events,
  and a shallow or broken history fails the run instead of degrading
  silently.
- Not decided: diff semantics for consumer rulepacks; any change to ADR
  0023's SDK-side behaviour.
