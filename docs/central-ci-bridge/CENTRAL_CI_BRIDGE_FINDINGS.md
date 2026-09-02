# Central CI bridge — findings

Evidence-backed state of the Quantum-L9 transition from copied consumer-owned
L9 CI to centrally enforced organization CI.

**Inspected:** 2026-09-02. Every claim below is either a recorded observation or
is explicitly labelled `INFERENCE` / `UNKNOWN`.

## 1. Headline: the bridge is already crossed, and it is red

The primary gap hypothesis — "the organization required-workflow ruleset is not
yet proven live and canonical" — is **falsified**. The ruleset is live, active,
and correctly targets Core. The migration is blocked one step later: central CI
**runs and fails** on every Python repository, so no consumer may safely delete
its legacy caller yet.

The cause is now confirmed from a real central run (§4.3): central CI scans the
SDK runtime it provisions into `.l9/runtime/sdk`, so the CI toolchain's own
third-party dependencies are analyzed as product code. 51 of 52 unresolved
findings are in `site-packages`.

## 2. Exact heads inspected

| Repository | Default branch | HEAD inspected |
|---|---|---|
| `Quantum-L9/l9-ci-core` | `main` | `aaa01124d95b8fc51369636116e71aedf2f7389f` |
| `Quantum-L9/l9-ci-sdk` | `main` | `7d7762eae5e1a12fdc66276975e2949891762a20` |
| `Quantum-L9/l9-ci-debt-intelligence` | `main` | `249a46bf86ba90f56ae76b11d079e3ec3bfa1b57` |
| `Quantum-L9/l9-ci-debt-lsp` | `main` | `86620601d17d27ebf91c67e8f0be67a81eecc3b6` |
| `Quantum-L9/l9-ci-debt-resolver` | `main` | `ae40d6f76672abb4486aeac1fe12e96daddb48a3` |

`Quantum-L9/.github` could **not** be inspected — see §7.

## 3. Live organization enforcement (verified)

Read back from GitHub, not from checked-in JSON.

Organization ruleset **`L9 canonical CI required`** (id `21895545`,
`source_type: Organization`, `enforcement: active`, updated
`2026-09-01T18:59:36Z`):

```jsonc
conditions.ref_name.include = ["~DEFAULT_BRANCH"]
rules = [
  { "type": "workflows", "parameters": { "do_not_enforce_on_create": true,
      "workflows": [ { "repository_id": 1285564308,
                       "path": ".github/workflows/org-ci.yml",
                       "ref": "refs/heads/main" } ] } },
  { "type": "required_status_checks", "parameters": {
      "required_status_checks": [ { "context": "Analyze (central Core)" } ] } }
]
```

`repository_id 1285564308` is confirmed to be `Quantum-L9/l9-ci-core`
(`gh api repos/Quantum-L9/l9-ci-core --jq .id`).

**Projection probe.** The ruleset was confirmed applied to all 11 repositories
reachable from this session, each reporting `enforcement=active`:
`l9-ci-core`, `l9-ci-sdk`, `l9-ci-debt-intelligence`, `l9-ci-debt-lsp`,
`l9-ci-debt-resolver`, `l9-repo-template`, `Enrichment.Inference.Engine`,
`Gate_SDK`, `Constellation.Gate`, `l9-cognitive-runtime`, `Cursor-Governance`.

Method (reusable — the org-level API is unavailable to scoped sessions, but the
per-repository projection is not):

```bash
gh api repos/Quantum-L9/<repo>/rulesets \
  --jq '.[] | select(.source_type=="Organization")'
```

### 3.1 Contract violation: the enforcement pin is a branch, not a SHA

`ref: refs/heads/main` is a mutable branch pin. The contract requires an
immutable verified Core commit SHA and explicitly forbids `@main` enforcement.
Every consumer's required check therefore silently re-targets whatever Core
`main` points at. Fixing this requires a change in `Quantum-L9/.github` — see §7.

## 4. Central CI executes, and fails closed

`Organization CI (Core)` is instantiated by GitHub on real consumer pull
requests. It is **not** dormant. On every inspected run it fails:

| Repository | Run | Conclusion | Failing step |
|---|---|---|---|
| `l9-ci-debt-intelligence` | `33561406648` | failure | `Run + normalize Semgrep (SDK)` |
| `l9-ci-debt-lsp` | `33482603807` | failure | same |
| `l9-ci-debt-resolver` | `33481850033` | failure | same |

