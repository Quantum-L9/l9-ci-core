<!-- L9_META
l9_schema: 1
origin: l9-ci-core
layer: [docs, ci, ast-governance]
tags: [L9_TEMPLATE, semgrep, shadow-mode, TransportPacket]
owner: platform
status: active
/L9_META -->

# AST Governance

AST governance is the shadow-mode enforcement layer for L9 protocol laws. It turns architectural doctrine into structural checks while preserving rollout safety.

## Current mode

All AST governance rules in this pack are **shadow mode only**. They emit findings for telemetry and agent payload shaping but do not block merges until promoted by governance policy.

## Rule families

- `.semgrep/l9-transport.yml`: TransportPacket-only law and legacy packet detection.
- `.semgrep/l9-routing.yml`: Gate-only routing and direct node dispatch detection.
- `.semgrep/l9-logging.yml`: forbidden logging fields such as token, password, secret, raw_response, and llm_response.
- `.semgrep/l9-handler-signature.yml`: handler boundary signature checks.

## Promotion path

Rules must move through:

1. shadow
2. advisory
3. blocking

Promotion to blocking requires `l9-validated:approve` and platform owner review. No new AST rule should be introduced as blocking before telemetry is reviewed.

## Boundaries

This layer does not replace runtime tests, TransportPacket schema validation, or GitHub branch protection. It produces machine-readable CI findings that can later feed bounded Implementer/Validator loops.
