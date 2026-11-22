# Biome is its own check, judging incoming code only (2026-11-21)

A decision record, kept outside `docs/adr/` like
[`semgrep-runs-nightly.md`](semgrep-runs-nightly.md).

## Status

Accepted 2026-11-21, operator decision: Biome leaves the required `analyze`
job and becomes its own org-CI check named `Biome`, blocking and intended to
be required by the organization ruleset; pull requests are judged on incoming
code only; the whole codebase is judged nightly.

**Decision owner:** `Quantum-L9/l9-ci-core`. **Related:**
[`blocking-check-classification.md`](blocking-check-classification.md)
(Biome section), [`nightly-findings-sink.md`](nightly-findings-sink.md),
AGENTS.md §16 (Biome activation), §40 (promotion canary).

## Decision

| Where | What Biome judges | Outcome |
|---|---|---|
| `org-ci.yml` job `biome` (`Biome`), on a pull request | the files the pull request changes, against its base | a finding fails the check, annotated at file and line |
| … on a merge group | the files the group changes, against `merge_group.base_sha` | same |
| … on a default-branch push | the files the push changes, against `before` (a first push: the whole tree) | same |
| … on any other event (dispatch, canary) | the whole tree | same |
| `nightly-sink.yml` | the whole default branch, every repository the sink covers | advisory: findings go to code scanning (`l9-central/biome/`) and the rolling "Nightly findings" issue |

The check always reports. A repository without `biome.json` / `biome.jsonc`
succeeds with "not applicable: no biome configuration"; a change that touches
no file Biome handles succeeds with "no changed files in Biome's scope".
Neither installs Biome. An unresolvable base fails the check by name; it never
widens or narrows the scope silently.

### How the changed files reach Biome

Biome 2.5.8 (the version in Core's toolchain lock), from the locked binary's
own `biome ci --help` and the official guide
<https://biomejs.dev/guides/integrate-in-vcs/> ("Process only changed files"):

- `biome ci --changed --since=REF` "processes only files changed by commits
  since the merge base between REF and HEAD"; it needs `vcs.enabled` and
  `vcs.clientKind`, which the CLI can set (`--vcs-enabled=true
  --vcs-client-kind=git`) without the repository's configuration.
- `biome ci [PATH]...` judges exactly the given paths, still applying the
  repository's `files.includes`; `--no-errors-on-unmatched` makes a path
  Biome does not handle a no-op.

Core uses the second form. `--changed` needs the merge base in local history,
which a shallow CI checkout cannot promise; Core fetches the event's base and
takes `git diff --name-only --diff-filter=d <base> <revision>`, which names the
same files exactly (a pull request's revision is GitHub's merge commit, so the
diff against its base is the pull request's change), and passes them as
paths. Verified with the locked binary: a broken file the change does not
touch is not judged; a broken changed file fails with
`::error file=<path>,line=<n>` annotations.

### Signal

Biome's own GitHub reporter annotates every diagnostic at its file and line.
The job summary states the mode, the scope, the files judged, the error and
warning counts, and the first 20 errors (file, line, rule, message); the
failing exit is preceded by an `::error` naming the count and where to look.

### Requiredness (operator action)

Making `Biome` required is an organization ruleset setting, operator-only:
GitHub → Quantum-L9 organization → Settings → Rules → Rulesets →
`l9-canonical-ci-required` (id 21895545) → in the rule that already lists
`.l9/org-runtime-contract.yaml`'s required check ("Require status checks to
pass") → Add checks → type `Biome` → select it → Save changes. GitHub offers a check only after it has
reported at least once, so the job must reach Core `main` and run once on a
governed repository first. Until then, `Biome` reports on every pull request
but does not block a merge by itself.

## Consequences

- `analyze` no longer installs or runs Biome; its summary points at the
  `Biome` check.
- The promotion canary runs org-ci.yml's `biome` job as a sibling of each leg
  and folds its result into the leg record (`checks`), so a Core change that
  turns `Biome` red on a canary repository is a regression.
- `run-biome-check` is no longer used by any Core workflow; the `Biome` job
  runs `biome ci` itself so it can pass the changed paths.
