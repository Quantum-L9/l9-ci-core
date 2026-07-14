# VALIDATION — node-pipeline-g01-g12-patch v2

## Gate results: 37/37 PASS

| Category | Checks | Result |
|---|---|---|
| YAML structural (jobs, timeouts, SHA pins, concurrency, permissions) | 6 | PASS |
| PR #15 regression guard (--redact, fetch-depth, sbom ${{ }} syntax) | 3 | PASS |
| G-01 through G-12 coverage | 12 | PASS |
| E-01 through E-12 hardening | 12 | PASS |
| Test suite integrity (count, syntax, G-test presence) | 4 | PASS |

## Known unknowns

| Item | Status |
|---|---|
| actions/setup-python SHA → v5.5.0 tag live verification | Accepted from prior audit; live tag check not available in sandbox |
| ossf/scorecard-action SHA → v2.4.3 live verification | Same |
| github/codeql-action/upload-sarif SHA → v4.36.2 live verification | Same |
| `SEMGREP_VERSION=1.96.0` PyPI availability | Known real release as of 2026-07 |
| `l9-ci gate` CLI flag contract | Accepted per session context; not runtime-verified |

## Test results

- 34 tests parsed, 0 syntax errors
- Structural contract checks (YAML shape, SHA pins, job graph, permission set, input names) fully verified
- Runtime execution requires target workflow file + l9-ci SDK (not available in sandbox)
