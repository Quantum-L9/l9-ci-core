# PR-A — Bootstrap Supply-Chain Security Gates: Consolidation Report

Branch: `pr-a-bootstrap-gates` (off `main` @ `2b330a5`)
Repository: `Quantum-L9/l9-ci-core`
Status at time of writing: **all gates and tests green in a clean-room Python 3.12 environment** (evidence below). This report is written to be verifiable; every claim maps to a command you can re-run.

---

## 1. What this PR does

PR-A adds a set of **bootstrap supply-chain security gates** to the repo and hardens the existing CI workflows to satisfy the strongest of those gates without breaking the pre-existing green pipeline.

Four gates run through one orchestrator (`.github/scripts/run_bootstrap_validators.py`), which invokes each validator as a **sequential subprocess** and then re-validates every emitted result plus the aggregate manifest through the schema-backed results validator:

| Gate ID | Validator | What it enforces |
|---|---|---|
| `workflow/action-pins` | `validate_action_pins.py` | Every external `uses:` is pinned to a 40-hex commit SHA and matches the checked-in pin inventory. |
| `workflow/download-integrity` | `validate_download_integrity.py` | Every marked tool download binds an inline URL + SHA-256 that match the download registry, verifies the checksum against the same artifact it extracts, and uses a structural checksum step. |
| `dependencies/ci-lock` | `validate_ci_dependencies.py` | The production bootstrap lock is exact-pinned + fully hashed with no index-subverting options; workflow `pip install`s are enforced under a **phased, baseline-aware** model (see §4). |
| `workflow/contracts` | `validate_workflow_contracts.py` | Bootstrap jobs declare least-privilege `permissions` and strict shells; the reusable `pr-pipeline.yml` public interface is preserved. |

---

## 2. Authority order (the correction that drove this remediation)

The prior consolidation inverted the authority order. This PR restores it explicitly:

> **security contract → production invariant → implementation → tests**

Where a **test contradicted a security invariant, the test was updated and the stronger invariant preserved** — not the reverse. Concretely, `tests/test_workflows.py::test_new_workflows_use_v6_actions` previously asserted floating tags (`actions/checkout@v6`). SHA-pinning is the stronger invariant, so the test now asserts a **SHA pin with a `# v6.x` annotation** and rejects floating tags. The intent ("these workflows are on v6, not v4/v5") is preserved; the mechanism is hardened.

Fail-closed loading is mandatory throughout: missing/broken schemas, a missing jsonschema library, a tampered baseline digest, or a malformed registry all produce **error / exit 2**, never a silent pass.

---

## 3. Real supply-chain data (no placeholders in production artifacts)

- **`.github/governance/action-pins.lock.json`** — 14 external action entries, each resolved **live via the GitHub API** to its 40-hex commit SHA (`generated_at`/`verified_at` = 2026-07-12). All 9 workflows were then pinned to these SHAs with `# vX.Y.Z` annotations (86 pinned refs total, **0 floating external refs remaining**).
- **`.github/governance/download-integrity.yaml`** — the gitleaks `v8.18.4` linux-x64 download, with a SHA-256 (`ba6dbb…e7d`) that was **independently re-downloaded and verified** (2,903,447 bytes, `sha256sum` match, and matched upstream `gitleaks_8.18.4_checksums.txt`). The `l9-self-ci.yml` download step now carries the `# l9-download: gitleaks-linux-x64` marker, inline `TOOL_URL`/`TOOL_SHA256` bindings that match the registry, and a `sha256sum --check` **before** extraction.
- **`requirements/bootstrap.in` / `requirements/bootstrap.lock`** — `bootstrap.in` lists only the third-party libraries actually imported at runtime (`jsonschema`, `ruamel.yaml`). The lock was compiled with `pip-tools` on **Python 3.12.13 (Ubuntu x86_64)**, resolves 8 packages, and carries **189 real `--hash` lines with no placeholder hashes**. A `--require-hashes` install was proven in a fresh venv (exit 0).

