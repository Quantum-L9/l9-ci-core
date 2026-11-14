# Releasing a Quantum-L9 repository

One release engine, owned by Core, for every Quantum-L9 repository. No
repository owns a release workflow. A repository opts in by declaring
`.l9/release.json`. After that, merging the version bump to the default branch
is the release.

> Scope: this is how **consumer repositories** release. Core itself is not a
> consumer of the engine. It releases only through
> `docs/release/tag-and-release.sh` (see `docs/release/README.md` and
> `.l9/release-plane.yaml` `release_writers`). The engine refuses
> `Quantum-L9/l9-ci-core` in code and in contract
> (`.l9/release-plane.yaml` `consumer_release_engine`).

| Piece | Path |
|---|---|
| Engine (stdlib only) | `tools/l9_release/` (`python3 -m tools.l9_release`) |
| Declaration schema | `tools/l9_release/release.schema.json` (`l9.release/v1`) |
| Composite action | `.github/actions/release-engine/` |
| Contract | `.l9/release-plane.yaml` → `consumer_release_engine` |
| Examples | `docs/release/examples/*.release.json` |
| Tests | `tests/release_engine/` |

The engine is a sibling of `tools/l9_repo`, not part of it. The
repository-execution runtime exposes no publication command (AGENTS.md §11 and
§14; `tests/repo_execution/test_boundary.py`).

## 1. The declaration — `.l9/release.json` (`l9.release/v1`)

```json
{
  "schema": "l9.release/v1",
  "adapter": "python-uv",
  "version_authority": "pyproject.toml",
  "changelog": "CHANGELOG.md",
  "major_tag": false
}
```

| Field | Meaning |
|---|---|
| `adapter` | `python-uv` or `node-npm`: Core's build adapter |
| `version_authority` | Must be the adapter's one version source: `pyproject.toml` (`[project].version`, static) or `package.json` (`version`). A consistency assertion, not a choice. |
| `changelog` | Repository-relative path to the CHANGELOG. Each release needs a dated `## X.Y.Z - YYYY-MM-DD` section; `## [X.Y.Z] - …` is also accepted. |
| `major_tag` | `true` maintains `vMAJOR`, moving it forward only |

The document is closed: every field is required, and an unknown field, another
schema, or a mismatched authority fails closed. It never carries a token, a
command, a tool version, a workflow, or a Core revision.

