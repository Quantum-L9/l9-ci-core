<!-- L9_META
l9_schema: 1
parent: l9-devpack-compiler
layer: reference
role: assembly_contract
tags: [dpk, extract, assemble, filetree, file-signals, materialize, narrow-scope]
owner: igor_beylin
status: active
version: 1.4.0
updated: 2026-07-15
/L9_META -->

# Assembly Contract (extract → target filetree → narrow scope)

## Purpose

A spec's `file_plan` says *what* files exist and in *what status*. **Assembly materializes them.** When the source docs carry the real contents of `extract`/`existing` files, [`scripts/extract_files.py`](../scripts/extract_files.py) pulls those contents out, **organizes them into a target repo filetree** (`<out>/target/<path>`), maps **what's made vs what isn't**, scopes the dev pack **narrowly to the remainder**, and records the **signals each present file generates**. The extracted files **ship inside the pack** — they are not merely referenced.

This closes the compile loop:

```text
spec (facts) → extract+assemble (materialize what exists, map the tree, scope the delta) → build only the remainder → validate
```

## The status legend (`file_plan[].status`)

| Legend | status | Assembly behavior |
|:--:|---|---|
| `[E]` | `extract` | pull real content from a source doc block → write to `target/<path>`. **Ships in the pack.** |
| — | `existing` | same as extract if a source block exists; else fall back to the in-scope repo (`--repo`) if the file is present there. |
| `[B]` | `build` | **map-only** — appears in the filetree as `remaining`, no file written. Never stubbed. |
| `[A]` | `adapt` | **map-only** — `remaining`; the change target is named, not written. |
| `[D]` | `deferred` | listed in the tree as `deferred`; out of the remaining queue. |

A `[B/A]` / `[E/B]` dual legend resolves to its first token for status; the compiler records the dual intent in the map.

## Doctrine (non-negotiable)

1. **Extracted files are REAL content and ship in the pack.** The materialized bytes are the deliverable, verified against their contract — not a pointer, not a summary.
2. **Build/adapt files are map-only — never stubbed.** Zero-stub governs the *built* artifact; unbuilt work stays as a filetree node + a work-queue item, never a placeholder file that *looks* built.
3. **Never fabricate.** A spec file declared `extract`/`existing` with no source block → `missing_source` (reported in remaining work, never invented). A doc block not in the spec → `unplanned` (reported, never silently dropped or written).
4. **Presence ≠ conformance.** A `made` state means bytes were materialized, not that they are correct. Corruption (smart quotes, `...` elision copied from a doc) → `made_needs_review` signal. The extractor **never silently rewrites code** — that would be a fabrication/stub. It reports; a human or agent resolves.
5. **Scope narrows to the delta.** Extracted / existing / deferred / external compress out of the work queue; `build` / `adapt` / `missing_source` remain. The pack a downstream agent receives contains the finished files plus a work queue of *only what is left*.

## Source-doc formats supported

The extractor reads the two conventions real hand-off docs use — no fabrication either way:

- **`FILE: <path>` markers (primary).** The path is named on a `FILE:` line; the content follows **unfenced** until the next `FILE:` marker or end of doc. A group-boundary section heading swept in at the tail (an isolated prose line above the next marker) is trimmed; contiguous code is never mistaken for prose.
- **Path/heading-above-fence (fallback).** A bare path or a `#`-heading naming a file sits directly above a ```` ``` ```` fenced block; the fenced body is the content.

The first marked block for a path wins (later restatements/summaries do not clobber it). Matching to `file_plan` is by exact path, then `dir/` or `*` glob prefix, then basename.

## Outputs (all inside `<out>/`)

| Artifact | Contract | Carries |
|---|---|---|
| `target/<path>` | real bytes | every materialized `extract`/`existing` file |
| `FILETREE.md` | human map | made (extracted/existing) · remaining to build · unplanned |
| `.ai/filetree.yaml` | machine map | every node annotated `made` / `made_needs_review` / `missing_source` / `remaining` / `deferred` |
| `.ai/file-signals.yaml` | [`schemas/file-signals.schema.json`](../schemas/file-signals.schema.json) | per-file evidence — see below |

## The file-signal contract

Each present file emits a signal so downstream agents route on evidence, not tribal knowledge:

| Field | Source | Answers |
|---|---|---|
| `path`, `state` | assembly | is it made, and can it be trusted (`made` vs `made_needs_review`)? |
| `satisfies_contract` | `file_plan[].contract_ref` | which contract does this file implement? |
| `enforces_invariants` | `invariants[].enforcement` names the file | what must never break because of it? |
| `validated_by` | `commands[].cmd` references the file | which command proves it? |
| `populates_layer` | path heuristics | which DPK layer does it fill (`L4-contract`, `impl`, `L1/L4`)? |
| `source_doc` | provenance | where did the bytes come from (doc name or `repo`)? |
| `needs_review` | corruption scan | what must a human resolve before trusting it? |

## Usage

```bash
python3 scripts/extract_files.py <spec> \
  --docs <doc-or-dir> [<doc-or-dir> ...] \
  --out <pack-dir> \
  [--repo <in-scope-repo-root>] [--json]
```

**Exit codes:** `0` assembled clean (no `missing_source`, `unplanned`, or `needs_review`); `1` gaps to resolve (materialized, but the delta/reviews are reported); `2` unreadable input.

`1` is not failure — it is the honest report that unfinished/unverified work remains. The pack is still produced; the exit code tells a gate the delta is non-empty.
