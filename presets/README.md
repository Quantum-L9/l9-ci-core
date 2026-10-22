# presets

Only the locked consumer Biome contract remains here
(`typescript/biome.json` + `typescript/stamp.sh`; see `typescript/README.md`).

The per-language copy-first presets (`presets/python`, the preset workflows
and governance packs) implemented the old copy-first CI model and were
retired on 2026-10-22 (`AGENTS.md` §15) after the central required-workflow
canary was proven: GitHub organization rulesets require
[`.github/workflows/org-ci.yml`](../.github/workflows/org-ci.yml) directly,
Core selects governance, SDK compatibility, tool pins, and enforcement
centrally, and a consumer activates by declaring the Repository Execution V2
contract (`docs/ACTIVATION.md`). Do not copy `.github/workflows/l9-analysis.yml`
or `.github/governance/` into a consumer, and do not distribute CI from
`Quantum-L9/.github`.
