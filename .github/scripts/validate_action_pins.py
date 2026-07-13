#!/usr/bin/env python3
"Bootstrap gate: workflow/action-pins"
from __future__ import annotations
import argparse, json, re, sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from l9_bootstrap.models import GateResult, ResultStatus
from l9_bootstrap.output import write_json, write_json_stdout
from l9_bootstrap.paths import repo_root, safe_resolve
from l9_bootstrap.workflow_scan import iter_uses_references, iter_workflow_files
from l9_bootstrap.yaml_loader import load_yaml_file
from l9_bootstrap.schema_loader import (
    SchemaUnavailable,
    format_error as _fmt_err,
    load_validator,
    schema_errors as _schema_errors,
)

GATE_ID = "workflow/action-pins"
_RE_FULL_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
_RE_DOCKER_DIGEST = re.compile(r"^sha256:[0-9a-fA-F]{64}$")
_RE_SHORT_SHA = re.compile(r"^[0-9a-fA-F]{7,39}$")


def _classify_ref(ref: str) -> str:
    # Local GitHub Action syntax is precise: it must begin with "./". Any other
    # spelling (e.g. ".github/actions/foo") is not a valid local reference and
    # is left to the remote-ref path so it fails deterministically rather than
    # being leniently reclassified as local.
    if ref.startswith("./"):
        return "local"
    if ref.startswith("docker://"):
        tag_part = ref[len("docker://"):]
        if "@" in tag_part and _RE_DOCKER_DIGEST.match(tag_part.split("@", 1)[1]):
            return "digest"
        return "mutable"
    if "@" in ref:
        _, pin = ref.rsplit("@", 1)
        if _RE_FULL_SHA.match(pin):
            return "sha"
        if _RE_SHORT_SHA.match(pin):
            return "short_sha"
        return "mutable"
    return "mutable"


