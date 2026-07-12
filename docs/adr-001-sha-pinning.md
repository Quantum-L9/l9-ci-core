# ADR-001: Mandatory SHA pinning for all GitHub Actions

**Status**: accepted  
**Date**: 2026-07-11

## Context

Version tags (e.g. `actions/checkout@v4`) are mutable. An attacker who compromises a
third-party action repository can redirect the tag to malicious code. This has happened
in the wild (e.g., the `tj-actions/changed-files` compromise in 2025).

## Decision

All `uses:` references must pin to a 40-character commit SHA or a Docker image digest.
Exceptions must be reviewed and documented in `action-pins.lock.json`.

## Consequences

Operators must update SHAs when upgrading actions. The lock file provides an audit trail.
