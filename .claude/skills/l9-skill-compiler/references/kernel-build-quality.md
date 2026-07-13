<!-- L9_META
l9_schema: 1
parent: l9-skill-compiler
layer: reference
role: build_quality_kernel
tags: [skill, compiler, build_quality, validation, zero_stub]
owner: igor_beylin
status: active
version: 1.0.0
updated: 2026-07-04
/L9_META -->

# Build Quality Kernel

## Cardinal Rule

Build the requested pack fully in one turn, validate it, zip it, and return a download link.

## Use When

Consult when handling `/build_zip` or full build/rebuild/package requests.

## Hard Rules

- **Inspect all provided inputs first.**
- **Infer the correct artifact structure** from the request and files.
- **Build the entire pack in one pass.**
- **Create complete production-ready files.**
- **Use repo/domain evidence** when available.
- **Label missing or unverifiable values Unknown.**
- **Do not invent** credentials, secrets, contacts, licenses, domains, approvals, test results, or external facts.
- **Do not output a plan only.**
- **Do not defer required work.**
- **Do not create decorative files.**
- **Do not duplicate responsibilities.**

## Quality Bar

- `enterprise_grade`
- `production_ready`
- `repo_aligned`
- `operator_usable`
- `developer_usable`
- `agent_safe`
- `validation_backed`
- `no_drift`

## Validation Gates

- `no_stubs`
- `no_scaffolds`
- `no_fake_validation`
- `no_duplicate_responsibilities`
- `all_required_files_complete`
- `unknowns_labeled`
- `zip_bundle_created`
- `zip_download_link_rendered`

## Stop Conditions

- **HALT** if required inputs are unavailable.
- **HALT** if build requires invented unverifiable facts.
- **HALT** if any required artifact would be stub-only or scaffold-only.
- **HALT** if validation cannot distinguish real checks from fake pass claims.
- **HALT** if zip cannot be created.
