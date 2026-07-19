# L9 Auditor → Planner → Remediator pipeline run

**Target repository:** `l9-ci-core` (in-scope)
**Snapshot base ref:** `f7a4ee8c1f4e4413cb3645d088cafa3e9c798235`
**Repository snapshot id:** `sha256:448f3665b0863de8b03b9ed06e766d84d0869e759212255ccb04c20698ac8810`
**Run date:** 2026-07-19
**Suite:** `l9-auditor-planner-remediator-suite` (auditor 3.3.0, planner 1.3.0, remediator 0.2.0/R1)

The three-agent suite was executed end-to-end against this repository. The suite
itself is not committed to this repo; it was run from the supplied archive and
only the resulting evidence artifacts are recorded here under `evidence/`.

## Result summary

| Stage | Command | Outcome | Key result |
|-------|---------|---------|------------|
| Auditor (native) | `l9_auditor audit` | `complete` | 97 files analyzed, 5 rules executed, **0 observations** |
| Auditor (federate) | `l9_auditor aggregate` | `complete` | 1 provider (native), 0 clusters, **0 candidate findings** |
| Planner | `l9_planner` | manifest emitted | `MAN-a5d0fdd02e489098ea65f79e`, governance **passed**, **0 findings selected** |
| Remediator (R1) | `agent_b_remediator.cli` | **rejected** | Manifest schema `l9.remediation-manifest.v4` not supported by R1 |

No defects were remediated because the deterministic auditor produced **zero
observations** within its declared coverage, so there was nothing for the
planner to select or the remediator to execute.

## Stage 1 — Deterministic Auditor

Ran the native auditor with the core rulepacks (`org.l9.core-packaging@1.0.0`,
`org.l9.core-security@2.0.0`) over the full working tree.

- **Outcome:** `complete` (not degraded/partial/failed)
- **Files considered / analyzed / skipped:** 97 / 97 / 0
- **Bytes analyzed:** 198,620
- **Rules enabled / executed:** 5 / 5
- **Capabilities exercised:** lexical, contextual, manifest_query, repository_inventory
- **Observations:** 0

A zero-observation result is an explicit auditor state: it means no enabled
deterministic rule matched within declared coverage. Per the auditor's own trust
model it does **not** assert the repository is defect-free — semantic analyses
(AST, control-flow, dataflow, taint, call-graph, ownership, temporal) are
unsupported and were not attempted.

The federated `aggregate` pass added no external providers (native only), so
clustering and qualification produced **0 candidate findings**.

## Stage 2 — Planner (Agent A)

The federated envelope was bridged into an `l9.planning-input.v1` document
(carrying the real snapshot id, base ref, and envelope hash) and planned with
the suite's `default-planning-policy.json` (`default-remediation@1.3.0`).

- **Manifest:** `MAN-a5d0fdd02e489098ea65f79e` (`l9.remediation-manifest.v4`)
- **Governance:** passed — lane-capacity, impact-budget, and validation-coverage
  gates all green; authorization and dependency-graph gates green
- **Selection:** 0 eligible / 0 selected / 0 rejected, 0 effort points
- **Execution waves / lanes:** 0 / 0

The planner emitted a complete, well-formed immutable manifest with an empty
task set — the correct output for an empty finding set.

## Stage 3 — Remediator (Agent B, R1)

The remediator was invoked against the planner manifest with a clean working
tree matching the declared base ref.

- **Outcome:** `remediation_rejected: unsupported manifest schema:
  'l9.remediation-manifest.v4'`

This is a **schema-version boundary in the suite as shipped**, not a fault in
this repository. Remediator R1 accepts `l9.manifest.v1` /
`l9.remediation_manifest.v1`, while planner P3 emits `l9.remediation-manifest.v4`.
Per the suite's own `VALIDATION_REPORT.json`, the remediator was only built
through R1 (R2 source was a zero-byte upload and R3 was blocked), so the newer
manifest revision has no consumer. The point is moot for this run regardless: the
manifest contains 0 contracts, so no remediation work existed to execute.

## Reproduction

```bash
# from the suite archive root
cd auditor
PYTHONPATH=src python3 -m l9_auditor aggregate <repo> --rulepacks ./rulepacks --output aggregate.json

# bridge aggregate.json -> planning-input.v1 (snapshot id, base ref, envelope sha), then:
cd ../planner
PYTHONPATH=src python3 -m l9_planner planning-input.json \
  --policy policies/default-planning-policy.json --output-root ./plan \
  --generated-at 2026-07-19T00:00:00Z --result-json plan-result.json
```

## Artifacts

- [`evidence/audit-envelope.json`](evidence/audit-envelope.json) — native audit (`l9.audit-envelope.v1`)
- [`evidence/federated-audit-envelope.json`](evidence/federated-audit-envelope.json) — aggregate (`l9.federated-audit-envelope.v1`)
- [`evidence/planning-input.json`](evidence/planning-input.json) — planner input (`l9.planning-input.v1`)
- [`evidence/remediation-manifest.json`](evidence/remediation-manifest.json) — planner manifest (`l9.remediation-manifest.v4`)
- [`evidence/plan-result.json`](evidence/plan-result.json) — planner result summary
