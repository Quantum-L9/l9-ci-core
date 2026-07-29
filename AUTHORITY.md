# Authority Contract

## Identity

- Artifact: `l9-ci-core-repository-execution-runtime`
- Version: `4.3.0`
- Status: `authoritative`
- Beneficiary: `Quantum-L9/l9-ci-core`
- Scope: repository execution, local evidence, and guarded Git mutation

## Precedence

Authority is resolved in this order:

1. Target-repository law: `AGENTS.md`, `.l9/architecture.yaml`, `.l9/ownership.yaml`, `.l9/sdk-compatibility.yaml`.
2. Component executable policy: `.l9/repo-workflow.json`.
3. Component validation grammar: `.l9/repo-workflow.schema.json`.
4. Runtime behavior: `tools/l9_repo/`.
5. Generated facade: `Makefile` from `tools/l9_repo/Makefile.template`.
6. Derived operator documents listed in the executable policy.

A lower source may explain a higher source but may not widen, weaken, or contradict it.

## Invariants

- `agent-check` is the single non-mutating completion proof.
- Targeted gates add evidence and never replace the full configured suite.
- Exit `0` means success, `1` means blocking findings, and `2` means invalid configuration, infrastructure, context, or repository state.
- Validation must preserve the initial Git subject, policy digest, index, tracked worktree, and untracked-file set.
- Push and PR mutation require a clean admissible state, a non-protected branch, no in-progress Git operation, a valid lock, and a passing completion proof.
- Commands are argument vectors. Shell-string execution, force push, protected-branch mutation, hidden bypasses, and mutating validation are prohibited.
- Core orchestration authority is preserved. SDK analysis, canonical evidence/findings, classification, severity normalization, repository graphing, Assurance decisions, repair planning, and learning remain outside this component.

## Derivation rule

`README.md`, `OPERATIONS.md`, `VALIDATION.md`, `MANIFEST.md`, and `AGENTS-ADDENDUM.md` are derived documents. When they conflict with executable policy or target-repository law, they are wrong and must be regenerated or corrected.
