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

### Implemented split

The context names in the table stay. `make test` is `repo-test`:
`python -m unittest --verbose` of the 17 frozen-census modules. A missing
listed path or a collection of zero fails it. It is not `unittest discover`.
`Repository contract` still runs `make setup` → `validate` → `check` → `test`
at the event revision. The portable phases stay `setup`, `validate`, `check`,
and `test`.

The `Unit tests` job keeps that name and runs `python -m unittest` on the
other 79 modules, with the same fail-closed checks. It does not run
`make test` and it does not discover. The table's "full `unittest` suite"
parenthetical is the pre-split job.

`make test-deep` is the only full discover:
`python -m unittest discover --start-directory tests --pattern 'test_*.py' --verbose`.

`.github/workflows/nightly-deep.yml` uses cron `17 7 * * *`, allows
`workflow_dispatch`, sets `permissions: contents: read`, and runs
`make test-deep` only when the ref is `Quantum-L9/l9-ci-core` default branch
`main`. It is not a required pull-request check. The retired stem `nightly`
stays retired. The payload does not gain a context for it.
`.github/workflows/org-nightly-dispatch.yml` uses the same cron for a
plan-only fleet pass that dispatches zero jobs and is not a required check.
A fixture from that planner is not a live schedule receipt.

On the pre-split push `44071155232a3d5232258a1f1a44f5a45c126f1c`, self-ci run
44208286762 spent 31s in `make test` and 29s in `Run Phase 1 validation`. No
per-test durations were read. After the split, manual dispatch
[44214710432](https://github.com/Quantum-L9/l9-ci-core/actions/runs/44214710432)
succeeded on `34f715627d9521cef997f7a31f0f2562b8ece01f`, and the schedule event
[44347968938](https://github.com/Quantum-L9/l9-ci-core/actions/runs/44347968938)
succeeded on `027e86543701740c4a238e593bd33c03f59eda00`. That schedule run was
created at 16:30 UTC, so the 07:17 UTC timing is not proven. Consumer
repositories stay on the required check named in `.l9/org-runtime-contract.yaml` (`entrypoint.required_check.name`).

**Applying this payload is an organization-admin action. The pull request that
changes it does not apply it**, and nothing in this repository writes a
ruleset; until an admin applies it, the live ruleset keeps its previous
contexts.

`integration_id` 15368 is GitHub Actions. The organization's required check
(`.l9/org-runtime-contract.yaml`, `entrypoint.required_check.name`) is not
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

### 2026-11-20 revision (operator decisions)

- `pull_request`: a pull request is still required, but with
  `required_approving_review_count: 0` and `require_code_owner_review: false`.
  Core has a sole operator plus agents, and the author cannot approve their
  own pull request, so a required approval blocked every merge or forced a
  bypass. The contract (`.l9/release-plane.yaml`) declares these exact values
  and the attestation holds the live rule to them.
- `merge_queue` (new): squash merges through a queue with `ALLGREEN`
  grouping, at most 5 entries built and merged together, a 60-minute check
  timeout and no minimum-batch wait. Every required context above now also
  runs on `merge_group`, so the queue can complete.
- `strict_required_status_checks_policy: false`: the queue re-tests each
  pull request on top of the current base, so a strict "up to date" policy
  would only add a second run per pull request. Freshness is attested
  through the active `merge_queue` rule (`production.admission_freshness`).
