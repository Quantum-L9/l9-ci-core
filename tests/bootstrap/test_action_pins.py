from __future__ import annotations
import json, shutil, sys
from pathlib import Path
import pytest
FIXTURES = Path(__file__).parent.parent / "fixtures" / "action-pins"
import validate_action_pins as vap

# Inventory entries matching the immutable references used by the "valid"
# fixtures. External references require inventory provenance, so passing cases
# must ship a matching inventory (the correct contract: provenance is
# mandatory, not optional).
_INV_CHECKOUT = {
    "action": "actions/checkout",
    "kind": "action",
    "version": "v4.2.2",
    "commit_sha": "11bd71901bbe5b1630ceea73d27597364c9af683",
    "upstream_repository": "actions/checkout",
    "verification_method": "upstream-tag-resolution",
    "verified_at": "2026-07-01",
}
_INV_REUSABLE = {
    "action": "owner/repo/.github/workflows/ci.yml",
    "kind": "reusable_workflow",
    "version": "v1.0.0",
    "commit_sha": "11bd71901bbe5b1630ceea73d27597364c9af683",
    "upstream_repository": "owner/repo",
    "verification_method": "manual-upstream-commit-review",
    "verified_at": "2026-07-01",
}
_INV_DOCKER = {
    "action": "docker://alpine",
    "kind": "docker_action",
    "version": "3.20",
    "digest": "sha256:" + "a" * 64,
    "verification_method": "verified-image-digest",
    "verified_at": "2026-07-01",
}


def _write_inventory(root, entries):
    gov = root / ".github" / "governance"
    gov.mkdir(parents=True, exist_ok=True)
    (gov / "action-pins.lock.json").write_text(json.dumps({
        "schema_version": "1.0",
        "generated_at": "2026-07-01",
        "entries": {f"e{i}": e for i, e in enumerate(entries)},
    }))


def _run(fixture_name, tmp_path, root=None, inventory=None):
    wdir = tmp_path / ".github" / "workflows"
    wdir.mkdir(parents=True)
    shutil.copy(FIXTURES / fixture_name, wdir / fixture_name)
    _root = root or tmp_path
    if inventory is not None:
        _write_inventory(_root, inventory)
    out = tmp_path / "result.json"
    ec = vap.run(_root, wdir, out, "text", True)
    return ec, json.loads(out.read_text())


def test_valid_full_sha_passes(tmp_path):
    ec, d = _run("valid-full-sha.yml", tmp_path, inventory=[_INV_CHECKOUT])
    assert ec == 0 and d["result"] == "passed" and d["violations"] == []


def test_external_ref_with_empty_inventory_fails(tmp_path):
    # SHA reference with no inventory entries -> EMPTY_ACTION_PIN_INVENTORY.
    ec, d = _run("valid-full-sha.yml", tmp_path)
    assert ec == 1 and any(v["code"] == "EMPTY_ACTION_PIN_INVENTORY" for v in d["violations"])


def test_missing_inventory_entry_fails(tmp_path):
    # Inventory present but the used action is absent -> MISSING_INVENTORY_ENTRY.
    ec, d = _run("valid-full-sha.yml", tmp_path, inventory=[_INV_REUSABLE])
    assert ec == 1 and any(v["code"] == "MISSING_INVENTORY_ENTRY" for v in d["violations"])


def test_inventory_sha_mismatch_fails(tmp_path):
    bad = dict(_INV_CHECKOUT, commit_sha="0" * 40)
    ec, d = _run("valid-full-sha.yml", tmp_path, inventory=[bad])
    assert ec == 1 and any(v["code"] == "INVENTORY_SHA_MISMATCH" for v in d["violations"])


def test_no_external_refs_empty_inventory_passes(tmp_path):
    # Local-only workflow with no inventory is valid (no external references).
    wdir = tmp_path / ".github" / "workflows"
    wdir.mkdir(parents=True)
    (wdir / "local.yml").write_text(
        "on: push\njobs:\n  b:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - uses: ./.github/actions/local\n"
    )
    (tmp_path / ".github" / "actions" / "local").mkdir(parents=True)
    (tmp_path / ".github" / "actions" / "local" / "action.yml").write_text("name: local\n")
    out = tmp_path / "result.json"
    ec = vap.run(tmp_path, wdir, out, "text", True)
    d = json.loads(out.read_text())
    assert ec == 0 and d["result"] == "passed"


def test_invalid_version_tag_fails(tmp_path):
    ec, d = _run("invalid-version-tag.yml", tmp_path)
    assert ec == 1 and any(v["code"] == "FLOATING_ACTION_REF" for v in d["violations"])


def test_invalid_main_branch_fails(tmp_path):
    ec, d = _run("invalid-main-branch.yml", tmp_path)
    assert ec == 1 and d["result"] == "failed"


def test_invalid_master_branch_fails(tmp_path):
    ec, d = _run("invalid-master-branch.yml", tmp_path)
    assert ec == 1


def test_invalid_short_sha_fails(tmp_path):
    ec, d = _run("invalid-short-sha.yml", tmp_path)
    assert ec == 1 and any(v["code"] == "SHORT_SHA_REF" for v in d["violations"])


def test_invalid_reusable_workflow_tag_fails(tmp_path):
    ec, d = _run("invalid-reusable-workflow-tag.yml", tmp_path)
    assert ec == 1


def test_invalid_docker_tag_fails(tmp_path):
    ec, d = _run("invalid-docker-tag.yml", tmp_path)
    assert ec == 1


def test_valid_docker_digest_passes(tmp_path):
    ec, d = _run("valid-docker-digest.yml", tmp_path, inventory=[_INV_DOCKER])
    assert ec == 0 and d["result"] == "passed"


def test_valid_reusable_workflow_sha_passes(tmp_path):
    ec, d = _run("valid-reusable-workflow-sha.yml", tmp_path, inventory=[_INV_REUSABLE])
    assert ec == 0


def test_comment_only_no_violation(tmp_path):
    ec, d = _run("comment-only-reference.yml", tmp_path)
    assert d["result"] == "passed"


def test_no_workflow_files_is_error(tmp_path):
    wdir = tmp_path / ".github" / "workflows"
    wdir.mkdir(parents=True)
    out = tmp_path / "result.json"
    ec = vap.run(tmp_path, wdir, out, "text", True)
    assert ec == 2 and json.loads(out.read_text())["result"] == "error"


def test_metadata_fields_present(tmp_path):
    ec, d = _run("valid-full-sha.yml", tmp_path, inventory=[_INV_CHECKOUT])
    for k in ("files_scanned", "references_scanned", "remote_references"):
        assert k in d["metadata"]