> Note: test **fixtures** under `tests/fixtures/` still use a synthetic `aaaa…` hash by design — they exercise validator logic and must not depend on network state. Production artifacts contain real hashes only.

---

## 4. Phased, baseline-aware dependency enforcement

The `dependencies/ci-lock` gate correctly finds that **26 pre-existing `pip install` steps** across the 9 workflows do not use `--require-hashes`. Hard-failing them would break the existing green pipeline; silently ignoring them would hide real risk; 30-day expiring exceptions would manufacture a false cliff. Instead this PR implements a **phased model**:

**Hard-fail (blocking):**
- every install in a bootstrap-managed job (any step that installs `requirements/bootstrap.lock`),
- any install not recorded in the baseline (i.e. newly added),
- any baselined install whose normalized command changed (`*_BASELINE_CHANGED`),
- any mutable VCS ref, version range/unbounded install, or unconditional `pip upgrade` that is not a matched legacy observation.

**Observe only (non-blocking warning):**
- unchanged, pre-existing, non-bootstrap installs that exactly match the checked-in baseline.

**Baseline** — `.github/governance/ci-dependency-baseline.json` (schema: `schemas/ci-dependency-baseline.schema.json`):
- holds the exact identity `(path, job, step, violation_code)` + `command_sha256` of the 26 legacy findings,
- is **fail-closed against growth**: a new finding, a changed command, a stale entry, a wildcard entry, or a self-digest that does not match the recomputed digest all block (violation / error),
- exposes deterministic metadata on every run: `legacy_observation_count`, `new_violation_count`, `baseline_digest`.

**PR-C** is expected to eliminate this baseline as the remaining gates and dependency profiles are migrated to hash-pinned installs.

---

## 5. Preservation of existing green gates

- `pr-pipeline.yml` **public `workflow_call` interface is byte-identical to `main`** (verified by parsing both and comparing inputs/secrets/outputs). The only change to that file is 24 lines of `uses:` → SHA pins; no job, step, artifact, or logic change.
- All pre-existing workflow jobs, aggregator jobs, and artifacts are unchanged apart from SHA pinning.
- The 26 legacy installs remain functional (observed, not blocked).

---

## 6. Verification evidence (clean-room, Python 3.12)

Re-runnable from the repo root:

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install --require-hashes --no-deps -r requirements/bootstrap.lock   # exit 0, 8 pkgs
python .github/scripts/run_bootstrap_validators.py --root . --output-dir bootstrap-results
python .github/scripts/validate_bootstrap_results.py --results-dir bootstrap-results --root .
pip install pytest pyyaml && python -m pytest tests
```

Observed results at the time of writing:

- `pip install --require-hashes` → **exit 0**, 8 packages, `import jsonschema, ruamel.yaml` OK.
- Orchestrator → **all 4 gates PASSED**, manifest `overall=passed complete=True`.
- Aggregate results validator → **PASSED** (`All bootstrap gate results validated`).
- `dependencies/ci-lock` metadata → `result=passed`, `legacy_observation_count=26`, `new_violation_count=0`, `baseline_digest` present and matching the file.
- Full test suite → **128 passed** (existing repo tests + 99 bootstrap tests + updated workflow test).

---

## 7. Honest limitations

- Results above were produced in the build/CI sandbox, not yet on GitHub-hosted runners. The new `.github/workflows/bootstrap-gates.yml` job runs the same orchestrator + pytest on CI; final confirmation is the green check on this PR.
- `ossf/scorecard-action` is pinned to the latest `v2.x` commit (no exact `v2` release ref existed at resolution time); `trufflesecurity/trufflehog@main` is pinned to a reviewed commit SHA. Both are recorded with their `verification_method` in the pin inventory.
- The legacy baseline is intentionally temporary; it is debt to be retired in PR-C, not a permanent waiver.
