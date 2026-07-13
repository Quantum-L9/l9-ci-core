<!-- L9_META
l9_schema: 1
parent: l9-skill-compiler
layer: reference
role: build_execution_contract
tags: [build, zip, validation, artifact_generation, no_stub, one_turn]
owner: igor_beylin
status: active
version: 1.0.0
updated: 2026-06-17
/L9_META -->

# Build Execution Contract

## Purpose

Use this contract when the user asks the compiler to build, rebuild, package, export, or `/build_zip` a complete skill pack or related artifact pack. It is an execution kernel, not a separate skill. It forces full artifact delivery, real validation, and zip packaging instead of plans, decorative scaffolds, or fake pass reports.

## Activation

Load this reference when any of these are true:

- The user asks to build, rebuild, regenerate, integrate, package, zip, export, or return a download link.
- The requested output is a complete skill pack, pack update, artifact bundle, or downloadable archive.
- The user invokes `/build_zip` or asks for one-turn, high-velocity, dry-run false artifact generation.
- The task requires writing files and validating the result before delivery.

Do not load this contract for pure discussion, planning, comparison, or conceptual review unless the user asks to produce the artifact in the same turn.

## Core Law

Build the requested pack fully in the current response cycle, validate it with real structural or deterministic checks, create a zip bundle, and return the download link. Do not output a plan only when the user requested a build.

## Build Mode Requirements

When this contract is active, the compiler must:

1. Inspect all provided inputs first.
2. Infer the correct artifact structure from the request and files.
3. Build the entire requested pack or update in one pass when enough information exists.
4. Create complete production-ready files.
5. Use repo, domain, and source evidence when available.
6. Label missing or unverifiable values as `Unknown`.
7. Avoid invented credentials, secrets, contacts, licenses, domains, approvals, test results, or external facts.
8. Avoid decorative files and duplicate responsibilities.
9. Run deterministic validation or structural checks; never claim validation that did not run.
10. Create a zip containing only approved generated or updated artifacts.
11. Return summary, validation results, Unknowns, zip manifest, and download link.

## Required Artifacts

Generate these when they are useful for the artifact type and not merely decorative:

- Complete generated or updated files.
- `MANIFEST.md` for non-trivial bundles or multi-file packs.
- `CHANGE_SUMMARY.md` when modifying an existing pack.
- `VALIDATION.md` or validation report when validation was performed.
- `RUNBOOK.md` only when operational use exists.
- `README.md` only when the pack needs a human/operator entrypoint.
- Zip bundle.

For ChatGPT skill packaging, preserve the expected skill-root structure. Do not add docs that would bloat the skill unless they change operator use or validation.

## Validation Gates

Before returning a zip, verify:

- `no_stubs`: no required files contain TODO-only, placeholder, scaffold-only, or fake content.
- `no_scaffolds`: no initializer examples or decorative files remain.
- `no_fake_validation`: validation status reflects checks actually run or explicitly marks blocked checks.
- `no_duplicate_responsibilities`: files and references have distinct jobs.
- `all_required_files_complete`: required files exist and are non-empty with real content.
- `unknowns_labeled`: missing or unverifiable facts are labeled `Unknown`.
- `zip_bundle_created`: final archive exists.
- `zip_download_link_rendered`: final response includes a download link.

## Quality Bar

Classify the build as acceptable only when it is:

- operator usable
- developer usable
- agent safe
- validation backed
- no-drift
- repo/domain aligned when evidence exists
- production-ready within the declared scope

Do not claim enterprise-grade readiness unless validation evidence supports it.

## Stop Conditions

Halt or return a blocked result if:

- Required inputs are unavailable.
- The build requires invented or unverifiable facts.
- Any required artifact would be stub-only or scaffold-only.
- Validation cannot distinguish real checks from fake pass claims.
- A zip cannot be created.

When blocked, return the exact blocker, Unknowns, and smallest safe next action. Do not pretend completion.

## Output Contract

For active build/package execution, final response should include:

```yaml
execution_summary:
  status: built | rebuilt | packaged | blocked
  artifact: string
  zip_path: string | Unknown

files_changed:
  created: []
  updated: []
  removed: []

validation_results:
  checks_run: []
  passed: []
  failed: []
  blocked: []

unknowns: []
zip_manifest: []
download_link: string | Unknown
```

Keep the user-facing response short unless the user asks for the full audit report.