Versions are the strict SemVer core: `MAJOR.MINOR.PATCH`, no leading zeros, no
prerelease or build metadata (the grammar of l9-meta-injector's ADR-075). The
exact tag is `vX.Y.Z`; the major tag is `vX`.

## 2. Stages

```text
plan     read-only: derive identity, observe remote, decide pending work
build    two clean rounds from the exact commit; byte-identical or fail
publish  re-plan, then only the pending side effects, then verify
verify   re-download every asset; sha256 -c; exact tag peels to the commit
```

**plan** generalises l9-meta-injector's `release-state.js`. Publication has
four independent side effects, and each is decided separately, so a rerun after
a partial publication finishes only what is missing:

| Side effect | Pending when |
|---|---|
| exact tag `vX.Y.Z` | absent remotely |
| GitHub Release | absent for the exact tag |
| assets | no Release, or the Release has no `SHA256SUMS` yet. `SHA256SUMS` is uploaded last, so it marks completion. |
| major tag `vX` (opt-in) | absent (`create`), or at an older version (`advance`). At the same version it is `hold`; ahead of this version the release is refused. |

The plan also has these outcomes:

- If the exact tag already exists **at another commit**, this push is not a
  release (an ordinary non-bump push to main): there is nothing to do and no
  error.
- If the exact tag is absent, the version must be strictly greater than every
  existing `vX.Y.Z` tag. Otherwise the release is refused.
- Whenever work is pending, the dated CHANGELOG section must exist at the
  commit.

**build** refuses a dirty working tree. It sets `SOURCE_DATE_EPOCH` to the
commit time, clones the exact commit twice into fresh directories, and builds
in each clone:

- `python-uv`: `uv build --sdist --wheel`. setuptools ignores
  `SOURCE_DATE_EPOCH` for sdist members, so the engine normalises only the
  sdist **archive envelope**: members sorted, mtime set to the epoch, uid/gid
  0, empty uname/gname, PAX headers dropped, gzip mtime 0 with no stored
  filename. It then proves every member's name, type, mode, link target and
  content digest unchanged. Wheels are untouched.
- `node-npm`: `npm pack --ignore-scripts`. The published package content must
  be committed, because lifecycle scripts do not run at release.

The two rounds must be byte-identical. The build then writes the artifacts,
`SHA256SUMS`, and `build-record.json` (`l9.release-build/v1`, provenance only,
not an asset).

**make-v1 phases are a precondition.** `build --phases verified-upstream`
reuses a result that a preceding job already produced at the same SHA: org
CI's `Analyze (central Core)` runs repository verification
(`setup validate check test`). `build --phases run` executes the phases in a
clean clone first. There is no default.

**publish**:

1. Creates an annotated exact tag. It is create-only, and the remote's
   refusal to update an existing ref is the proof.
2. Creates the GitHub Release, with notes from the dated CHANGELOG section and
   a provenance block: repository, commit, version source, epoch, adapter,
   engine revision, workflow run, `SHA256SUMS`.
3. Uploads the missing assets. An existing asset is never replaced.
4. If declared, advances `vX` with `--force-with-lease` bound to the SHA that
   the monotonicity proof read, so a racing writer loses.
5. Verifies.

No registry is published to (npm and PyPI are out of scope).

**verify** downloads `SHA256SUMS` and every asset and checks each digest, the
`sha256sum -c` equivalent. Any asset missing from, or extra to, `SHA256SUMS`
fails. It also checks that the exact tag peels to the commit, and that a
declared `vX` is not behind this version.

Exit codes: `0` done, `1` refused by a precondition, `2` tool, transport or
contract failure.

## 3. Running it

Locally, or from Cursor-Governance's future `make release` front door (dry
run / manual):

```bash
python3 -m tools.l9_release --workspace <repo> --repository <owner/name> plan
python3 -m tools.l9_release --workspace <repo> build --out dist --scratch .tmp/release --phases run
GH_TOKEN=… python3 -m tools.l9_release --workspace <repo> --repository <owner/name> publish --dist dist
```

In CI, run it through the pinned composite action:

```yaml
uses: Quantum-L9/l9-ci-core/.github/actions/release-engine@<full sha>
with:
  stage: plan        # plan | build | publish | verify
```

The action always targets `github.repository` at `github.sha`. No input names
another repository or revision.

## 4. Organization CI wiring (activation; not in this change)

The intended carrier is a `release` job in `org-ci.yml`:

- it runs on `push` to the repository's own default branch only, never on
  `pull_request` or `merge_group`;
- it `needs: analyze`;
- it runs only when `.l9/release.json` exists and `plan` reports
  `work-pending`, so every repository without the file sees no change
  (fleet safety);
- it serialises per repository through
  `concurrency: l9-release-${{ github.repository }}` with
  `cancel-in-progress: false`.

It is deliberately **not** wired by the change that introduces the engine:

1. **Pin advance (AGENTS.md §16).** `org-ci.yml` may reference the action only
   at a merged Core SHA. Wiring is the follow-up pin-advance PR.
2. **Contract.** `.l9/org-runtime-contract.yaml` `push_authority` reads: "A
   push trigger is an evaluation lane, not a trusted write lane. The push path
   holds contents:read". `test_required_workflow_has_no_write_scopes` and
   `test_workflow_permissions.py` lock `org-ci.yml` as write-free. A release
   job needs write authority (a tag and a Release). The operator has decided
   the write path (`GITHUB_TOKEN`, `contents: write` on the `release` job
   only; see the token finding below), so the pin-advance PR amends
   `push_authority` and narrows those two tests to that one job, never
   reachable on `pull_request` or `merge_group`.
3. **Trigger carrier.** GitHub documents that "Ruleset workflows support using
   the `pull_request`, `pull_request_target` and `merge_group` events" and
   that they "do not run on events triggered by the `GITHUB_TOKEN`". A `push`
   trigger in the central `org-ci.yml` may therefore never fire in a consumer
   repository. Whether it does today is UNKNOWN until a consumer's run list
   shows it.

