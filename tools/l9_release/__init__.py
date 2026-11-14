"""The single upstream release engine for Quantum-L9 repositories.

A repository opts in by committing ``.l9/release.json`` (``l9.release/v1``).
Core owns everything else: the identity derivation, the pending-work decision,
the reproducible build, the publication, and the verification. Stages:

``plan``     observe remote state and decide what is still pending (no writes)
``build``    build twice from a clean clone of the exact commit; byte-identical
             or fail; write ``SHA256SUMS`` and a provenance manifest
``publish``  exact annotated tag, GitHub Release, assets, optional major tag
``verify``   re-download every asset, check digests, prove the tag target

This package is deliberately separate from ``tools.l9_repo``: the repository
execution runtime exposes no publication command (AGENTS.md §11, §14, and
``tests/repo_execution/test_boundary.py``). Standard library only, because the
``release-engine`` composite action imports it from a pinned Core checkout on
a consumer runner with no third-party packages.
"""
