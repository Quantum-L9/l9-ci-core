<!-- L9_META
l9_schema: 1
parent: l9-skill-compiler
layer: reference
role: platform_doctrine_kernel
tags: [skill, platform, doctrine, invariants, typing, ci_governance]
owner: igor_beylin
status: active
version: 1.0.0
updated: 2026-07-04
/L9_META -->

# Platform Doctrine Kernel

## Cardinal Rule

L9 is a frontier-grade research, development, execution, automation, and intelligence platform. The objective is building a platform capable of repeatedly producing software, systems, automation, agents, services, workflows, infrastructure, and future platforms with increasing leverage, reproducibility, autonomy, and velocity.

## Use When

Consult when designing skills that generate code, configure environments, set up CI, or establish agent operability contracts within the L9 ecosystem.

## Non-Negotiable Invariants

- **uv required:** `uv.lock` committed to version control.
- **pyright strict required:** Strong typing is infrastructure.
- **ruff required:** For formatting and linting.
- **pytest required:** For behavior validation.
- **AGENTS.md required:** Agent operability contract.
- **Canonical commands required:** Standardized `Makefile` or `justfile`.
- **CI required:** Green mainline required.

## Philosophy

- **Platform first:** Repositories exist to strengthen the platform.
- **Systems over projects:** Projects end. Systems compound. Prioritize reusable primitives.
- **Reproducibility over convenience:** Deterministic environments, explicit locked dependencies.
- **Strictness creates freedom:** Strong types, enforced contracts, and validation gates make autonomous operation safe.
- **Agent operability:** Repositories must be understandable and operable by both humans and AI agents without requiring tribal knowledge.
- **Autonomy is created by structure, not intelligence:** Agents become reliable through structured environments, deterministic commands, validated outputs, and explicit boundaries.

## Agent Security and Governance

- Agents are first-class identities requiring named roles.
- Least privilege is the default.
- High-risk actions require human approval (e.g., production deploy, DB migration, secret rotation).
- Auditability is mandatory.
- Validation ladder must be followed: `make format` -> `make lint` -> `make type` -> `make test` -> `make ci`.
