# actionlint is one org-wide check, judging changed workflows (2026-11-21)

A decision record, kept outside `docs/adr/` like
[`codeql-check.md`](codeql-check.md).

## Status

Accepted 2026-11-21, operator decision: one org-wide `actionlint` check, on
the workflow and action files a pull request changes and on every file
nightly, blocking, with its version and checksum from Core's toolchain lock.

**Decision owner:** `Quantum-L9/l9-ci-core`. **Related:**
[`blocking-check-classification.md`](blocking-check-classification.md),
[`nightly-findings-sink.md`](nightly-findings-sink.md),
[`codeql-check.md`](codeql-check.md) (overlap below), AGENTS.md §48–§50.

## Decision

| Where | What actionlint judges | Outcome |
|---|---|---|
| `org-ci.yml` job `actionlint` (`actionlint`), pull request / merge group / push | the workflow files (`.github/workflows/*.yml` / `*.yaml`) the event changes, against its base; a changed action definition (`action.yml` / `action.yaml` anywhere) re-judges every workflow, since actionlint checks a workflow's use of local actions through them | a finding fails the check, annotated at file, line and column |
| … any other event (dispatch, the promotion canary): no base | every workflow | reported as warnings, never fails: the event brings nothing in |
| `nightly-sink.yml` | every workflow of the default branch | findings to code scanning (`l9-central/actionlint/`), counted by the ratchet |

It always reports: no workflow files is "not applicable: no workflow files";
a change that touches none is "pass: no changed workflow or action files".
An unresolvable base or an actionlint failure (exit above 1) fails as
INCOMPLETE.

- **Pin.** `toolchain-lock.json` `actionlint` (1.7.12, released 2026-03-31)
  and `install-consumer-ci/locks/actionlint-1.7.12.json` (linux-x64 asset
  sha256 `8aca8db9…49a3d8`, from the release's own checksums file). The job
  carries both as literals and `tests/workflows/test_org_ci_actionlint_job.py`
  binds them to the lock.
- **Integrations off.** actionlint runs shellcheck and pyflakes on `run:`
  scripts when they are on `PATH`; the runner image's copies are not pinned,
  so findings would change with the image. Both are disabled
  (`-shellcheck= -pyflakes=`). Pinning shellcheck is a separate decision.
- **Configuration.** A repository's own `.github/actionlint.yaml` applies.
  Core's ignores one false positive: `job.workflow_sha`, a documented job
  context property that actionlint 1.7.12's context type lacks, in
  `settings-runner.yml` only.
- **Core's own workflows are clean.** The first run found 31 expression
  errors in Core's generated workflows: the promotion canary and the nightly
  sink inlined steps that read `inputs.*` they do not declare, and the
  canary's leg record read a step id its leg does not have. The renderers now
  write an undeclared input's default and read only the leg's own steps
  (`undeclared_defaults`, tested).

## Overlap with CodeQL `actions`

CodeQL's `actions` language ([codeql-check.md](codeql-check.md)) and
actionlint both read workflows, for different questions. CodeQL
(`security-extended`) is a dataflow security analysis: untrusted input
reaching a `run:` script or an expression (code injection), untrusted
checkouts in privileged workflows, artifact poisoning, excessive token
permissions. actionlint is a structural checker: workflow syntax, expression
types and undefined properties, job and step references, `needs` graphs,
runner labels, action inputs, cron syntax. They overlap on script injection:
actionlint flags an untrusted `${{ github.event.* }}` used directly in
`run:`, which CodeQL's `actions/code-injection` also reports, with dataflow.
CodeQL judges only what a change introduces; actionlint judges whole changed
files. Both stay: neither covers the other's main question.

## Other invocations (per repository, code search, default branches)

| Repository | Where | What |
|---|---|---|
| `.github` | `.github/workflows/actionlint.yml` | its own actionlint workflow |
| `l9-ci-sdk` | `.github/workflows/l9-yaml-governance.yml`, `l9-yaml-governance-dogfood.yml`; `docs/templates/l9-yaml-governance-caller.yml` | the SDK's reusable YAML-governance workflow and its dogfood caller |
| `Website-Bot` | `.github/workflows/emit-handoff.yml` | a mention in a workflow |
| `Cursor-Governance` | `ops/ci_parity/` (`tools.yaml`, `run.py`, `findings.py`), `environment/agents/adapters/claude-code/hooks/ci_parity_posttool.py`, `…/web/setup.sh` | local CI-parity hooks and setup |
| `IdeaOS`, `Mack-5.0`, `Website-Bot`, `l9-dependency-template`, `l9-observability-core`, `l9-repo-template`, `l9-state` | `CONTRIBUTING.md` | documentation |

The two workflow copies (`.github`, `l9-ci-sdk`) become redundant once
`actionlint` is required; retiring them is each repository's change.

## Requiredness (operator action)

GitHub → Quantum-L9 → Settings → Rules → Rulesets → `l9-canonical-ci-required`
(21895545) → "Require status checks to pass" → Add checks → `actionlint` →
Save changes, once the check has reported on a governed repository.