Run `33561406648` post-dates current Core `main` (`aaa0112`), so the failure is
**not** stale.

Job log, `l9-ci-debt-intelligence` job `100034515692`:

```
error[unresolved_strict_contract]: strict identity resolution failed for
findings: fn_semgrep_069caf2e…, fn_semgrep_0c55f17c…, …   (50 finding ids)
##[error]Process completed with exit code 6.
```

Resolved run context: `MODE: blocking`, `LANGUAGE: python`,
`SDK_REVISION: 7d7762ea…`, `GATE_STATUS:` (empty — the run dies *before* the
technical gate is evaluated).

### 4.1 Why the legacy caller is green and central is red

Both paths resolve `strict: true` (`resolve.py` emits the profile's own
`strict`; mode does not modulate it), so strictness is not the difference. The
governance delta that *is* real:

| `pr_fast` default mode | Value |
|---|---|
| Core `@core-defaults/rule-modes.yaml` | **`blocking`** |
| All three canary consumer packs | **`shadow`** |

The legacy `L9 Analysis` workflow additionally publishes under
`Publish analysis (Core) / shadow`, confirmed in run `33576014109`.

**A green legacy `L9 Analysis` is therefore not evidence that a repository
passes central CI.** It is a weaker gate on the same code.

### 4.2 Root cause of the strict failure

`--strict` requires every normalized finding to carry a `canonical_rule_id`
(`l9_ci/pipeline/semgrep.py`). Identity comes from either trusted
`metadata.l9.canonical_rule_id` on an L9-authored rule, or an identity-map
entry for a third-party registry rule.

Verified inputs:

* The SDK default ruleset profile `l9-standard` composes **the community
  registry ruleset (`p/python`) plus** the packaged L9 ruleset.
* The SDK's packaged identity map holds **15** rules.
* `@core-defaults` ships **six** control documents and **no** identity map;
  `org-ci.yml` never passes `identity-map:` to `invoke-sdk`, although the
  action does expose that input.
* A reviewed **151-rule** identity map exists as *copied consumer governance* in
  `l9-ci-debt-intelligence/.github/governance/semgrep-identity-map.yaml`.
  `l9-ci-debt-lsp` and `l9-ci-debt-resolver` have **none**.

The failure class is reproduced directly. Scanning the SDK's own source with
`p/python` produces a registry finding with no identity-map entry, and the
pipeline fails exactly as central CI does:

```
$ l9-ci semgrep normalize --input report.json --strict
error[unresolved_strict_contract]: … 1 finding(s) across 1 provider rule(s)
  - python.django.security.injection.command.subprocess-injection…:
      1 finding(s) (e.g. l9_ci/providers/semgrep/provider.py:145)
exit 6
```

**Conclusion (verified mechanism):** under `strict: true`, any third-party
registry rule outside the 15-entry packaged map fails central CI closed. The
identity map that would cover them is owned by *consumers*, not by Core or the
SDK — the exact copy-first ownership inversion this migration exists to remove.

### 4.3 Root cause, confirmed from a real central run

The shipped diagnostic (§8) resolved this from CI's own output on its first
failure. Central run `33583852808` on `Quantum-L9/l9-ci-sdk` reported:

```
strict identity resolution failed for 52 finding(s) across 15 provider rule(s)
  - …logger-credential-leak…: 9 finding(s)
      (e.g. .l9/runtime/sdk/venv/lib/python3.12/site-packages/pip/_internal/network/auth.py:85)
  - …weak-ssl-version…: 9 finding(s)
      (e.g. .l9/runtime/sdk/venv/lib/python3.12/site-packages/urllib3/contrib/pyopenssl.py:76)
  - …insecure-hash-algorithm-sha1: 8 finding(s)
      (e.g. .l9/runtime/sdk/venv/lib/python3.12/site-packages/pip/_vendor/requests/auth.py:205)
  …
```

**51 of the 52 findings are inside
`.l9/runtime/sdk/venv/lib/python3.12/site-packages/`** — `pip`, `urllib3`,
`cryptography`, `peewee`, `semgrep`, `httpcore`, `dotenv`, `playhouse`. Exactly
one (`…subprocess-injection`, `l9_ci/providers/semgrep/provider.py:145`) is
repository code.

**Central CI is analyzing its own toolchain.** `provision-sdk` materialises the
immutable SDK — including a virtualenv full of third-party dependencies — at
`.l9/runtime/sdk`, *inside* the repository, and `org-ci.yml` then scans that
same tree with `root: .`. Every third-party package shipped with the CI runtime
is analyzed as if it were product code. Those registry rules carry no
`metadata.l9.canonical_rule_id` and are absent from the 15-entry packaged
identity map, so `strict: true` fails the job closed.

