<!-- L9_META
l9_schema: 1
parent: l9-skill-compiler
layer: reference
role: file_contract
tags: [skill, metadata, search, versioning, auditability, prompt_primitives]
owner: igor_beylin
status: active
version: 1.1.1
updated: 2026-06-06
/L9_META -->

# Metadata Standard

## Purpose

Every file in this Skill pack must include compact metadata.

Metadata supports search, routing, ownership, versioning, auditability, source tracking, prompt-as-primitive handling, and future maintenance.

Metadata identifies a file. It does not replace the file's actual instructions.

## Required Fields

### `SKILL.md` — single YAML frontmatter (canonical)

All discovery and audit metadata lives in one frontmatter block. Do **not** add a separate `SKILL_META` HTML comment.

```yaml
---
name: skill-name              # required — must match directory name
description: lowercase what + when triggers  # required — agent discovery
skill_schema: 1
layer: control_plane
role: skill_entrypoint
tags: [tag1, tag2]
owner: igor_beylin
status: active | experimental | deprecated
version: 1.0.0
updated: YYYY-MM-DD
sources:                      # optional — kernel/source provenance only
  - source-file.yaml
---
```

- `name` and `description` are required for agent discovery (Cursor, Claude Code).
- Do not duplicate `name` as `origin` — `name` is the canonical identifier.
- Optional fields (`sources`, `disable-model-invocation`) only when needed.

### Other markdown files (`references/`, etc.)

Use an HTML comment block at the top:

```yaml
skill_schema: 1
parent: skill-compiler        # owning skill pack
layer: reference | script | asset
role: pack_contract | ...
tags: [...]
owner: igor_beylin
status: active
version: 1.0.0
updated: YYYY-MM-DD
```

Each non-SKILL file must include a short Purpose paragraph in the comment or body.

## YAML Format

Use YAML comments at the top of non-SKILL config files when needed. Prefer keeping all skill metadata in `SKILL.md` — do not create `agents/openai.yaml`.

## Script Format

Use language-native comments at the top of script files. Include the same required fields and a short purpose statement.

## Asset Metadata

When an asset format cannot safely hold comments, store metadata in a sibling sidecar file using this naming pattern:

```text
asset_filename.ext.meta.md
```

The sidecar must use markdown metadata and must identify the asset it describes.

## Prompt Primitive Rule

Treat prompts, kernels, contracts, checklists, schemas, and output modes as first-class operational primitives.

Each primitive must be:

- named
- scoped
- versioned
- searchable
- linked from `SKILL.md`
- validated before packaging
- removable without breaking unrelated files

## Validation Rules

A file fails metadata validation when:

- metadata is missing
- required fields are missing
- `layer` does not match file location or purpose
- `role` does not match file behavior
- tags are vague or useless for search
- purpose is missing or inaccurate
- metadata contains secrets
- metadata contains large doctrine instead of identification
