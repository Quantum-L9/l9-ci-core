# no-bytecode and sha-pins are org-wide checks (2026-11-21)

A decision record, kept outside `docs/adr/` like
[`security-checks.md`](security-checks.md).

## Status

Accepted 2026-11-21, operator-directed correction: two organization concerns
that lived only as `Quantum-L9/.github` scripts become Core org checks under
the same tool spec as the others. **Decision owner:** `Quantum-L9/l9-ci-core`.

## Decision

| Check (`org-ci.yml` job) | Pull request / merge group / push | No base (dispatch, canary) | Nightly (`nightly-sink.yml`) |
|---|---|---|---|
| `no-bytecode` | fails on a tracked `*.pyc` / `*.pyo` / `*.pyd` or `__pycache__/` file the change adds, modifies or renames in | every tracked file, reported (warnings), passes | every tracked file, `l9-central/no-bytecode/`, counted |
| `sha-pins` | fails on a `uses:` in a changed workflow file or action definition (`action.yml`) not pinned by a full 40-character commit SHA; a `docker://` image needs `@sha256:`; `Quantum-L9/l9-ci-core@v1`, `@v2`, `@v2.0.0` are the documented frozen Core tags; local `./` actions have no ref | every workflow and action file, reported | every file, `l9-central/sha-pins/`, counted |

Both are blocking, `contents: read`, and always report: "no tracked bytecode
in N file(s)", "N uses: reference(s) pinned", "no changed workflow or action
files", or "not applicable: no workflow or action files". Each finding is
annotated at its file and line; an unresolvable base fails by name.

## Provenance

- `sha-pins` ports `Quantum-L9/.github` `ops/audit-sha-pins.sh` (main): the
  same SHA rule and frozen-tag exception, the same comment and inline-comment
  handling, and the same local-action exemption; it adds action definitions,
  docker digests, a ref-less `uses:` (`owner/repo` with no `@`), and change
  scope. `.github` has no tests for the script; these are new
  (`tests/workflows/test_org_ci_hygiene_checks.py`).
- `no-bytecode`: the directive names `ops/check-no-bytecode.sh` from `.github`
  #279. #279 changes only `.gitignore`, and no such script exists on `.github`
  `main` or on #279's branch, so the check is written here.

Core's own tree passes both (403 tracked files; 228 `uses:` references in 50
files, all pinned).

## Requiredness (operator action)

GitHub → Quantum-L9 → Settings → Rules → Rulesets → `l9-canonical-ci-required`
(21895545) → "Require status checks to pass" → Add checks → `no-bytecode`,
`sha-pins` → Save changes, once each has reported on a governed repository.
`.github`'s own `sha-pin-audit.yml` becomes redundant then (its change).
