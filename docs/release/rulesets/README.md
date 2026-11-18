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
| `Unit tests` | `self-ci.yml` (full `unittest` suite, pinned Python and requirements) |
| `Repository contract` | `self-ci.yml` (`make setup` → `validate` → `check` → `test` at the event revision) |
| `Governance validate` | `governance-ci.yml` |
| `sdk-contract` | `sdk-contract-check.yml` |
| `Analyze (semgrep) / Analyze (semgrep -> SDK)` | `self-analysis.yml` → `analyze-semgrep.yml` |
| `Security scan / security` | `self-security.yml` → `security.yml` |

### 2026-11-17 revision (AGENTS.md §29)

- The bare `validate` context was ambiguous: `governance-ci.yml` and
  `release-validation.yml` both have a job whose check run is named `validate`,
  so either could satisfy it. The governance job now carries the unique name
  `Governance validate`, and the payload requires that name instead.
- `Repository contract` is new: no self-CI job previously ran Core's own make-v1
  contract at the pull request's revision.
- The analysis and security contexts are reusable-workflow callers, so GitHub
  names their check runs `<caller job name> / <called job name>`. The strings
  above are the exact check-run names read from
  `repos/Quantum-L9/l9-ci-core/commits/<sha>/check-runs`: both ran green on
  `main` at `a18a8f6`, `c659f97` and `8c785c1`. At `44d85a6` and `9bd8863`
  (2026-11-17) `Analyze (semgrep) / Analyze (semgrep -> SDK)` and
  `sdk-contract` concluded `failure`; binding this payload while they are red
  blocks every merge to `main`.
- `Repository contract` and `Governance validate` have no green run on `main`
  yet: their first evidence is the pull request that introduces them. Read
  their check runs on `main` after it merges, before applying.

### This revision (`44071155232a3d5232258a1f1a44f5a45c126f1c`)

The context names in the table stay. On this revision, `Unit tests` and
`make test` each run a full `unittest discover` (`--start-directory tests
--pattern 'test_*.py'`). `Repository contract` still runs `make setup` →
`validate` → `check` → `test` at the event revision. The parenthetical
"full `unittest` suite" on `Unit tests` is that observed job.

The intended partition is not implemented at this revision: `repo-test` as 17
listed modules, the `Unit tests` job as 76 listed modules, `test-deep` as the
only full discover, and a Core `main` schedule at 07:17 UTC. `nightly-deep.yml`
is not implemented at this revision. The payload does not gain a context for
it. Durations are UNKNOWN. This document records no measured speed-up and no
fired cron. Consumer repositories stay on `Analyze (central Core)`.

**Applying this payload is an organization-admin action. The pull request that
changes it does not apply it**, and nothing in this repository writes a
ruleset; until an admin applies it, the live ruleset keeps its previous
contexts.

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