### Token finding

GitHub's ruleset documentation does not state the `GITHUB_TOKEN` scope,
whether `permissions:` is honoured for a ruleset-required workflow in the
target repository, or whether that workflow can read secrets. The retired
required-workflows feature documented: "Secrets in the source repository will
not be fetched when a workflow runs in the target repository … should be
created at either the organization level or in the target repositories."

**Decision (CTO answer, from the operator, 2026-11-14):** the org-CI built-in
`GITHUB_TOKEN` can push tags and create releases in the repository it runs on.
The release job uses it with minimal job-level permissions (`contents: write`
on the `release` job only; `analyze` stays `contents: read`). There is no App
token path. The documentary evidence above is recorded as found; the
operator's statement is the authority for the live capability.

## 5. Reproducibility evidence — l9-constellation-topology @ `89150bd`

`89150bdef6057913f1037aff4d973033de72c224`, version `3.0.0`,
`SOURCE_DATE_EPOCH=1794678803`, uv 0.11.32:

| Artifact | Build A | Build B |
|---|---|---|
| raw sdist (`uv build`) | `0e7b1e7d…` | `d40f865f…` (differs) |
| engine-normalised sdist | `4555bcd2d9b601f52f61f61c1049e82af4836fdd66c2094996fcc781404b0e89` | identical |
| wheel | `c6f4eea0e9a1ce46caa7f310da28032a0d3b4be0ee9b6483bee26e1dd08b8028` | identical |

Normalising both raw sdists gives the same `4555bcd2…`. All 256 members (name,
type, mode, link target, content sha256) are equal across the raw builds and
the normalised archive. The engine's own `build` stage (two clean clones)
produced the same digests.

## 6. Release l9-constellation-topology v3.0.0

`main` is already at `89150bd`, version `3.0.0`, with a dated
`## 3.0.0 - 2026-11-14` CHANGELOG section, and no `v3.0.0` tag exists.

1. Once org-ci wiring is active (§4), open a PR in
   `Quantum-L9/l9-constellation-topology` adding
   `.l9/release.json` with the contents of
   `docs/release/examples/l9-constellation-topology.release.json`
   (`python-uv`, no major tag).
2. Merge it. The push to `main` runs `plan`: `v3.0.0` is absent, `3.0.0` is
   ahead of `v2.0.0`, and the CHANGELOG section exists. The engine builds twice
   (wheel `c6f4eea0…`; the sdist digest is recomputed at the merge commit,
   since its epoch is that commit's time), creates the annotated `v3.0.0` at
   the merge commit, creates the Release with the 3.0.0 notes, and uploads the
   wheel, the sdist and `SHA256SUMS`.
3. Check the result with `verify`. The job runs it automatically, and a rerun
   is a no-op.

Note: the tag lands on the merge commit that adds `.l9/release.json`, not on
`89150bd`, because the declaration must be present in the released tree. Their
source trees differ only by that file.

## 7. Migrating l9-meta-injector off its own `release.yml`

The injector's `.github/workflows/release.yml` is the model the engine
generalises (same side effects, same create-only exact tag, same forward-only
major CAS). Once the engine is active, migrate in one injector PR:

1. Add `.l9/release.json` from
   `docs/release/examples/l9-meta-injector.release.json` (`node-npm`,
   `major_tag: true`).
2. Delete `.github/workflows/release.yml`.
3. Keep `scripts/release-identity-cli.js` only while other scripts consume it.
   The engine derives identity itself.

Behaviour that does not carry over:

- The injector's consumer-acceptance job (`uses: ./`, inventory mode) has no
  engine equivalent. Its repository verification (`make setup validate check
  test`) is the gate, so move that acceptance into `make test` or `Repo.mk`
  before deleting the workflow.
- Release notes come from the CHANGELOG section instead of
  `--generate-notes`.
- The `npm pack` tarball and `SHA256SUMS` are attached. This is the binary
  that was a manual follow-up for v4.4.0.

Do not edit those repositories from Core.
