<!-- L9_META
l9_schema: 1
parent: l9-skill-compiler
layer: reference
role: adapter_contract
tags: [skill-compiler, wiring, adapters, repo]
owner: igor_beylin
status: active
version: 1.1.0
updated: 2026-06-06
/L9_META -->

# Project Adapters (Repo Wiring)

`l9-wire-skill-into-repo` Step 3 loads repo-local adapters before registry edits.

## Probe order

1. `.claude/adapters/plasticos-repo-wiring.md`
2. `.claude/adapters/{any}-repo-wiring.md`
3. `.cursor/skills/l9-wire-skill-into-repo/references/project-adapter.md` (repo-local override template)

## L9 global skills

Register in **L9 Global Skills** table (`.claude/README.md`) and `AGENTS.md` with path `~/.cursor/skills/l9-{name}/`. Do not copy pack into `.claude/skills/`.

## Project skills

Register in **Project Skills** table with path `skills/{name}/` under `.claude/skills/`.
