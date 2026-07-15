<!-- L9_META
l9_schema: 1
parent: l9-devpack-compiler
layer: reference
role: execution_package_contract
tags: [execution-package, repo-access, environment, credentials, contracts, work-queue, validation, stop-conditions]
owner: igor_beylin
status: active
version: 1.0.0
updated: 2026-07-15
/L9_META -->

# Execution Package Contract

## Purpose

The prompt explains **what** to build. The execution package makes the work **reproducible** and **prevents the agent from inventing missing infrastructure decisions**. It is the authoritative envelope an agent loads before doing any work. **A missing component is a STOP condition, never an invention** — the agent asks for it or labels it `Unknown` and halts, rather than guessing repo URLs, credentials, or contracts.

Emit as `.ai/execution-package.yaml`. All seven components are required (a component may be `Unknown` only if the agent then stops on needing it).

## The Seven Components

```yaml
schema_version: "1.0"
execution_package:

  # 1. Repository access — where the code is and what may be touched
  repository_access:
    id: <slug>
    url: <git url | Unknown>
    default_branch: <branch>
    working_branch: <feature branch to develop on>
    checkout: clone | worktree
    allowed_paths: [ src/<area>/, tests/<area>/ ]     # from the Task Contract
    prohibited_paths: [ src/generated/, db/migrations/ ]

  # 2. Environment access — how to make it run, deterministically
  environment_access:
    runtime: <language + version, e.g. node@20 / python@3.12>
    bootstrap: <single-command setup, e.g. scripts/bootstrap>   # deterministic; Local Reproducibility gate
    services: [ postgres, redis, ... ]                          # dependencies to stand up
    compose: <docker-compose.yml | Unknown>
    network_policy: <egress-allowlist | offline | Unknown>

  # 3. Scoped credentials — least privilege, referenced by name, NEVER embedded
  scoped_credentials:
    - name: <SECRET_NAME>            # env-var name only
      purpose: <what it authorizes>
      scope: <least-privilege scope>
      source: gh-actions-secret | env-settings | vault
      required: true | false
    # RULE: values live in the environment; prohibited_in_logs still applies.

  # 4. Authoritative contracts — the truth sources; do not contradict these
  authoritative_contracts:
    manifest: .ai/manifest.yaml
    repository_map: .ai/repository-map.yaml
    constraints: .ai/constraints.yaml
    task_contract: <path to the active Task Contract>
    interface_schemas: [ specs/contracts/api/<...>.yaml, specs/contracts/events/<...>.yaml ]
    adrs: [ docs/adr/<...>.md ]

  # 5. Phased work queue — ordered phases with entry/exit criteria
  work_queue:
    - phase: <name>
      task_contract: <id>
      entry_criteria: [ "<precondition>" ]
      exit_criteria: [ "<measurable done condition>" ]
      validation: [ <commands from component 6 that must pass> ]
    # Phases run in order; a phase cannot start until the previous phase's exit_criteria pass.

  # 6. Validation commands — the Layer-4 classes, as exact machine commands
  validation_commands:
    lint: <make lint>
    unit: <make test-unit>
    contract: <make test-contract>
    integration: <make test-integration | Unknown>
    evaluate: <make evaluate | n/a for non-AI>
    perf: <make test-perf | Unknown>
    resilience: <make test-resilience | Unknown>

  # 7. Stop conditions — when to halt instead of guessing or drifting
  stop_conditions:
    - any red-line unsatisfiable (no ops owner / no rollback / missing eval / broken runbook link)
    - a required execution-package component is Unknown and needed to proceed
    - a constraint budget is breached (latency/token/scope) with no compliant path
    - a change would require touching a prohibited_path
    - authoritative contracts conflict and the Authority Order cannot resolve it
    - readiness score < 80 after packing
```

## Completeness Gate

Before work begins, assert all seven components are present and each either has a concrete value or an explicit `Unknown` with a matching entry in `stop_conditions`. This is Gate E in `references/enforcement-gates.md`.

## Anti-Invention Rule

If the agent finds itself deciding a repo URL, a credential value, a service topology, a branch policy, or a contract that is not in the execution package, it MUST stop and request it — inventing infrastructure is the failure mode this envelope exists to prevent.
