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
- **PENDING**: class not yet decided by the CTO; listed, and unchanged until
  it is.

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
| Run Biome check (blocking) | PENDING | Biome check combines formatting (housekeeping) with lint (correctness); proposed split: format advisory, lint blocking |
| Route apt package archives to the verification cache | PLUMBING | Cache errors only warn |
| Restore cached apt package archives | PLUMBING | |
| Run repository verification contract | PLUMBING | Records the result; the enforce step below decides |
| Inventory apt package archives | PLUMBING | |
| Save apt package archives | PLUMBING | |
| Set up locked Python for the central toolchain | PLUMBING | |
| Install Semgrep (hash-locked central pin) | PLUMBING | |
| Run + normalize Semgrep (SDK) | PLUMBING | Findings are judged by the technical gate |
| Validate canonical bundle | BLOCKING_CORRECTNESS | A malformed result bundle is not a result |
| Evaluate SDK technical gate | PLUMBING | Records the gate status; the enforce step decides |
| Run every planned instance (SDK) | PLUMBING | An instance that cannot conclude is reported incomplete |
| Aggregate required instances (Core) | PLUMBING | Records pass, fail or incomplete for every required instance; the enforce step decides |
| Project agent-review payload | PLUMBING | |
| Project SARIF (SDK-owned) | PLUMBING | |
| Route artifacts | PLUMBING | |
| Stage technical gate result | PLUMBING | |
| Build artifact manifest | PLUMBING | |
| Upload analysis artifact set | PLUMBING | |
| Create artifact handoff descriptor | PLUMBING | |
| Write central CI summary | PLUMBING | |
| Enforce repository verification | BLOCKING_CORRECTNESS | See the repository verification table |
| Enforce central mode on SDK technical gate | BLOCKING_SECURITY | Semgrep across every required instance in blocking mode; incomplete fails |

## Repository verification (`run-repository-verification`)

| Check | Class | Why |
|---|---|---|
| Contract present and valid (V2 schema, facade bytes, `Repo.mk` ABI) | BLOCKING_CORRECTNESS | Without it the phases below are not the repository's declared execution |
| Unresolved merge-conflict blocks (#300) | BLOCKING_CORRECTNESS | Operator: stays blocking |
| `make setup`, `validate`, `check`, `test` | PENDING | Core runs each phase whole, so it cannot separate a consumer's formatting check from its lint and type checks inside `check`; making formatting advisory needs a Core-defined phase split, a contract change |
| Core-stamp byte drift (`.l9/core-stamp.json`) | PENDING | Stale Core-owned files are drift (housekeeping), but the record is the repository's own claim that the files are Core's (integrity) |
| Infrastructure failure (registry unreadable, tool unavailable) | BLOCKING_CORRECTNESS | INCOMPLETE never passes |
