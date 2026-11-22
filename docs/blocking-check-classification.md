# What Core may block a consumer merge on

Operator policy (2026-11-20): only checks that protect working or secure code
may block a pull request merge. Housekeeping never blocks. Secret scanning and
conflict markers stay blocking, and an INCOMPLETE result never passes.

Every step of the one required job, `org-ci.yml` `analyze` (its check name is
`.l9/org-runtime-contract.yaml` `entrypoint.required_check.name`), is listed
below with its class. `tests/workflows/test_blocking_check_classification.py`
fails when a step is added, renamed or removed without a row here.

Semgrep is not among them. Operator decision 2026-11-21
([decision record](semgrep-runs-nightly.md), superseding row 23 for
Semgrep): Semgrep no longer judges pull requests, merge groups or pushes. Its
chain, with every planned instance, the SDK technical gate and its artifacts,
moved to `central-semgrep.yml`, which the nightly findings sink runs on each
repository's default branch; its findings go to code scanning and the rolling
nightly issue, never to a merge verdict.

Neither is the security scan. Operator decision 2026-11-21
([security-checks.md](security-checks.md)): `analyze` no longer waits for or
enforces a security job. gitleaks, pip-audit and npm-audit are checks of their
own (sections below), and bandit runs only in the nightly sink, advisory.

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
| Judge pull-request test expense | BLOCKING_CORRECTNESS | A pull request is judged on the tests it would run; an unreadable guard or a non-literal suite blocks |
| Checkout immutable event revision | PLUMBING | Fixes the evaluated revision |
| Report toolchain alignment (advisory) | HOUSEKEEPING | Tool-version drift; a consumer finding never fails the job, only an unreadable Core lock does |
| Validate native push event context | BLOCKING_CORRECTNESS | A push evaluation must name the revision it judges |
| Resolve optional consumer metadata | PLUMBING | |
| Resolve central governance | PLUMBING | |
| Validate consumer waiver pointers | BLOCKING_CORRECTNESS | A consumer cannot waive what Core did not make waivable |
| Route apt package archives to the verification cache | PLUMBING | Cache errors only warn |
| Restore cached apt package archives | PLUMBING | |
| Run repository verification contract | PLUMBING | Records the result; the enforce step below decides |
| Inventory apt package archives | PLUMBING | |
| Save apt package archives | PLUMBING | |
| Write central CI summary | PLUMBING | |
| Report toolchain alignment drift (advisory) | HOUSEKEEPING | One `::warning` per drifting declaration and a summary line; runs with `always()` and has no failing exit (#302) |
| Enforce repository verification | BLOCKING_CORRECTNESS | See the repository verification table |

## Repository verification (`run-repository-verification`)

| Check | Class | Why |
|---|---|---|
| Contract present and valid (V2 schema, facade bytes, `Repo.mk` ABI) | BLOCKING_CORRECTNESS | Without it the phases below are not the repository's declared execution |
| Unresolved merge-conflict blocks (#300) | BLOCKING_CORRECTNESS | Operator: stays blocking |
| `make setup`, `validate`, `check`, `test` | BLOCKING_CORRECTNESS | Whole phases for this campaign (CTO, 2026-11-21). Core cannot separate a consumer's formatting check from its lint and type checks inside `check`; see Backlog |
| Core-stamp: a Core-owned file modified or removed locally | BLOCKING_CORRECTNESS | Integrity: its bytes match neither Core nor the digest the record holds (`tools/l9_repo/stamp.py` `classify_stamp`) |
| Core-stamp: a Core-owned file merely behind Core | HOUSEKEEPING | Drift: the file still carries the bytes it was stamped with, Core added a file after the stamp, or the record is stale; reported as `::warning`, never fails |
| Infrastructure failure (registry unreadable, tool unavailable) | BLOCKING_CORRECTNESS | INCOMPLETE never passes |

## Biome (`org-ci.yml` job `biome`, the `Biome` check)

Operator decision 2026-11-21 ([biome-check.md](biome-check.md)): Biome is its
own check beside `analyze`, blocking, and meant to be required by the
organization ruleset. It always reports: a repository without a Biome
configuration, and a change that touches no file in Biome's scope, both
succeed and say so.

| Step | Class | Why |
|---|---|---|
| Checkout immutable event revision | PLUMBING | Fixes the evaluated revision |
| Resolve Biome's scope | PLUMBING | Names the files the event changes (pull request: against its base; merge group: against the group base; push: against the previous revision) or the whole tree; an unresolvable base fails the step, never widens or narrows the scope silently |
| Install Biome (digest-locked central pin) | PLUMBING | Only when there is something to judge |
| Run Biome check (blocking) | BLOCKING_CORRECTNESS | `biome ci` lint and format of the changed files; findings are annotated at file and line and fail the job; an infrastructure failure is never a pass. Splitting formatting out as housekeeping stays in the backlog below |

## gitleaks (`org-ci.yml` job `gitleaks`, the `gitleaks` check)

Blocking. Always reports. Incoming code only: the commits the event adds.

| Step | Class | Why |
|---|---|---|
| Checkout (full history) | PLUMBING | The commit range must resolve in the clone |
| Secret scan (gitleaks) | BLOCKING_SECURITY | gitleaks 8.24.3 (checksum-pinned) over `base..head` of a pull request or merge group, `before..after` of a push, the whole history otherwise; the Core preset plus the repository's `.gitleaks.toml`; each finding annotated at file and line by rule and commit, its value never printed; an unresolvable range or a scanner error fails as INCOMPLETE |

## pip-audit (`org-ci.yml` job `pip-audit`, the `pip-audit` check)

Blocking. Always reports. Runs only when a dependency manifest or lock changes.

| Step | Class | Why |
|---|---|---|
| Checkout (full history) | PLUMBING | |
| Detect project languages | PLUMBING | No Python project: success, "not applicable: no Python project" |
| Set up Python | PLUMBING | |
| Python dependency audit (pip-audit) | PLUMBING | A change that touches no manifest or lock ends here as "dependencies unchanged"; otherwise the full resolved lock is audited to a JSON report; an export or lock failure fails as INCOMPLETE |
| Judge the dependency audit | BLOCKING_SECURITY | A known vulnerability fails, each annotated at the lock file by package and advisory; an unreadable report fails |

## npm-audit (`org-ci.yml` job `npm-audit`, the `npm-audit` check)

Opt-in, as before the split (`L9_NPM_AUDIT`, off): until Core enables it, the
check succeeds with "not applicable: npm audit is opt-in and not enabled".

| Step | Class | Why |
|---|---|---|
| Checkout (full history) | PLUMBING | |
| Node dependency audit (npm audit) | BLOCKING_SECURITY | When enabled: no `package.json` is not applicable; unchanged `package.json` / `package-lock.json` passes as "dependencies unchanged"; otherwise `npm audit --audit-level=high` fails on a high or critical advisory, and a manifest without a lock fails named |

## CodeQL (`org-ci.yml` job `codeql`, the `CodeQL` check)

Blocking at error / high. Always reports. Only what the event introduces
([codeql-check.md](codeql-check.md)).

| Step | Class | Why |
|---|---|---|
| Checkout (full history) | PLUMBING | The base must be in the clone |
| Resolve CodeQL's languages and base | PLUMBING | `actions` for workflow or action files, and each build-mode-none language by its files; the event's base, or none; a base outside the clone fails as INCOMPLETE |
| Install CodeQL (checksum-pinned bundle) | PLUMBING | |
| Analyze with CodeQL (security-extended) | PLUMBING | Head and base, build-mode none; a language with no source on one side has no results there; any other CodeQL failure fails as INCOMPLETE |
| Judge new CodeQL results (blocking at error / high) | BLOCKING_SECURITY | A result the base does not have (rule, path, CodeQL line fingerprint) at level error or security severity 7.0+ fails, annotated at file and line; other new results are warnings; no base: nothing is new |

## actionlint (`org-ci.yml` job `actionlint`, the `actionlint` check)

Blocking. Always reports. The workflow files the event changes
([actionlint-check.md](actionlint-check.md)).

| Step | Class | Why |
|---|---|---|
| Checkout (full history) | PLUMBING | The base must be in the clone |
| Resolve actionlint's scope | PLUMBING | The changed workflow files, every workflow when an action definition changed or there is no base; none changed is a pass that says so, no workflow files is not applicable; an unresolvable base fails as INCOMPLETE |
| Install actionlint (toolchain-locked) | PLUMBING | |
| Run actionlint (blocking) | BLOCKING_CORRECTNESS | A finding in a changed file fails, annotated at file, line and column; with no base (dispatch, canary) findings are warnings; an actionlint failure is never a pass |

## bandit (nightly only)

Advisory, not a pull-request check: `nightly-sink.yml` runs bandit 1.8.6 on the
whole default branch (tests excluded at any depth), sends its SARIF to code
scanning (`l9-central/bandit/`, listed in the rolling issue and never counted
by the ratchet) and keeps the full JSON report as an artifact.

## Backlog (rollout campaign)

- **Split formatting out of `make check`.** Making a consumer's formatting
  check advisory needs a Core-defined phase (for example `make format-check`)
  in the `l9.repo-execution/v2` contract, the generated facade and every
  consumer's `Repo.mk`: a contract change, out of this campaign (CTO,
  2026-11-21).
- **org-ci step two.** Advance org-ci's Core pins to this revision, run Biome
  as `checks: lint` (blocking) and `checks: format` (advisory), and pick up
  the Core-stamp split in repository verification.
