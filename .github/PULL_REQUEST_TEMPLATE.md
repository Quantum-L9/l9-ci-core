<!-- Shown when a PR is opened in the GitHub web UI. API/gh/MCP-created PRs do
     not get this prefilled — paste it manually there. -->

## What

<!-- One-line summary of the change. -->

## Why

<!-- Motivation. Link the issue: Closes #NNN -->

## Control-plane checklist (l9-ci-core v2)

- [ ] Respects the Core→SDK boundary — no SDK-owned behavior added to Core
- [ ] `.l9/` contracts unchanged, or changed intentionally with rationale
- [ ] Workflows declare least-privilege `permissions` (`contents: read`; only the
      publication workflow requests `checks: write`)
- [ ] External actions pinned to full 40-char commit SHAs
- [ ] `python3 -m unittest discover -s tests -p 'test_*.py'` passes
