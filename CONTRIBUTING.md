# Contributing

Thanks for helping build the Quantum-L9 CI platform. This repository is part of
the **L9 CI trio**:

- `l9-ci-sdk` — installable Python SDK and CLI (execution / governance runtime)
- `l9-ci-core` — reusable GitHub Actions workflows and governance defaults
- `l9-repo-template` — starter template for new L9 repos

## Doctrine

- The wire contract is **TransportPacket only**. Do not reintroduce superseded
  message contracts (such as `PacketEnvelope`) or references to retired legacy
  tooling — the governance scanners reject them.
- Keep responsibilities in their home repo: the SDK owns runtime logic, core owns
  the workflow / governance source of truth, and the template owns the thin
  bootstrap surface. Do not duplicate one repo's internals into another.

## Workflow

1. Branch from `main`.
2. Make your change with tests.
3. Run the validation commands below.
4. Open a pull request. The **PR Pipeline Gate** must pass, conversations must be
   resolved, and history must stay linear (no force pushes, no merge commits).

## Validation

```bash
# The governance/workflow tests import PyYAML; install the test deps first.
python -m pip install pyyaml pytest
python -m compileall .
pytest -q
```

Reusable workflows are the source of truth here. When you change a workflow
interface, update `docs/WORKFLOW_CONTRACT.md`, and update the template caller
in `l9-repo-template` if the input surface changes. The `trio-governance`
workflow enforces that no retired legacy contract or tooling references remain.
