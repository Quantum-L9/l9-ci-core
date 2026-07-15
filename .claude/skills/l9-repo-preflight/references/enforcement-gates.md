<!-- L9_META
l9_schema: 1
parent: l9-repo-preflight
layer: reference
role: enforcement_gates
tags: [preflight, enforcement, gates, proof-of-compliance, protocol-violation]
owner: igor_beylin
status: active
version: 1.0.0
updated: 2026-07-15
/L9_META -->

# Enforcement Gates (runtime proof-of-compliance)

The pipeline advances only when each step deposits its **gate artifact** — machine-checkable evidence that the step actually ran on real repository facts. A step with no artifact is **not done**; advancing past it is a **protocol violation**. This is the layer that stops the pipeline from being narrated instead of executed.

## The gate artifacts

| Gate | Step | Required artifact | Blocks advance when |
|------|------|-------------------|---------------------|
| A | Run probe | a timestamped probe log ending in `PROBE COMPLETE`, with every section marker present | log truncated, missing sections, or no completion marker |
| B | Extract expertise (exemplary builds) | `expertise_model.yaml` + `skill_intelligence_report.yaml` present and cap-valid | intelligence model missing or incomplete |
| C | Evaluate gates | a readiness report (`schemas/preflight-report.schema.json`) with a verdict per gate 1–8 | any gate has verdict `Unknown` with no evidence |
| D | Classify failures | each NO tagged with a taxonomy class and an existing-vs-new label | a failure is unclassified, or a new failure is mislabeled existing |
| E | Remediate | the single next action recorded, and for autofix the exact command applied | a hard-stop was autofixed, or code/unknown-file was touched |
| F | Re-run probe | a **new** probe log post-dating the last fix | a gate is re-evaluated from a stale (pre-fix) log |
| G | Emit readiness | `ready: true` only with gates 1–7 `pass` and zero red lines tripped | `ready` claimed with any red line or non-pass gate |

## Protocol-violation detection

A run is **non-compliant** — halt and report — if any of these are observed:

1. **Stale-gate advance.** A gate verdict cites a probe log older than the most recent remediation. Every fix invalidates the prior log; re-run first.
2. **Waved unknown.** Gate 3 reports an `unknown`-class untracked file and the verdict is still `ready`. Golden Rule 1 is absolute.
3. **Manufactured baseline.** Gate 7 reports `ready` without a reproduced baseline, or a `new` failure was relabeled `existing` to pass. Golden Rule 2.
4. **Blueprint-forced failure.** Gate 4/5 is `blocked` solely because the repo lacks a blueprint assumption that is not actually expected here. Golden Rule 4 — this must be `adapt`, not `blocked`.
5. **Premature mutation.** Any tracked-code edit before gates 1–3 pass. Golden Rule 3.
6. **Autofix overreach.** Autofix modified an unknown file, tracked code, or a validation baseline. Autofix is fenced to safe non-code hygiene + install only.

## Verdict vocabulary

Every gate resolves to exactly one of:

- `pass` — evidence satisfies the PASS condition.
- `blocked` — a failure whose class requires a stop-and-fix before advancing.
- `confirm` — evidence is present but cannot be judged without an expected contract or a human (e.g. "is this the right repo?"). Not `pass`, not a red line — a required confirmation.
- `adapt` — the blueprint is wrong for this repo; update the blueprint/contract, do not fail the repo (gates 4/5).

`ready` is the whole-pipeline verdict, true only when gates 1–7 are `pass` (a `confirm` must be resolved to `pass` first) and no red line is tripped.

## The loop is mandatory

Every NO returns through `Fix → Re-run Probe → Verify → Continue`. There is no path that verifies a gate without a fresh probe behind it. The enforcement layer exists to make that loop non-optional: the artifact for Gate F is a probe log newer than the fix, and Gate C may only cite the newest log.
