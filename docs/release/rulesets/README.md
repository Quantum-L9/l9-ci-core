# Core main protection — ruleset payload

`core-main-protection.json` is the repository ruleset `.l9/release-plane.yaml`
`core_main_protection` expects on `Quantum-L9/l9-ci-core` `main`, and that
`tools/verify_control_plane.py` attests (`Core main protection`). On
2026-10-22 the live attestation reported **FAIL**: the only active rules on
`main` were the organization ruleset's `workflows` and
`required_status_checks` (`Analyze (central Core)`); no `pull_request` rule
and no Core self-CI check were bound.

Required check contexts are evidence-backed (Cursor-Governance L9-ORG-009):
they are the job names of the checks that ran green on `main` at
`80c0cac4223d6ef0e001b53e8174be6cacd96f0f` (2026-10-21), read from
`repos/Quantum-L9/l9-ci-core/commits/<sha>/check-runs`:

| Context | Workflow |
|---|---|
| `Lint and Type Check` | `self-ci.yml` (toolchain conformance, ruff, mypy) |
| `Unit tests` | `self-ci.yml` (full `unittest` suite) |
| `validate` | `governance-ci.yml` |
| `sdk-contract` | `sdk-contract-check.yml` |

`integration_id` 15368 is GitHub Actions. `Analyze (central Core)` is not
repeated here: the organization ruleset already requires it on every default
branch, this repository included.

## Apply (organization admin; the session proxy refuses ruleset writes)

```bash
gh api -X POST repos/Quantum-L9/l9-ci-core/rulesets \
  --input docs/release/rulesets/core-main-protection.json
make attest-control-plane      # expect: Core main protection PASS
```

A later change to the payload is a reviewed PR here followed by
`gh api -X PUT repos/Quantum-L9/l9-ci-core/rulesets/<id> --input ...`.
The ruleset bypasses nothing for repository admins by default; the single
`OrganizationAdmin` bypass actor mirrors the SDK's own
`SDK self-validation required on main` ruleset.
