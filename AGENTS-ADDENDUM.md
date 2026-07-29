## Repository execution contract

Authority order and boundaries are defined in [`AUTHORITY.md`](AUTHORITY.md) and executable policy in [`.l9/repo-workflow.json`](.l9/repo-workflow.json).

- Read `.l9/architecture.yaml`, `.l9/ownership.yaml`, and `.l9/sdk-compatibility.yaml` before changing Core.
- Run `make agent-check` before declaring work complete, committing, pushing, or opening a pull request.
- Do not bypass, weaken, or replace `agent-check` with direct tool commands.
- Targeted change gates add proof; they never replace the complete configured suite.
- Keep the root Makefile generated and delegation-only. Runtime behavior belongs in `tools/l9_repo/`; policy belongs in `.l9/repo-workflow.json`.
- Never add SDK-owned analysis, canonical evidence/findings, classification, severity normalization, repository graphing, Assurance decisions, repair planning, or learning behavior.
- Push and PR operations must remain clean-tree requiring, protected-branch rejecting, non-force, single-flight, and non-bypassable.
- Exit `1` means blocking findings. Exit `2` means invalid configuration, infrastructure, context, authority, or repository state.
