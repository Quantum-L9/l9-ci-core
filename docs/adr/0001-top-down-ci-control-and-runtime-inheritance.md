# ADR 0001: One organization CI control plane, inherited runtime configuration

## Status
Proposed. Campaign admission candidate; not an enacted policy or evidence of rollout.

**Decision owner:** `Quantum-L9/l9-ci-core` for organization CI orchestration and runtime governance. The GitHub organization ruleset owner retains target/enforcement authority; `.github` retains global L9 semantics. **Related:** Core `.l9/architecture.yaml`, `.l9/ownership.yaml`, `.l9/governance-contract.yaml`, `.l9/release-plane.yaml`; SDK ADR 0016 and ADR 0017; campaign `L9-CI-CORE-001`, `002`, `003`, `006`; global `L9-OWNER-001`, `L9-MONOTONIC-001`, `L9-ASSURANCE-001`, `L9-UNKNOWN-001`.

## Context

The live organization ruleset already requires Core's `org-ci.yml`, Core centrally selects tool versions and Semgrep policy, and SDK observes repository capabilities. However, other tools and reusable scanner configurations remain materialized in individual repositories, creating drift, duplicate execution and repeated repinning. A required check can also give an incomplete picture when only one of multiple applicable languages or providers is selected. The purpose is to strengthen the **existing** chain, not to invent another CI control plane.

## Decision

1. **Downward control:** GitHub organization rulesets select governed repository/branch targets and require the central workflow. `l9-ci-core` is the sole organization CI orchestration, exact-version, event-profile, provider-requiredness, execution-mode, permission, and enforcement owner. `l9-ci-sdk` implements factual capability detection, provider execution/normalization and technical evaluation under a pinned Core-authorized contract. Consumers cannot select the central Core/SDK revision, weaken a required scanner, change a central pin, or supply an alternative required check.
2. **Upward evidence:** Each scanner produces native observations; SDK retains source identity, report provenance, ruleset/configuration digest, analyzed scope, failures and coverage; Core publishes and enforces the applicable technical outcome. `l9-assurance`, where admitted and integrated, independently owns the final organization policy decision. A Core/SDK technical gate must not be presented as an Assurance verdict.
3. **Runtime inheritance:** Approved organization-wide scanner defaults and ruleset compositions are selected at execution from pinned Core/SDK sources. For this Core/SDK-managed required path, a newly governed supported repository must not need a copied central workflow, central scanner config, locally pinned scanner version, or routine update PR. The existing stamped-file drift and `l9.repo-execution/v2` contracts **must be revised and proved compatible before** retiring a formerly mandatory stamped file. The repository still owns genuine project facts, native tests, local implementation, language/build requirements and domain-specific semantic rules. The native-provider exception in Decision 6 remains explicit.
4. **Total applicable coverage:** Core consumes the SDK's entire applicable capability/instance inventory, not one preferred language. Every required applicable instance must have a supported terminal outcome before the central check passes. An unevaluated, skipped, timed-out, untrusted, unavailable or invalid required instance is never a successful execution. Truly non-applicable checks require explicit evidence of non-applicability. Unknown must remain visible.
5. **Monotonic specialization:** Central reusable defaults are mandatory minima. Legitimate product constraints may add checks or strengthen rules with traceable ownership, but cannot suppress or override the mandatory organization baseline. SDK's mechanical rule composition must preserve this. No hidden consumer selection of centrally governed enforcement policy.
6. **Scope of exception:** Tool execution follows its supported platform contract. Native GitHub/SaaS analyzers may run through a separately governed privileged workflow and yield exact-run evidence for correlation. Preserve Cursor-Governance's accepted CodeQL reusable workflow, suites and thin consumer callers, including SHA pins and adoption updates until an explicitly admitted compatible successor. Existing governed native config generators and project bindings retain their source owners. These exceptions do not allow consumers to override Core/SDK policy or pins and do not create a second organization policy owner. Core's current Biome execution is a valid existing bridge until a separately reviewed and tested SDK provider supersedes it; this ADR does not falsely declare all tools already SDK providers.
7. **Evidence-bound coverage:** Fleet-wide enforcement may be claimed only after current effective ruleset targeting, branch protection, required-check freshness, bypass policy and executed run evidence are verified for each in-scope repository. A repository under another GitHub owner, such as `cryptoxdog/IB-Odoo_19`, requires separately demonstrated governance.

## Alternatives considered and rejected

- **Copy Core/SDK required workflows/config into every repository:** retains fleet-wide drift, stamp churn and local authority over what should be central; distinct from the preserved native exceptions above.
- **Make `Quantum-L9/.github` distribute CI or interpret provider findings:** violates the existing separation between global semantic authority, GitHub ruleset enforcement, Core orchestration and SDK analysis.
- **Core parses reports or selects one dominant language:** steals SDK semantics and permits incomplete coverage.
- **Move every native SaaS credential into the read-only PR workflow:** breaks trust boundaries and increases secret exposure.

## Consequences and follow-through

- Good: one central Core/SDK revision can improve its governed coverage and policy for every bound consumer without routine downstream updates; native caller updates remain explicit under Decision 6, and the detected repository matrix remains reproducible.
- Trade-off: central regressions have fleet-wide blast radius, so controlled canaries, rollback and immutable evidence are not optional; see Core ADR 0002.
- Required realization: amend the existing Core owner contracts, not a parallel policy file; integrate SDK contract changes; migrate stamp enforcement safely; add observed good/bad fixtures and live organization-rule/required-job proofs; only then remove duplicate local scanner workflows.
- **Not decided:** a new global invariant, a new CI distribution service, a new paid subscription or unverified cross-account GitHub rule enforcement.

## Acceptance conditions

**Mandatory first-consumer promotion gate:** The first integrated production
consumer acceptance after Core/SDK promotion is a real required-workflow run
against `Quantum-L9/Cursor-Governance` at the exact evaluated head, with
positive/negative fixture parity for general and Cursor-owned special rules.
Core self-CI, SDK tests and PR publication do not discharge this requirement.
Do not retire local checks or proceed to other consumers/fleet rollout before
this gate passes. Existing Core rollback and org ruleset authority remain
unchanged. See campaign `CURSOR_CANARY.md` for concrete test procedure.

- New supported Python and TypeScript consumer with no copied central scanner configs receives the same centrally pinned scanner behavior as a previously stamped consumer.
- Mixed-language fixture executes *every* required provider/language pair; killing one required instance makes the required check fail or explicitly incomplete.
- Consumer attempt to lower blocking requiredness or override tool version is rejected; valid product-native settings continue to work.
- Actual ruleset/evaluated revision/bypass evidence is recorded for Quantum-L9 canaries; `cryptoxdog` status is established separately.
