# gitleaks, pip-audit and npm-audit are checks of their own; bandit is nightly (2026-11-21)

A decision record, kept outside `docs/adr/` like
[`semgrep-runs-nightly.md`](semgrep-runs-nightly.md) and
[`biome-check.md`](biome-check.md).

## Status

Accepted 2026-11-21, operator decisions: "same approach for all tools". The
security scan leaves the required `analyze` job. Each tool becomes its own
org-CI check, judging incoming code only and always reporting; every tool runs
on the whole default branch nightly; modes are unchanged. Bandit: option A,
nightly only and advisory, no pull-request check. GitGuardian is being
disabled; no Core workflow, test or document expects a GitGuardian check (its
only mention is ADR 0002's context), so nothing in Core changes for it.

**Decision owner:** `Quantum-L9/l9-ci-core`. **Related:**
[`blocking-check-classification.md`](blocking-check-classification.md),
[`nightly-findings-sink.md`](nightly-findings-sink.md), AGENTS.md §40
(promotion canary), §47, §48.

## Decision

| Check (`org-ci.yml` job) | On a pull request / merge group / push | Nightly (`nightly-sink.yml`) | Mode |
|---|---|---|---|
| `gitleaks` | the commits the event adds: `base..head` (pull request, merge group), `before..after` (push; a first push: its head commit); every other event the whole history | the whole tree of the default branch (`gitleaks dir`; the sink's checkout is shallow, and a secret still in the code is the one to fix), the check's own step with findings written as SARIF (`l9-central/gitleaks/`) and counted by the ratchet instead of failing the step | blocking |
| `pip-audit` | the full resolved lock, only when the change touches a dependency manifest or lock; otherwise a success that says "dependencies unchanged"; no Python project: "not applicable" | the full resolved lock, every repository, ratcheted | blocking |
| `npm-audit` | opt-in, as before (`L9_NPM_AUDIT`, off): "not applicable: npm audit is opt-in and not enabled". Enabled: `npm audit --audit-level=high` only when `package.json` / `package-lock.json` change, otherwise "dependencies unchanged" | (not run while opt-in is off) | opt-in, blocking when enabled |
| (none) bandit | never | bandit 1.8.6 on the whole default branch, every severity and confidence, tests excluded at any depth (`*/tests/*`, `*/test_*.py`); SARIF to code scanning (`l9-central/bandit/`), listed in the rolling issue and never counted; the full JSON report kept as the `bandit-<repository>` artifact (30 days) | advisory |

`analyze` no longer `needs` a security job and has no "Enforce security scan"
step; its verdict cannot depend on a security tool. Each check is a step job
with `contents: read` only, no secrets, and the same event and ref gate as
`analyze`. `security.yml` stays as the v1 compatibility kernel for its own
callers (`self-security.yml`); `org-ci.yml` no longer calls it.

### Signal

- **gitleaks** annotates each finding at its file and line with the rule and
  the commit, and lists the first 20 in the job summary. The report is
  `--redact`ed and the summary reads only `File`, `StartLine`, `RuleID` and
  `Commit`, so a secret's value never reaches the log or the summary.
- **pip-audit** annotates each known vulnerability at the audited lock file
  (`uv.lock` or `requirements.txt`) by package, version and advisory id, with
  a summary table. An export or lock failure is INCOMPLETE and fails.
- **npm-audit** annotates `package-lock.json` (or `package.json` when the lock
  is missing).
- Every check writes one summary line stating its result: pass, findings, not
  applicable, or dependencies unchanged.

### Canary

The promotion canary runs every `org-ci.yml` step job as a sibling of each leg
(§48), so `gitleaks`, `pip-audit`, `npm-audit` and `Biome` are each part of the
leg record: a Core change that turns one of them red on a canary repository is
a regression.

## Requiredness (operator action)

Making the checks required is an organization ruleset setting, operator-only.
GitHub offers a check only after it has reported at least once, so each job
must reach Core `main` and run once on a governed repository first.

GitHub → Quantum-L9 organization → Settings → Rules → Rulesets →
`l9-canonical-ci-required` (id 21895545) → the rule "Require status checks to
pass" → Add checks → type `gitleaks` → select it → Add checks → `pip-audit` →
select it → Add checks → `npm-audit` → select it → Save changes.

`npm-audit` reports a success while opt-in is off, so requiring it blocks
nothing until Core enables it. Until a check is listed, it reports on every
pull request but does not block a merge by itself.

## Consequences

- A pull request that changes no dependency manifest no longer installs
  Python or pip-audit at all; a pull request is never judged on secrets or
  vulnerabilities it did not bring in.
- Bandit findings never block a merge. They reach code scanning and the
  nightly issue, and the JSON artifact allows comparison with SonarCloud
  before bandit is promoted or retired.
- `tests/workflows/test_org_ci_security_job.py` is replaced by
  `tests/workflows/test_org_ci_security_checks.py`, which runs each check's
  own scripts against real repositories and fake reports.
