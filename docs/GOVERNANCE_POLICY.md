# L9 CI Governance Policy

## Files

- `.github/governance/audit-policy.yml`
- `.github/governance/audit-baseline.json`
- `.github/scripts/classify_pr.py`

## Runtime Contract

The classifier emits exactly one of seven canonical classes. Unknown or malformed diffs fail closed. The baseline prevents inherited untouched debt from freezing unrelated PRs while keeping new high/critical or touched dangerous findings blocking.

## Blocking Controls

Required fail-closed controls:

- `DEPR-001`: `PacketEnvelope` forbidden
- `TP-MUTATION-001`: `TransportPacket` immutability
- `ARCH-001..003`: chassis/runtime imports forbidden in engine layer
- `HANDLER-SIG-001`: engine handler signature contract

## Comment Marker

CI audit comments use only:

```html
<!-- l9-ci-audit-marker: v1 -->
```

This must not collide with Audit, Implementer, or Validator markers.
