# Releasing l9-ci-core

Repo: <https://github.com/Quantum-L9/l9-ci-core>
Contract: [`.l9/release-plane.yaml`](../../.l9/release-plane.yaml)
(`l9.release-plane/v1`), asserted by `tests/workflows/test_release_plane.py`.

## Two planes, two responsibilities

| Plane | Source | Responsibility |
|---|---|---|
| **Production CI runtime** | `main` → `.github/workflows/org-ci.yml` | What governed repositories run. Bound directly by the GitHub organization ruleset. |
| **Immutable releases** | `vMAJOR.MINOR.PATCH` tags + GitHub Releases | Audit, provenance, rollback identity, release notes, compatibility communication. |

`main` is the production channel. A merge to `main` is production for the
next governed `pull_request` or `merge_group` evaluation in every targeted
repository. Nothing is propagated to, pinned in, or updated inside a consumer.

Releases are **not** propagation. Cutting `v2.8.0` changes nothing downstream;
it records which exact `main` commit was known-good at that point.

### What no longer exists

- **No moving major alias.** Core releases do not create or move `v2`. The
  `refs/tags/v2` ref is not a release alias and is not a governed
  organization-enforcement consumption path. It survives only as the
  transitional consumer toolchain installer tag
  (`install-consumer-ci@v2`, see [`docs/consumer-lint-test.md`](../consumer-lint-test.md)),
  moved solely by `tools/publish_consumer_ci_tag.sh` after a human pin-file
  PR. The release script never touches it.
- **No consumer Core pin.** Consumers do not `uses: Quantum-L9/l9-ci-core/...@vX`
  for organization CI. Historical guidance that offered `@v2.0.0` / `@v2` /
  SHA pins for the org path is retired.
- **No hard-coded release number** in `release-validation.yml`. The expected
  version is `metadata.version` in `.l9/repo-spec.yaml`.

## The organization ruleset binding

The production binding is a GitHub organization ruleset, configured as:

```text
Ruleset:            L9 / Required Organization CI
Enforcement:        active
Target repos:       governed repositories (custom property, see below)
Target branch:      default branch
Require PR:         true
Required workflow:  repository Quantum-L9/l9-ci-core
                    branch     main
                    file       .github/workflows/org-ci.yml
```

That is a configuration model, not an importable ruleset file. GitHub
invokes a required workflow on `pull_request`, `pull_request_target`, and
`merge_group` only; it does not invoke it on `push`. Core's native `push`
trigger governs Core's own repository and native callers. It is not
organization-wide push fanout, and `.l9/org-runtime-contract.yaml` says so
under `cross_repository_push_fanout`.

Repository targeting belongs to the ruleset, not to Core. The intended
future state is an organization custom property such as
`l9_ci_managed = true|false` (default `true`, Core itself `false`), so a new
repository is governed by default with no bootstrap CI PR. That property is a
proposal; it has not been observed in the Quantum-L9 organization.

### Governance clarification (proposed, not yet organization law)

Cursor-Governance L9-ORG-008 forbids reusable-workflow references to `main`
and to unprotected tags. The ruleset binding above is a different mechanism:

> A GitHub organization ruleset required-workflow source-branch binding is a
> GitHub control-plane binding and is not a reusable-workflow consumer
> reference. L9-ORG-008 continues to prohibit
> `uses: Quantum-L9/l9-ci-core/...@main`. It does not prohibit the
> organization ruleset from selecting repository `Quantum-L9/l9-ci-core`,
> branch `main`, workflow `.github/workflows/org-ci.yml`, provided that Core
> `main` is independently protected as the production control-plane branch.

This is recorded in `.l9/release-plane.yaml` under
`governance_clarification` with `recorded_in_cursor_governance: false`. It
becomes law only through a Cursor-Governance PR to `ORG_INVARIANTS.yaml`
under L9-ORG-007 (independent human authorization).

## Core `main` protection

Because `main` is the runtime, Core needs its own control-plane ruleset,
separate from the organization CI ruleset that governs consumers:

```text
Ruleset:  L9 / CI Control Plane
Target:   Quantum-L9/l9-ci-core, branch main
Rules:    pull request required; independent approval for governed paths
          (.l9/, .github/workflows/, .github/actions/, CODEOWNERS);
          required checks taken from real successful self-CI runs
```

