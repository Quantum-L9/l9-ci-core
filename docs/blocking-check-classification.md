# What Core may block a consumer merge on

Operator policy (2026-11-20): only checks that protect working or secure code
may block a pull request merge. Housekeeping never blocks. Secret scanning and
conflict markers stay blocking, and an INCOMPLETE result never passes.

Every step of the one required job, `org-ci.yml` `analyze` (its check name is
`.l9/org-runtime-contract.yaml` `entrypoint.required_check.name`), is listed
below with its class. `tests/workflows/test_blocking_check_classification.py`
fails when a step is added, renamed or removed without a row here.

Classes:

- **BLOCKING_CORRECTNESS**: protects working code, or the integrity of what is
  evaluated. It may fail the job.
- **BLOCKING_SECURITY**: protects secure code. It may fail the job.
- **HOUSEKEEPING**: drift or hygiene. It reports and never fails the job.
- **PLUMBING**: resolves inputs, installs pinned tools, routes or uploads
  artifacts. A failure is an infrastructure failure, and an evaluation that
  cannot run is INCOMPLETE, so it fails closed rather than passing.
- **PENDING**: class not yet decided; listed, and unchanged until it is.
- **SPLIT**: one step that carries both a blocking and an advisory part,
  described in its row.

| Step | Class | Why |
|---|---|---|
| Resolve central runtime class | PLUMBING | Selects the evaluation profile |
| Refuse a pull-request full-suite fallback | BLOCKING_CORRECTNESS | A pull request must be judged on its own event, never a substitute |
| Checkout immutable event revision | PLUMBING | Fixes the evaluated revision |
| Report toolchain alignment (advisory) | HOUSEKEEPING | Tool-version drift; a consumer finding never fails the job, only an unreadable Core lock does |
| Validate native push event context | BLOCKING_CORRECTNESS | A push evaluation must name the revision it judges |
| Resolve optional consumer metadata | PLUMBING | |
| Resolve central governance | PLUMBING | |
| Validate consumer waiver pointers | BLOCKING_CORRECTNESS | A consumer cannot waive what Core did not make waivable |
| Resolve artifact name | PLUMBING | |
| Provision immutable SDK | PLUMBING | |
| Detect repository language (SDK) | PLUMBING | |
| Inventory repository capabilities (SDK) | PLUMBING | |
| Plan required instances (Core) | PLUMBING | |
| Install Biome (digest-locked central pin) | PLUMBING | |
| Run Biome check (blocking) | SPLIT | Lint is BLOCKING_CORRECTNESS, formatting is HOUSEKEEPING (CTO, 2026-11-21). `run-biome-check` takes `checks: format` or `lint` from this revision on; org-ci runs them as two steps once its pin reaches this revision (§16, step two) |
| Route apt package archives to the verification cache | PLUMBING | Cache errors only warn |
| Restore cached apt package archives | PLUMBING | |
| Run repository verification contract | PLUMBING | Records the result; the enforce step below decides |
| Inventory apt package archives | PLUMBING | |
| Save apt package archives | PLUMBING | |
| Resolve the baseline commit (CENTRAL-DIFF) | PLUMBING | Names the commit findings are classified against (SDK ADR 0023); an unresolvable baseline fails closed in the SDK, so the gate is INCOMPLETE, never PASS |
| Set up locked Python for the central toolchain | PLUMBING | |
| Install Semgrep (hash-locked central pin) | PLUMBING | |
| Run + normalize Semgrep (SDK) | PLUMBING | Findings are judged by the technical gate |
| Validate canonical bundle | BLOCKING_CORRECTNESS | A malformed result bundle is not a result |
| Evaluate SDK technical gate | PLUMBING | Records the gate status; the enforce step decides |
| Run every planned instance (SDK) | PLUMBING | An instance that cannot conclude is reported incomplete |
| Aggregate required instances (Core) | PLUMBING | Records pass, fail or incomplete for every required instance; the enforce step decides |
| Report CENTRAL-DIFF pre-existing findings (advisory) | HOUSEKEEPING | Warns with the SDK's new / pre-existing counts; it changes no verdict, only new blocking findings and incomplete results fail through the gate |
| Project agent-review payload | PLUMBING | |
| Project SARIF (SDK-owned) | PLUMBING | |
| Route artifacts | PLUMBING | |
| Stage technical gate result | PLUMBING | |
| Build artifact manifest | PLUMBING | |
| Upload analysis artifact set | PLUMBING | |
| Create artifact handoff descriptor | PLUMBING | |
| Write central CI summary | PLUMBING | |
| Report toolchain alignment drift (advisory) | HOUSEKEEPING | One `::warning` per drifting declaration and a summary line; runs with `always()` and has no failing exit (#302) |
| Enforce repository verification | BLOCKING_CORRECTNESS | See the repository verification table |
| Enforce central mode on SDK technical gate | BLOCKING_SECURITY | Semgrep across every required instance in blocking mode; incomplete fails |

## Repository verification (`run-repository-verification`)

| Check | Class | Why |
|---|---|---|
| Contract present and valid (V2 schema, facade bytes, `Repo.mk` ABI) | BLOCKING_CORRECTNESS | Without it the phases below are not the repository's declared execution |
| Unresolved merge-conflict blocks (#300) | BLOCKING_CORRECTNESS | Operator: stays blocking |
| `make setup`, `validate`, `check`, `test` | BLOCKING_CORRECTNESS | Whole phases for this campaign (CTO, 2026-11-21). Core cannot separate a consumer's formatting check from its lint and type checks inside `check`; see Backlog |
| Core-stamp: a Core-owned file modified or removed locally | BLOCKING_CORRECTNESS | Integrity: its bytes match neither Core nor the digest the record holds (`tools/l9_repo/stamp.py` `classify_stamp`) |
| Core-stamp: a Core-owned file merely behind Core | HOUSEKEEPING | Drift: the file still carries the bytes it was stamped with, Core added a file after the stamp, or the record is stale; reported as `::warning`, never fails |
| Infrastructure failure (registry unreadable, tool unavailable) | BLOCKING_CORRECTNESS | INCOMPLETE never passes |

## Backlog (rollout campaign)

- **Split formatting out of `make check`.** Making a consumer's formatting
  check advisory needs a Core-defined phase (for example `make format-check`)
  in the `l9.repo-execution/v2` contract, the generated facade and every
  consumer's `Repo.mk`: a contract change, out of this campaign (CTO,
  2026-11-21).
- **org-ci step two.** Advance org-ci's Core pins to this revision, run Biome
  as `checks: lint` (blocking) and `checks: format` (advisory), and pick up
  the Core-stamp split in repository verification.
