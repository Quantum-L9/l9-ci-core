# ADR 0002: Evidence-gated CI promotion, native trust boundaries and tool leverage

## Status
Proposed. Campaign admission candidate; not permission to merge, disable workflows, modify vendor billing or grant secrets.

**Decision owner:** `Quantum-L9/l9-ci-core` for organization CI release, enforcement and trust-boundary orchestration. Native provider owners retain their own action configuration/credentials; SDK owns scanner facts. **Related:** Core ADR 0001; Core `.l9/governance-contract.yaml` and `.l9/release-plane.yaml`; `L9-CI-CORE-004`, `005`, `006`; global `L9-HARD-001`, `L9-CAPABILITY-001`, `L9-EVIDENCE-001`, `L9-INTEGRATION-001`.

## Context

Central CI updates can affect every governed repository. Existing local Semgrep, SonarQube Cloud, CodeQL, GitGuardian, Bandit and other jobs cover different threats and may have different native permission requirements. A similarly named rule, green local unit test, or shared version pin is not proof that a successor scan preserves coverage or value. Nor does the presence of a vendor workflow prove paid entitlement, valid execution or economically valuable results.

## Decision

1. **Promotion has a sequence:** source-owner contract change and fixture proof; SDK/Core public-interface compatibility at immutable revisions; representative live canaries; measured coverage/finding parity and required-check enforcement; gradual affected-fleet promotion; reversible closure. Preserve existing effective checks until their precise replacements demonstrate parity. A central configuration change does not automatically authorize deleting a consumer's tests, rulepack or native job.
2. **Independent evidence per finding and per stage:** use known-bad fixtures that trigger and known-good fixtures that do not; retain scan scope, exclusions, tool/rulepack digest, native result, effective policy, run/revision identity, and aggregate status. An invalid, missing, skipped or incomplete *required* provider or required project test must not produce green. A static schema pass proves only schema correctness.
3. **Privileges remain separated:** read-only untrusted pull request analysis never receives write-scoped CodeQL publication tokens, SonarQube Cloud secrets, or billing/administration credentials merely to centralize reporting. GitHub-native or SaaS jobs may remain outside the required PR job when the platform requires it, with separate authorized owner and least privilege. Correlate results by exact repository/revision/run, and explicitly label unattested or asynchronous native statuses. Never claim Core/SDK executed a vendor action that ran elsewhere.
4. **Existing CodeQL work stays settled:** preserve the accepted PR default-suite fast path and scheduled security-extended path, existing reusable workflow owner and native overlay/build behavior. Treat recently merged and open CodeQL PRs as source state, not a reason to redesign; any transfer to another owner needs new evidence of necessity and an explicit ADR successor.
5. **Every tool must earn its cost:** for both paid and free scanners evaluate effective license/entitlement, activated features, source coverage, unique actionable findings, precision/false-positive rates, PR critical-path runtime, runner-minutes, token/seat/LOC costs, and duplicates. Prefer configuring already-entitled tools to add unique value over buying more tools. Proposed retirement needs evidence that its useful signal is preserved or demonstrably unnecessary. Paid-account access unknown means optimization remains unverified; no agent may authorize purchases or billing/permission changes by implication.
6. **Rollbacks belong upstream:** stop the affected rollout wave on regression, return the owning Core/SDK/native-provider release to the last proven compatible revision through its governed release path, and rerun the relevant checks. A consumer-specific central policy bypass is not rollback. GitHub owner boundaries and actual organization ruleset state must be independently checked.

## Alternatives considered and rejected

- **Delete redundant-looking scanners upfront:** loses framework/domain-specific detections and may silently drop paid features.
- **Turn on every paid feature and every blocking rule immediately:** cost and false-positive inflation without measured incremental value.
- **Centralize all vendor tokens in a single PR job:** breaks least-privilege and may expose write-scoped credentials to untrusted code.
- **Give consumers local bypass controls:** defeats top-down governance and masks rollout regressions.
- **Replace CodeQL's completed architecture:** destroys proven investment without a demonstrated requirement.

## Consequences and follow-through

- Good: fewer duplicated jobs and less per-repo maintenance, with defensible detection quality and controlled spend.
- Cost: a staged rollout and provider-specific integration proofs are needed before a local check is removed; lack of vendor-account proof is an explicit blocker for claiming paid-tool optimization.
- Required realization: exact Core owner contract and executable promotion/rollback tests; native vendor settings inventory; cross-owner permissions proof; four-repo canary ledger and subsequent eligible-fleet coverage ledger.
- **Not decided:** purchase or cancel any subscription, approve secret expansion, bypass protected branches, merge PRs, or redefine `l9-assurance` authority.

## Acceptance conditions

- Attempting to retire a local rule without positive, negative and integrated parity evidence fails the migration gate.
- A failed native scan and a completed-but-advisory scan retain different statuses; a required scan missing credentials cannot be marked successful.
- A representative CodeQL PR remains fast and read-only outside privileged native publication; extended coverage runs at its established schedule.
- Tool-value report distinguishes measured unique signal and cost from estimates, missing entitlements and unapproved vendor changes.
