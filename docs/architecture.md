# Architecture: l9-ci-core Bootstrap Gate

## Purpose

All L9 CI pipelines consume untrusted external inputs: third-party GitHub Actions,
downloaded binaries, and pip dependencies. This repo enforces four immutable
security contracts before any bootstrap job may execute.

## Enforcement model

```
PR opened / synchronized
        │
        ▼
  ┌─────────────────────────────────────────────────────────┐
  │            run_bootstrap_validators.py                  │
  │  (runs all four gates as sequential subprocesses)       │
  └─────┬──────────┬─────────────┬──────────────┬──────────┘
        │          │             │              │
        ▼          ▼             ▼              ▼
  action-pins  download-     ci-lock      workflow-
  validator    integrity     validator    contracts
        │          │             │              │
        └──────────┴─────────────┴──────────────┘
                          │
                          ▼
              bootstrap-manifest.json
              validate_bootstrap_results.py
                          │
                    PASS / FAIL
```

## Gate contract schema

Every gate emits a `bootstrap-gate-result.schema.json`-compliant JSON file:

```json
{
  "schema_version": "1.0",
  "gate_id": "workflow/action-pins",
  "result": "passed | failed | error",
  "violations": [{"code": "FLOATING_ACTION_REF", "message": "...", "path": "...", "line": N}],
  "warnings": [],
  "metadata": {"files_scanned": 3, "references_scanned": 12}
}
```

## Violation codes

| Gate | Code | Meaning |
|------|------|---------|
| action-pins | `FLOATING_ACTION_REF` | Uses non-SHA ref (version tag, branch) |
| action-pins | `SHORT_SHA_REF` | SHA is < 40 hex chars |
| action-pins | `PATH_ESCAPE_DETECTED` | Local action path escapes repo |
| download-integrity | `STREAMED_EXECUTION_FORBIDDEN` | `curl ... \| bash` pattern |
| download-integrity | `MISSING_DOWNLOAD_MARKER` | No `# l9-download:` comment |
| download-integrity | `UNREGISTERED_DOWNLOAD` | Marker key not in registry |
| download-integrity | `MUTABLE_LATEST_URL` | URL contains `/latest/` |
| dependencies/ci-lock | `LOCK_FILE_MISSING` | bootstrap.lock absent |
| dependencies/ci-lock | `LOCK_MISSING_HASH` | Lock has no `--hash=` entries |
| dependencies/ci-lock | `UNBOUNDED_PIP_INSTALL` | `pip install <pkg>` without hashes |
| dependencies/ci-lock | `UNCONDITIONAL_PIP_UPGRADE` | `pip install --upgrade` without hashes |
| dependencies/ci-lock | `BRANCH_URL_INSTALL` | `pip install git+...@<branch>` |
| workflow/contracts | `PR_TARGET_FORBIDDEN` | `pull_request_target` trigger |
| workflow/contracts | `BOOTSTRAP_PERMISSIONS_MISSING` | Bootstrap job missing `permissions` |
| workflow/contracts | `BOOTSTRAP_TIMEOUT_MISSING` | Bootstrap job missing `timeout-minutes` |
| workflow/contracts | `BOOTSTRAP_SHELL_UNSAFE` | `set +e` without status capture |
| workflow/contracts | `FAILURE_SWALLOWED` | `cmd \|\| true` |

## Governance files (operator-maintained)

- `.github/governance/action-pins.lock.json` — maps action ref comments to approved SHAs
- `.github/governance/download-integrity.yaml` — maps download keys to version/url/sha256
- `.github/governance/ci-dependency-exceptions.yaml` — time-bounded exceptions (30-day max)
- `.github/governance/workflow-contract-debt.yaml` — deferred contract violations

## Resource limits

| Limit | Value |
|-------|-------|
| Max workflow files per repo | 50 |
| Max workflow file size | 1 MiB |
| Max total scan bytes | 50 MiB |
| Max run block bytes | 256 KiB |
| Max registry entries | 200 |
| Max result file bytes | 5 MiB |

All limits are runtime-configurable via `l9_bootstrap.limits` module attributes.

## Known remaining unknowns (operator action required)

| Unknown | Action |
|---------|--------|
| Real sha256 hashes in `requirements/bootstrap.lock` | Regenerate on ubuntu-latest Python 3.12 with `pip-compile --generate-hashes` |
| Real action SHAs in `action-pins.lock.json` | Populate before first PR run |
| Download registry entries | Add actual L9 tool downloads |