This is not consumer-specific and not caused by any one pull request. The same
failure occurs on unrelated branches by other authors
(`fix/l9-analysis-caller-permissions`, `claude/pack-integration-remediation-d3kh7x`).

Relocating the runtime is deliberately unavailable: `provision.py` enforces
`runtime-directory must remain inside GITHUB_WORKSPACE`. The fix therefore
belongs on the analysis surface.

**Fixed in `Quantum-L9/l9-ci-sdk#85`** (`da4d228`): `semgrep run` now passes
`--exclude .l9/runtime`. The exclusion is narrower than `.l9/` on purpose —
repository-authored `.l9/` contracts stay in scope, only the provisioned
runtime subtree is skipped — and it applies to every execute request,
including `--profile l9-baseline`. `normalize` is untouched, since it imports a
report produced elsewhere and does not control the scan surface.

Measured on a tree with the runtime provisioned in place, identical except for
the flag:

| | files scanned | under `.l9/runtime` | findings |
|---|---|---|---|
| without `--exclude` | 3222 | 3130 | **62** |
| with `--exclude` | 92 | 0 | **0** |

**The fix reaches the fleet in two merges, not one — and the reason is
structural.** `0efd762…` is now *allowlisted* in `.l9/sdk-compatibility.yaml`,
but it is not yet the default and no workflow selects it. See §4.4.

Central CI on `l9-ci-sdk` is nevertheless green at `0efd762` — the first green
`Analyze (central Core)` run in the organization. That result does **not**
generalize: on that repository the fix applies regardless of the pin, because
`provision-sdk` runs `python -m l9_ci` with `PYTHONPATH={checkout}` and
`python -m` places the current working directory ahead of `PYTHONPATH`, so a
repository containing its own `l9_ci/` shadows the pinned checkout. That is a
hole in the immutable-provisioning guarantee, it affects `l9-ci-sdk` alone, and
every other repository still resolves the pin.

### 4.4 Why the pin bump takes two merges

Moving Core's SDK revision is not a one-line edit, and it cannot be completed in
a single pull request. Two mechanisms compound.

**The allowlist.** `.l9/sdk-compatibility.yaml` sets
`unlisted_revisions_allowed: false`, `branches_allowed: false` and
`floating_git_references_allowed: false`, so a revision must be *added* as a
supported entry before it can be selected at all. The revision is additionally
pinned in `provision.py` (`EXPECTED_REVISION`), the `provision-sdk` action
default, four workflow defaults, `sdk-contract-check`'s asserted equality, and
the declared topology in `.l9/architecture.yaml` and `.l9/artifact-protocol.yaml`
— nine sites that must move together, plus two tests that assert the exact SHA
and a `MANIFEST.sha256` digest for every changed tracked file.

**The action pin is the blocker.** Core's workflows invoke Core's own actions by
pinned SHA (`uses: Quantum-L9/l9-ci-core/.github/actions/provision-sdk@2aa859c8…`),
and `provision.py` resolves the allowlist *relative to its own file*:

```python
COMPATIBILITY_MANIFEST = (
    Path(__file__).resolve().parents[3] / ".l9" / "sdk-compatibility.yaml"
)
```

So the governing allowlist is the one committed at the pinned **action**
revision — not the branch, and not `main` at merge time. Flipping the workflow
defaults to `0efd762…` while the pinned action still resolves an allowlist
without it fails provisioning outright:

```
provision-sdk: sdk-revision is not listed in .l9/sdk-compatibility.yaml
```

That is observed, not predicted: it is why `Analyze (semgrep -> SDK)` failed on
this branch when all nine sites were moved at once. Merging that state would
have broken provisioning on **every** repository in the organization — a harder
failure than the strict-identity one it was meant to fix.

`2aa859c8…` is also **not an ancestor of `main`** (it is a pull-request head;
`1abce35` is its squash on `main`), so "just bump the action pin too" is not a
mechanical step — it moves the organization from one action-code line to
another.

**The safe sequence:**

1. **This PR** — allowlist `0efd762…` only. No default moves, no workflow
   selects it, so behavior is unchanged and the merge is safe.
2. **Follow-up** — once step 1 is on `main`, pin the Core actions to that `main`
   commit and flip the nine `sdk-revision` sites together. The pinned action then
   resolves an allowlist that contains the revision, and the fleet gets the fix.