Required check context strings are deliberately not written here. Per
Cursor-Governance L9-ORG-009, blocking enforcement must be evidence-backed;
take the names from successful Actions runs before binding.

`.l9/release-plane.yaml` carries the machine-readable form of this
requirement under `core_main_protection`, so the attestation below compares
live GitHub against a contract rather than against a hard-coded expectation.
It declares no policy this section does not already state, and it still names
no check context.

## Attesting the live control plane

Repository-local tests prove Core's contracts agree with each other. They
cannot prove GitHub is configured the way those contracts describe. That
evidence comes from a read-only verifier:

```bash
make attest-control-plane          # or: python3 tools/verify_control_plane.py
python3 tools/verify_control_plane.py --json
```

It compares live GitHub state against `.l9/release-plane.yaml` on three
points, and every request is a `GET` — it never creates or edits a ruleset,
branch, repository setting, release, or tag:

| Check | What must hold |
|---|---|
| organization required-workflow binding | the rule active on Core `main` resolves to this repository's own id, `refs/heads/main`, and `.github/workflows/org-ci.yml`, from an **organization** ruleset in `active` enforcement |
| Core `main` protection | the rules declared under `core_main_protection` are active on `main`, with code-owner review required and at least one bound status check |
| immutable releases | `GET /repos/{owner}/{repo}/immutable-releases` reports `enabled: true` |

The source repository is matched by numeric id, not by name, so a workflow
with the same filename in another repository or on another branch is a
failure rather than a silent pass.

Three outcomes exist and only one is success: **PASS**, **FAIL**, and
**UNKNOWN**. Absent credentials, a 403, an unreachable API, and an
unrecognised response body are all UNKNOWN, and UNKNOWN is never PASS — the
process exits `2` on any FAIL, `3` on any UNKNOWN, `0` only when every check
passes.

Credentials are read from `L9_CONTROL_PLANE_TOKEN`, `GH_TOKEN`, or
`GITHUB_TOKEN`. The workflow-scoped `GITHUB_TOKEN` can see the ruleset
binding and branch rules but **not** the immutable-releases setting, which
needs repository `administration: read`; with only that token the third check
is correctly UNKNOWN. That is why the attestation is
`.github/workflows/control-plane-attestation.yml`, run on demand, rather than
a job inside `release-validation.yml`: wiring it into the release gate would
make every release depend on an admin credential being present. It is Core
governance assurance — no governed downstream repository runs it, and none
needs organization-admin credentials to be governed.

## Who may write a release tag

Two tag namespaces exist and must never be conflated
(`.l9/release-plane.yaml` → `release_writers`):

| Namespace | Meaning | The only authorized writer |
|---|---|---|
| `vMAJOR.MINOR.PATCH` | immutable Core release identity | `docs/release/tag-and-release.sh` |
| `v2` | transitional `install-consumer-ci@v2` installer tag | `tools/publish_consumer_ci_tag.sh` |

`tools/check_release_writers.py` (`make check-release-writers`, and part of
the `unittest` suite the release gate runs) proves that exactly one
executable surface can create, move, or push each namespace, and that neither
writer can reach into the other's. The invariant is namespace ownership, not
the absence of tagging commands: "only one `git tag` in the repository" would
either forbid the transitional lane or bless a second release writer.

Scope is executable mutation only. Shell, YAML, and Python comments,
docstrings, printed instructions (`echo "… git push origin v2"`), Markdown,
and read-only inspection (`git rev-parse`, `git show-ref`, `gh release view`)
are not writers. A mutation whose target ref cannot be resolved to a
namespace is a failure, not a pass.

## What the release gate checks

Pushing a `vX.Y.Z` tag (or dispatching `release-validation.yml` with a tag)
checks out that revision and runs `validate-release`. It fails closed unless
**all** hold:

- The tag is an exact semantic version. A moving alias is rejected.
- The tag equals `metadata.version` in `.l9/repo-spec.yaml`.
- `.l9/repo-spec.yaml` declares `phase_4: … status: implemented`.
- `.l9/architecture.yaml` is `authoritative`, role `central-ci-orchestrator`,
  and declares `production_channel`.
