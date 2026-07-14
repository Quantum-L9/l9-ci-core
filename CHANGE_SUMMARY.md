# CHANGE_SUMMARY — node-pipeline-g01-g12-patch v2

## Gaps closed (G-01 through G-12)

| Gap | Change |
|---|---|
| G-01 | `changed-files`, `pr-labels`, `labels-known` inputs + context job pattern in docs |
| G-02 | `l9-ci-install-command` input; SDK installed in validate/lint/test/security/ci-gate; `l9-ci run-pipeline --stage X` in each |
| G-03 | ci-gate: download `ci-summary-*` → `l9-ci gate` → emit `agent_review_payload.json` → `l9-ci gate --result` |
| G-04 | validate job: `check-transport-packet`, `check-deprecated-api`, `validate-thresholds`, `validate-rule-modes` |
| G-05 | Advisory `npm audit --audit-level=none` — surfaced as `::notice`, never blocks |
| G-06 | ci-gate uses `l9-ci gate` — no `!= success` pattern anywhere in gate section |
| G-07 | `semgrep` job (explicit Python setup, pinned version, `.semgrep/` guard) |
| G-08 | `scorecard` job (`ossf/scorecard-action` + SARIF upload) |
| G-09 | `actions/upload-artifact` at end of validate/lint/test/security jobs |
| G-10 | `Configure private SDK access` step writing scoped gitconfig to `RUNNER_TEMP` in all SDK jobs |
| G-11 | `security-events: write` on security + scorecard; `id-token: write` on sbom + scorecard |
| G-12 | `validate-thresholds` step in validate job wires governance coverage policy |

## Hardening (E-01 through E-12, Improve.md kernel)

| ID | Sev | File | Change |
|---|---|---|---|
| E-01 | BLOCKER | tests | Removed `'l9-ci '` from `forbidden_fragments`; workflow legitimately calls it |
| E-02 | BLOCKER | tests | `test_node_pipeline_final_gate_is_strict` (inverse) → `test_node_pipeline_ci_gate_contract` |
| E-03 | BLOCKER | tests | Security permissions assertion updated to include `security-events: write` |
| E-04 | HIGH | workflow | ci-gate setup-python step renamed to `Set up Python for SDK` |
| E-05 | HIGH | workflow | semgrep: explicit `actions/setup-python`, `SEMGREP_VERSION=1.96.0`, `--version` check |
| E-06 | HIGH | workflow | ci-gate Evaluate Required Results: collapsed to single `shell: python` block |
| E-07 | MEDIUM | workflow | Verbatim-copy comment added to each of 5 SDK auth blocks |
| E-08 | MEDIUM | tests | Gate assertions consolidated (absorbed by E-02) |
| E-09 | MEDIUM | docs | Context job caller example: `fetch-depth: 0` added |
| E-10 | MEDIUM | docs | Scorecard permissions documented in pipeline stages table |
| E-11 | LOW | commit note | Duplicate PR #15 prerequisite mention removed |
| E-12 | LOW | workflow | `actions/setup-python` SHA comment fixed: `v6.3.0` → `v5.5.0` |

## Metrics

| Metric | Before | After |
|---|---|---|
| Tests | 21 (original) | 34 |
| Validation checks | — | 37/37 pass |
| Blocker issues | 3 | 0 |
| Jobs | 5 | 8 |
