# AGENT.md — L9 governance context (l9-ci-core)

Governance contract loaded by the L9 Implementer pipeline (`Quantum-L9/PR_Repair`)
before it acts on a payload from this repository. Factual and concise — not
auto-generated.

## Repository role

`l9-ci-core` is the reusable GitHub Actions workflow + governance platform. It
owns `.github/workflows/` (reusable `workflow_call` definitions), the governance
defaults under `.github/governance/`, the Semgrep AST rules, and the
`agent-review-payload` / `ci-summary` JSON schemas. It installs and calls the
`l9-ci` SDK; it does not implement the SDK runtime. GitHub Actions is a runner
shell only.

## Wire contract

TransportPacket is the only supported wire contract. `PacketEnvelope` is
superseded and must not be reintroduced. No `cryptoxdog` references, no public
`check-packet-envelope` command. The Semgrep rules enforce this in shadow mode.

## Implementer invariants (non-negotiable)

1. **Write, never merge.** `GITHUB_TOKEN` (or `L9_IMPLEMENTER_BOT_TOKEN`) with
   `pull-requests: write` / `issues: write`. Never merge, never edit branch
   protection, never mutate repository settings.
2. **Proposal-only by default:** `dry_run`, `PR_FIX_LLM_APPLY=0`, no push.
3. **Deterministic autofixes never call an LLM;** the LLM lane respects protected
   paths and never-auto-repair categories, with verify/rollback on every change.
4. **Fork safety.** Secret-dependent jobs are same-repo only. Never
   `pull_request_target`.

## Protected paths (never auto-repair)

- `.github/workflows/**` reusable workflow contracts (inputs/permissions).
- `.github/governance/**` policies and thresholds.
- `schemas/**` (cross-repo payload/summary contracts).
- release/tag configuration.

## Never-auto-repair categories

- Security findings requiring human judgement.
- Governance policy / threshold changes.
- Anything altering the TransportPacket contract or a schema's required fields.

## Escalation

See `docs/ci-enablement/RUNBOOK.md`. Autonomy is raised only by explicit
operator dispatch.