- `.l9/publication-contract.yaml` is the authoritative publication contract.
- `.l9/release-plane.yaml` is authoritative with `runtime_authority: false`
  and the moving major alias disabled.
- Every external action under `.github/**` is pinned to a full 40-char SHA.
  Discovery covers `*.yml` **and** `*.yaml`: GitHub loads a workflow or a
  composite action from either spelling, so scanning one extension would leave
  the other free to carry a mutable reference into an immutable release.
- The full `unittest` suite passes — which includes the release-writer
  uniqueness invariant above, so a release cannot be cut while a second
  writer for either tag namespace exists.

## Cutting a release

1. Open a PR that bumps `metadata.version` in `.l9/repo-spec.yaml` and adds
   `docs/release/RELEASE_NOTES_vX.Y.Z.md`. Merge it to `main`.
2. From a clone with tag push rights (automated Claude sessions cannot push
   tags; the git gateway denies `refs/tags/*`):

   ```bash
   bash docs/release/tag-and-release.sh X.Y.Z            # tags origin/main
   bash docs/release/tag-and-release.sh X.Y.Z <commit>   # or a specific SHA
   ```

   The script first runs a **preflight**: it checks the exact target commit
   out into a temporary detached worktree and runs `validate_release.py`
   against it (the same validator the post-tag workflow runs, including the
   full `unittest` suite; `python3` with PyYAML is required locally). Only after the
   preflight passes does it create the annotated immutable tag `vX.Y.Z`,
   push it, and create the GitHub Release from the notes file. It refuses to
   move an existing release tag and never touches `v2`.
3. Confirm `release-validation.yml` is green for the tag. This post-tag run
   is the independent attestation of the same revision.
4. Publish the GitHub Release as immutable where the organization has
   immutable releases enabled, so the tag and assets cannot change after
   publication.

The lifecycle, in order:

```text
version bump + release notes PR
          ↓
     merge Core main
          ↓
   RELEASE PREFLIGHT  (validate the exact main SHA: contracts, pins, tests)
          ↓
         PASS
          ↓
 create immutable vX.Y.Z
          ↓
 push tag + GitHub Release
          ↓
 post-tag release-validation.yml attestation
```

An immutable tag is an audit identity. Validating before it exists means a
failed check never leaves behind a tag that policy forbids moving.

### Manual equivalent

```bash
git fetch origin
REL=$(git rev-parse origin/main)
git tag -a vX.Y.Z "$REL" -m "l9-ci-core vX.Y.Z"
git push origin vX.Y.Z
gh release create vX.Y.Z --repo Quantum-L9/l9-ci-core \
  --title "l9-ci-core vX.Y.Z" --notes-file docs/release/RELEASE_NOTES_vX.Y.Z.md
```

### If validation fails

If the **preflight** fails, no tag exists: fix `main` and re-run the script
for the same version. If the **post-tag** workflow fails on a tag the
preflight passed, do not move the tag. Fix `main`, then cut the next patch
version. A release tag that consumers or audit records may already cite is
never repointed.

## Rollback

Runtime rollback is a revert on `main` (PR, governed like any Core change).
Releases give the revert an identity to target: `git revert` to the commit
that `vX.Y.Z` names, or open a PR that restores that tree. No consumer changes.

## SDK promotion

The SDK boundary is stricter than the consumer → Core boundary. Core consumes
`l9-ci-sdk` only at an exact 40-character commit SHA listed in
`.l9/sdk-compatibility.yaml`. An SDK change reaches the fleet through exactly
one governed Core promotion PR:

```text
SDK merge → exact SDK SHA → Core PR editing .l9/sdk-compatibility.yaml
  → SDK contract suite + self-CI + provisioning tests
  → independent approval → merge Core main → fleet
```

Zero downstream promotions. See the `sdk-pin-mirrors` companion rule in
`.l9/repo-workflow.json` for every file a pin change must touch.

## Historical documents

`RELEASE_CHECKLIST_v2.0.0.md` and `RELEASE_NOTES_v2.0.0.md` record the
v2.0.0 cut under the retired moving-alias model. They are kept as history and
are not the current procedure.
