"""Declarative, risk-aware, fail-closed CI gate control plane.

Authoritative pipeline (see ``docs/CONTROL_PLANE.md``)::

    event -> context -> changed files -> risk -> registry ->
    plan -> execute -> enrich -> evaluate -> promotion decision

Module map:

* :mod:`~l9_ci_core.control_plane.canonical_json` — deterministic JSON +
  content hashing primitives.
* :mod:`~l9_ci_core.control_plane.digests` — source/semantic policy digests.
* :mod:`~l9_ci_core.control_plane.schemas` — JSON-Schema loading + validation.
* :mod:`~l9_ci_core.control_plane.models` — typed domain vocabulary.
* :mod:`~l9_ci_core.control_plane.context` — event-context normalization (PR-B2).
* :mod:`~l9_ci_core.control_plane.changed_files` — changed-file acquisition (PR-B2).
* :mod:`~l9_ci_core.control_plane.registry` — gate-registry loader (PR-B2).
* :mod:`~l9_ci_core.control_plane.risk` — deterministic risk classifier (PR-B2).
* :mod:`~l9_ci_core.control_plane.planner` — deterministic planner (later PR).
* :mod:`~l9_ci_core.control_plane.executor` — safe gate executor (later PR).
* :mod:`~l9_ci_core.control_plane.results` — canonical result enrichment (later PR).
* :mod:`~l9_ci_core.control_plane.evaluator` — fail-closed evaluator (later PR).

No LLM participates in registration, classification, planning, execution
selection, result normalization, hashing, or promotion evaluation.
"""

SCHEMA_VERSION = "1.0"
