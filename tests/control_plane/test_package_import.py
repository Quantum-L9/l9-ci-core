"""PR-B1 package skeleton import + foundation tests.

These exercise deliverable #5 ("python -c import test") and the dependency-free
foundations (canonical JSON, digests, schema registry). They must pass in an
environment installed with ``pip install --no-deps -e .`` -- i.e. using only the
standard library, with no PyYAML/jsonschema present.
"""

from __future__ import annotations

import importlib


def test_import_control_plane_package():
    """The package imports cleanly with no third-party dependency."""
    module = importlib.import_module("l9_ci_core.control_plane")
    assert module is not None


def test_control_plane_reexports_foundations():
    import l9_ci_core.control_plane as cp

    for name in ("dumps_canonical", "encode_canonical", "sha256_bytes", "sha256_canonical"):
        assert hasattr(cp, name), name


def test_all_skeleton_modules_import():
    for name in (
        "canonical_json",
        "changed_files",
        "context",
        "digests",
        "evaluator",
        "executor",
        "models",
        "planner",
        "registry",
        "results",
        "risk",
        "schemas",
    ):
        importlib.import_module(f"l9_ci_core.control_plane.{name}")


def test_planner_exposes_plan_gates_symbol():
    from l9_ci_core.control_plane.planner import plan_gates

    assert callable(plan_gates)


def test_canonical_json_is_deterministic_and_sorted():
    from l9_ci_core.control_plane.canonical_json import dumps_canonical

    a = {"b": 1, "a": 2, "nested": {"y": 1, "x": 2}}
    b = {"nested": {"x": 2, "y": 1}, "a": 2, "b": 1}
    assert dumps_canonical(a) == dumps_canonical(b)
    assert dumps_canonical(a) == '{"a":2,"b":1,"nested":{"x":2,"y":1}}'


def test_canonical_json_preserves_array_order():
    from l9_ci_core.control_plane.canonical_json import dumps_canonical

    assert dumps_canonical([3, 1, 2]) == "[3,1,2]"


def test_digest_shape_matches_schema_pattern():
    import re

    from l9_ci_core.control_plane.digests import sha256_bytes, sha256_canonical

    pattern = re.compile(r"^sha256:[0-9a-f]{64}$")
    assert pattern.match(sha256_bytes(b"hello"))
    assert pattern.match(sha256_canonical({"k": "v"}))


def test_semantic_digest_ignores_key_order():
    from l9_ci_core.control_plane.digests import sha256_canonical

    assert sha256_canonical({"a": 1, "b": 2}) == sha256_canonical({"b": 2, "a": 1})
