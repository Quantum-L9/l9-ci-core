<!-- L9_META
l9_schema: 1
origin: l9-ci-core
layer: [docs, governance, shadow-mode]
tags: [L9_TEMPLATE, rule-modes, rollout]
owner: platform
status: active
/L9_META -->

# Shadow Mode Rollout

L9 CI rule modes let Quantum-L9 introduce new governance checks without breaking every repository at once.

## Modes

| Mode | Merge behavior | Use case |
|---|---|---|
| `blocking` | Fails the gate | Proven transport, security, threshold, and policy violations |
| `advisory` | Visible but non-blocking | Inherited debt or calibrated rules |
| `shadow` | Telemetry-only | Brand-new rules under false-positive measurement |
| `disabled` | Ignored with policy record | Time-bounded exception only |

## Promotion path

```text
shadow -> advisory -> blocking
```

Promotion to `blocking` requires a governance policy edit and platform validation, including `l9-validated:approve` when the edit touches `.github/governance/*`.

## Artifact contract

Rule mode appears on every normalized finding and flows into:

```text
artifacts/ci/*_ci_summary.json
artifacts/agent_review_payload.json
```

Agents must not treat `shadow` or `advisory` findings as merge blockers.
