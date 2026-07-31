# Component Manifest

## Identity

- Artifact: `l9-ci-core-repository-execution-runtime`
- Version: `4.3.0`
- Status: authoritative component pack
- Beneficiary: `Quantum-L9/l9-ci-core`
- Mutation authorization: none during installation

## Authority map

- `AUTHORITY.md`: human-readable precedence, boundaries, and invariants.
- `.l9/repo-workflow.json`: executable component authority and release identity.
- `.l9/repo-workflow.schema.json`: closed validation grammar.
- `requirements-repo-runtime.txt`: component-owned pinned runtime and quality dependencies.
- `tools/l9_repo/`: deterministic execution engine.
- `tools/l9_repo/Makefile.template`: canonical generated facade.
- `Makefile`: byte-identical generated adapter.

## Runtime modules

- `__main__.py`: command orchestration, validation, status, and Git operations.
- `change_policy.py`: changed-file resolution, gate selection, and companion obligations.
- `authority.py`: release identity, authority order, and derived-document validation.
- `contract_wiring.py`: target authority discoverability proof.
- `reporting.py`: deterministic redacted JSON and Markdown receipts.
- `locking.py`: PID-aware single-flight mutation lock.
- `push_preflight.py`: guarded mutation preflight.

## Operator and evidence files

- `README.md`: entrypoint.
- `OPERATIONS.md`: integration, commands, evidence, failures, and recovery.
- `AGENTS-ADDENDUM.md`: target-agent-law insertion.
- `VALIDATION.md`: evidence-backed release decision and residual gate.
- `validation_report.yaml`, `validation_checks.jsonl`, `validation_findings.jsonl`: machine-readable validation evidence.
- `MANIFEST.sha256`: integrity map for release files other than itself and runtime outputs.

## Exclusions

The pack does not duplicate Core workflows, actions, full tests, dependency lockfiles, target `AGENTS.md`, target `.l9` architecture/ownership contracts, SDK semantics, Assurance decisions, repair behavior, or learning systems.