def _validate_local(ref: str, root: Path):
    # A local action reference must be explicitly repo-relative ("./..."). Note
    # str.lstrip strips a *character set*, not a prefix -- the previous
    # implementation mangled "./.github/..." into "github/..." and turned
    # "./../escape" into "escape", defeating traversal detection. Strip exactly
    # the leading "./" once.
    if not ref.startswith("./"):
        return f"Local action reference must start with './': {ref!r}"
    rel = ref[2:]
    try:
        target = safe_resolve(root, rel)
    except ValueError as exc:
        return str(exc)
    if not target.exists():
        return f"Local action target does not exist: {target}"
    if target.is_dir():
        if not (target / "action.yml").exists() and not (target / "action.yaml").exists():
            return f"Local action directory missing action.yml/action.yaml: {target}"
    else:
        if target.suffix.lower() not in (".yml", ".yaml"):
            return f"Local action file must be a .yml/.yaml file: {target}"
    return None


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _is_real_iso_date(value) -> bool:
    """True iff ``value`` is a string ``YYYY-MM-DD`` naming a real calendar
    date. ``date.fromisoformat`` rejects impossible dates such as 2026-13-40
    and 2026-02-30, so a schema-level string/pattern check is insufficient.
    """
    if not isinstance(value, str) or not _ISO_DATE_RE.match(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _validate_inventory_dates(raw) -> list:
    """Return a list of (field, value) tuples for every date field in the
    inventory that is not a real ISO-8601 calendar date. Covers the top-level
    ``generated_at`` and each entry's ``verified_at``.
    """
    bad = []
    if isinstance(raw, dict):
        gen = raw.get("generated_at")
        if not _is_real_iso_date(gen):
            bad.append(("generated_at", gen))
        entries = raw.get("entries", {})
        if isinstance(entries, dict):
            for key, entry in entries.items():
                if isinstance(entry, dict):
                    va = entry.get("verified_at")
                    if not _is_real_iso_date(va):
                        bad.append((f"entries.{key}.verified_at", va))
    return bad


def _validate_inventory_schema(raw, root: Path):
    """Return a list of schema error messages for the inventory document.

    Fails closed: the action-pins-lock schema is a required dependency, so a
    missing schema (or missing jsonschema) raises SchemaUnavailable, which the
    caller converts into an ``error`` result.
    """
    validator = load_validator(root, "action-pins-lock.schema.json")
    return [_fmt_err(e) for e in _schema_errors(validator, raw)]


def run(root, workflow_dir, output_json, fmt, quiet):
    result = GateResult(gate_id=GATE_ID, result=ResultStatus.passed)
    inventory_path = root / ".github" / "governance" / "action-pins.lock.json"
    inventory: dict = {}
    if inventory_path.exists():
        try:
            raw = json.loads(inventory_path.read_text(encoding="utf-8"))
            inventory = raw.get("entries", {}) if isinstance(raw, dict) else {}
        except Exception as exc:
            result.add_violation(code="INVENTORY_PARSE_ERROR", message=str(exc))
            result.result = ResultStatus.error
            return _emit(result, output_json, fmt, quiet, 2)
        # When an inventory is present it must itself satisfy the lock schema;
        # a malformed inventory cannot be trusted to gate pins. The schema is a
        # required dependency (fail closed).
        try:
            schema_errors = _validate_inventory_schema(raw, root)
        except SchemaUnavailable as exc:
            result.add_violation(code="SCHEMA_UNAVAILABLE", message=str(exc))
            result.result = ResultStatus.error
            return _emit(result, output_json, fmt, quiet, 2)
        if schema_errors:
            for msg in schema_errors:
                result.add_violation(code="INVENTORY_SCHEMA_INVALID", message=msg,
                                     path=str(inventory_path.relative_to(root)))
            result.result = ResultStatus.error
            return _emit(result, output_json, fmt, quiet, 2)
        # Date fields must name real calendar dates. A schema string/pattern
        # match still admits impossible values (e.g. 2026-13-40); reject them.
        bad_dates = _validate_inventory_dates(raw)
        if bad_dates:
            for field, value in bad_dates:
                result.add_violation(code="INVENTORY_DATE_INVALID",
                    message=f"{field}={value!r} is not a valid ISO-8601 calendar date.",
                    path=str(inventory_path.relative_to(root)))
            result.result = ResultStatus.error
            return _emit(result, output_json, fmt, quiet, 2)
    try:
        workflow_files = list(iter_workflow_files(workflow_dir, root))
    except ValueError as exc:
        result.add_violation(code="RESOURCE_LIMIT", message=str(exc))
        result.result = ResultStatus.error
        return _emit(result, output_json, fmt, quiet, 2)
    if not workflow_files:
        result.add_violation(code="NO_WORKFLOW_FILES", message="No workflow files found.")
        result.result = ResultStatus.error
        return _emit(result, output_json, fmt, quiet, 2)
    action_to_inv = {e.get("action", ""): e for e in inventory.values() if isinstance(e, dict)} if isinstance(inventory, dict) else {}
    used_actions: set = set()
    files_scanned = remote_refs = local_refs = docker_refs = refs_scanned = 0
    # Collect immutable references first so we can enforce the invariant: every
    # external (remote/docker-digest) reference MUST have an inventory entry.
    # An empty inventory is acceptable ONLY when there are no external refs.
    _external_refs: list = []
    for wf_path in workflow_files:
        files_scanned += 1
        try:
            wf = load_yaml_file(wf_path)
        except Exception as exc:
            result.add_violation(code="WORKFLOW_PARSE_ERROR", message=f"{wf_path.name}: {exc}", path=str(wf_path.relative_to(root)))
            result.result = ResultStatus.error
            continue
        if not isinstance(wf, dict):
            continue
        for jid, idx, name, uses_val, line_no, annotation in iter_uses_references(wf):
            refs_scanned += 1
            kind = _classify_ref(uses_val)
            _rel = str(wf_path.relative_to(root))
            if kind == "local":
                local_refs += 1
                err = _validate_local(uses_val, root)
                if err:
                    result.add_violation(code="LOCAL_ACTION_INVALID", message=err, path=str(wf_path.relative_to(root)), line=line_no or None)
                    result.result = ResultStatus.failed
                continue
            if kind in ("mutable", "short_sha"):
                remote_refs += 1
                code = "FLOATING_ACTION_REF" if kind == "mutable" else "SHORT_SHA_REF"
                result.add_violation(code=code, message=f"{wf_path.name}: job={jid} step={name!r} uses={uses_val!r}", path=str(wf_path.relative_to(root)), line=line_no or None)
                result.result = ResultStatus.failed
                continue
            if kind == "sha":
                remote_refs += 1
                _, pin = uses_val.rsplit("@", 1)
                action_name = uses_val.rsplit("@", 1)[0]
                used_actions.add(action_name)
                _external_refs.append((action_name, "sha", pin, _rel, line_no, annotation))
            elif kind == "digest":
                docker_refs += 1
                digest = uses_val[len("docker://"):].split("@", 1)[1]
                action_name = uses_val.rsplit("@", 1)[0]
                used_actions.add(action_name)
                _external_refs.append((action_name, "digest", digest, _rel, line_no, annotation))

    # Invariant: external references require inventory provenance. An empty
    # inventory is only valid when there are zero external references.
    if _external_refs and not action_to_inv:
        result.add_violation(
            code="EMPTY_ACTION_PIN_INVENTORY",
            message=("External references exist but the action pin inventory "
                     "contains no entries."))
        result.result = ResultStatus.failed
    # Every immutable reference must have a matching inventory entry, regardless
    # of inventory size, and the pinned value must match the recorded one.
    for action_name, ekind, value, rel, line_no, annotation in _external_refs:
        inv = action_to_inv.get(action_name)
        if inv is None:
            if action_to_inv:
                result.add_violation(code="MISSING_INVENTORY_ENTRY", message=f"{action_name!r} missing from action-pins.lock.json", path=rel, line=line_no or None)
                result.result = ResultStatus.failed
            continue
        if ekind == "sha" and inv.get("commit_sha", "") != value:
            result.add_violation(code="INVENTORY_SHA_MISMATCH", message=f"{action_name}: workflow={value} inventory={inv.get('commit_sha')}", path=rel, line=line_no or None)
            result.result = ResultStatus.failed
        if ekind == "digest" and inv.get("digest", "") != value:
            result.add_violation(code="INVENTORY_DIGEST_MISMATCH", message=f"{action_name}: workflow={value} inventory={inv.get('digest')}", path=rel, line=line_no or None)
            result.result = ResultStatus.failed
        # Readable version annotation checks (warnings, not blocking).
        recorded_version = str(inv.get("version", "")).strip()
        if not annotation:
            result.add_warning(code="MISSING_VERSION_ANNOTATION", message=f"{action_name} pinned by {ekind} lacks a readable version annotation (expected '# {recorded_version}').")
        elif recorded_version and annotation != recorded_version:
            result.add_warning(code="STALE_VERSION_ANNOTATION", message=f"{action_name} annotation {annotation!r} does not match inventory version {recorded_version!r}.")
    for name, entry in action_to_inv.items():
        if name and name not in used_actions:
            result.add_warning(code="UNUSED_INVENTORY_ENTRY", message=f"{name!r} in action-pins.lock.json not found in any workflow.")
    result.metadata = {"files_scanned": files_scanned, "references_scanned": refs_scanned, "remote_references": remote_refs, "local_references": local_refs, "docker_references": docker_refs, "inventory_entries": len(action_to_inv)}
    if result.result == ResultStatus.passed:
        result.finalize(); exit_code = 0
    else:
        try: result.finalize()
        except ValueError: pass
        exit_code = 1 if result.result == ResultStatus.failed else 2
    return _emit(result, output_json, fmt, quiet, exit_code)


def _emit(result, output_json, fmt, quiet, exit_code):
    data = result.to_dict()
    if output_json: write_json(data, output_json)
    if fmt == "json": write_json_stdout(data)
    elif not quiet or exit_code != 0:
        print(f"[{result.result.value.upper()}] {GATE_ID}")
        for v in result.violations: print(f"  VIOLATION {v.code}: {v.message}")
        for w in result.warnings: print(f"  WARNING {w.code}: {w.message}")
    return exit_code


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--root", default=None); p.add_argument("--workflow-dir", default=None)
    p.add_argument("--output-json", default=None); p.add_argument("--format", choices=["text","json"], default="text")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)
    try:
        _root = repo_root(args.root)
        _wdir = Path(args.workflow_dir) if args.workflow_dir else _root / ".github" / "workflows"
        return run(_root, _wdir, Path(args.output_json) if args.output_json else None, args.format, args.quiet)
    except Exception as exc:
        r = GateResult(gate_id=GATE_ID, result=ResultStatus.error)
        r.add_violation(code="EXECUTION_ERROR", message=str(exc))
        data = r.to_dict()
        if args.output_json: write_json(data, Path(args.output_json))
        if args.format == "json": write_json_stdout(data)
        else: print(f"[ERROR] {GATE_ID}: {exc}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    sys.exit(main())