Doing it in this order avoids pinning the organization to any unmerged commit.

#### A correction, recorded deliberately

An earlier revision of this document reported that the failure "was NOT
reproduced locally" across five attempts. That statement was wrong, and the
reason matters: the local attempts invoked `l9-ci semgrep run` with an absolute
`--raw-output` outside `--root`, which silently produced **no raw report and an
empty finding bundle** (~827 bytes). Every "exit 0" recorded there was a scan
that never examined anything.

A direct `semgrep scan --config p/python .` over the identical tree scans
**3130 files under `.l9/runtime`** and returns **62 findings**, matching CI.
The negative results were an artifact of the reproduction harness, not evidence
about the system. They are documented here so the mistake is not repeated: an
exit code is not proof a scan ran — verify the scanned-path count.

## 5. Dependabot swarm (measured)

Open Dependabot pull requests whose head branch targets first-party
`l9-ci-core` / `l9-ci-sdk` references:

| Repository | Open PRs | Dependabot | First-party CI pins |
|---|---|---|---|
| `l9-ci-debt-intelligence` | 12 | 10 | **4** |
| `l9-ci-debt-lsp` | 8 | 7 | **5** |
| `l9-ci-debt-resolver` | 7 | 6 | **5** |
| `Cursor-Governance` | 17 | 5 | **5** |
| **Total** | | | **19** |

Representative branch shape — one PR per Core action, per consumer, per bump:

```
dependabot/github_actions/Quantum-L9/l9-ci-core/dot-github/actions/resolve-governance-aaa0112…
dependabot/github_actions/Quantum-L9/l9-ci-core/dot-github/actions/provision-sdk-aaa0112…
dependabot/github_actions/Quantum-L9/l9-ci-core/dot-github/actions/validate-bundle-aaa0112…
dependabot/github_actions/Quantum-L9/l9-ci-core/dot-github/workflows/publish-analysis.yml-aaa0112…
```

See `DEPENDABOT_SWARM_BEFORE_AFTER.md`.

## 6. Ownership graph as found

```
GitHub org ruleset "L9 canonical CI required"  (active, all 11 probed repos)
  └─ ref refs/heads/main            ← VIOLATION: mutable pin, must be a SHA
     └─ l9-ci-core/.github/workflows/org-ci.yml
        ├─ @core-defaults  (6 docs, pr_fast = blocking, NO identity map)
        ├─ SDK pinned 7d7762ea…      (immutable — correct)
        └─ consumer contents

Consumers ALSO still own, in parallel:
  .github/workflows/l9-analysis.yml   (pr_fast = shadow — a weaker gate)
  .github/governance/**               (incl. the 151-rule identity map)
  8–9 first-party l9-ci-core action SHA pins each  → the Dependabot swarm
```

## 7. Blockers

1. **`Quantum-L9/.github` is unreachable from this session.** It cannot be
   cloned (a leading-dot repository name would collide with configuration
   directories) and is not attachable via `add_repo`. The ruleset SHA-pin repair
   (§3.1), ruleset apply/read-back verifier, and custom-property semantics all
   live there and are **not** delivered. `UNKNOWN`: the checked-in desired-state
   JSON in that repository has not been compared to the live ruleset above.
2. **Organization-level API paths are blocked** for this session
   (`orgs/*` → HTTP 403). Live ruleset state was recovered only via the
   per-repository projection in §3. Any future verifier must use that path or
   run with org-scoped credentials.
3. **Central CI is red on every Python consumer.** Deleting consumer
   `l9-analysis.yml` now would leave those repositories with a *failing*
   required check and no passing analysis. The Phase 6 canary gate is **not**
   met, so no consumer deletion was performed.

## 8. Next highest-leverage move

1. **Merge `l9-ci-sdk#85`** (prefer a merge commit, so `0efd762…` lands on
   `main`), **then this PR** to allowlist it, **then the follow-up** that bumps
   the Core action pin and flips the nine `sdk-revision` sites (§4.4).
2. Decide identity-map ownership — the reviewed 151-rule map must move to Core
   `@core-defaults` (wired through the existing, unused `invoke-sdk`
   `identity-map:` input) or to the SDK packaged map. It must **not** stay in
   consumers.
3. Repair the `refs/heads/main` → immutable SHA pin in `Quantum-L9/.github`.
4. Only then migrate consumers, per `CENTRAL_CI_MIGRATION_MATRIX.md`.
