#!/usr/bin/env python3
"""Bootstrap gate: dependencies/ci-lock"""
from __future__ import annotations
import argparse, hashlib, json, re, sys
from datetime import date, datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from l9_bootstrap.models import GateResult, ResultStatus
from l9_bootstrap.output import write_json, write_json_stdout
from l9_bootstrap.paths import repo_root
from l9_bootstrap.workflow_scan import iter_run_blocks, iter_workflow_files
from l9_bootstrap.yaml_loader import load_yaml_file
from l9_bootstrap import schema_loader

GATE_ID = "dependencies/ci-lock"
# A job/install is "bootstrap-managed" (and therefore held to the full
# require-hashes standard, no baseline waiver) when it installs the bootstrap
# lock. This is the deterministic, content-based signal for PR-A-managed
# bootstrap jobs.
_BOOTSTRAP_LOCK_MARKER = "requirements/bootstrap.lock"
_PIP_INSTALL_RE   = re.compile(r"\bpip(?:3)?\s+install\b")
_REQUIRE_HASHES   = re.compile(r"--require-hashes")
# Options that, if present in the production lock, subvert hash-pinned resolution
# from the default TLS index. A compliant lock must not carry any of these.
_FORBIDDEN_LOCK_OPTIONS = frozenset({
    "--index-url", "-i", "--extra-index-url", "--find-links", "-f",
    "--trusted-host", "--pre", "--editable", "-e",
})
# Exact hash length per algorithm: sha256 -> 64 hex, sha512 -> 128 hex.
_HASH_LINE        = re.compile(r"--hash=(?:sha256:[0-9a-fA-F]{64}|sha512:[0-9a-fA-F]{128})")
_EDITABLE_NO_DEPS = re.compile(r"--no-deps\s+-e\b|-e\b.*--no-deps")
_UPGRADE_RE       = re.compile(r"--upgrade\b|-U\b")
_BRANCH_URL_RE    = re.compile(r"git\+https://[^@]+@(?!refs/tags/)[a-zA-Z]")
_UNBOUNDED_RE     = re.compile(r"pip(?:3)?\s+install\s+(?!-)([a-zA-Z][a-zA-Z0-9_\-\.]+)(?:\s|$)")
# A pinned requirement: name==version (extras allowed) at the start of a
# logical line.
_EXACT_PIN_RE     = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[^\]]+\])?==[^\s]+")

# Workflow-level codes that a signed, time-bounded exception may waive.
_EXCEPTABLE_CODES = frozenset({
    "UNCONDITIONAL_PIP_UPGRADE",
    "BRANCH_URL_INSTALL",
    "UNBOUNDED_PIP_INSTALL",
    "UNPINNED_PIP_INSTALL",
})
_MAX_EXCEPTION_WINDOW_DAYS = 30


def _logical_requirement_lines(text: str):
    """Yield (line_number, joined_text) folding pip backslash continuations.

    A requirement plus its ``--hash=`` fragments are frequently split across
    physical lines with trailing backslashes. Downstream checks need the whole
    logical requirement, so we join continuations while remembering the line
    number where the requirement started.
    """
    buffer = ""
    start_line = 0
    for idx, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not buffer:
            start_line = idx
        if stripped.endswith("\\"):
            buffer += stripped[:-1].rstrip() + " "
            continue
        buffer += stripped
        logical = buffer.strip()
        buffer = ""
        if logical:
            yield start_line, logical
    if buffer.strip():
        yield start_line, buffer.strip()


def _check_lock_file(lock_path: Path):
    violations = []
    if not lock_path.exists():
        return [{"code": "LOCK_FILE_MISSING", "message": f"{lock_path} not found"}]
    text = lock_path.read_text(encoding='utf-8')
    logical = [
        (line_no, body)
        for line_no, body in _logical_requirement_lines(text)
        if body and not body.lstrip().startswith("#")
    ]
    if not logical:
        return [{"code": "LOCK_FILE_EMPTY",
                 "message": f"{lock_path} declares no requirements"}]
    has_any_hash = _HASH_LINE.search(text)
    if not has_any_hash:
        violations.append({"code": "LOCK_MISSING_HASH",
                           "message": f"{lock_path} has no --hash= entries; regenerate with --generate-hashes"})
    for line_no, body in logical:
        # Option lines are not requirements, but they are not all benign. A
        # locked, hash-verified install must resolve only from the default index
        # over TLS: index/source overrides in the lock silently defeat that
        # guarantee, so they are forbidden here (HIGH-07). Bare --hash= folding
        # remnants and other pip options are ignored.
        if body.startswith("-"):
            opt = body.split("=", 1)[0].split()[0].strip().lower()
            if opt in _FORBIDDEN_LOCK_OPTIONS:
                violations.append({"code": "LOCK_FORBIDDEN_OPTION",
                                   "message": f"{lock_path}: forbidden lock option {opt!r} "
                                              f"(index/source overrides defeat hash-pinned resolution): {body!r}",
                                   "line": line_no})
            continue
        if not _EXACT_PIN_RE.match(body):
            violations.append({"code": "LOCK_REQUIREMENT_NOT_EXACT",
                               "message": f"{lock_path}: requirement not exactly pinned: {body!r}",
                               "line": line_no})
            continue
        if "--hash=" not in body:
            violations.append({"code": "LOCK_MISSING_HASH",
                               "message": f"{lock_path}: requirement missing --hash=: {body!r}",
                               "line": line_no})
        elif not _HASH_LINE.search(body):
            violations.append({"code": "LOCK_MISSING_HASH",
                               "message": f"{lock_path}: requirement has malformed --hash=: {body!r}",
                               "line": line_no})
    return violations


def _load_exceptions(root: Path, result: GateResult):
    """Load and validate the dependency-exception registry.

    Returns a set of (violation_code, workflow, job) tuples that are actively
    waived. Any malformed, expired, over-window, or wildcard exception is
    rejected (recorded as a violation) and never grants a waiver.
    """
    path = root / ".github" / "governance" / "ci-dependency-exceptions.yaml"
    if not path.exists():
        path = root / ".github" / "governance" / "ci-dependency-exceptions.json"
    if not path.exists():
        return set()
    try:
        if path.suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            data = load_yaml_file(path)
    except Exception as exc:
        result.add_violation(code="EXCEPTION_FILE_MALFORMED",
                             message=f"{path.name}: {exc}", path=str(path.name))
        result.result = ResultStatus.error
        return set()
    # Schema validation is mandatory: the exception registry is a security
    # control and cannot be trusted if it does not conform. Missing jsonschema
    # or schema file fails closed (error / exit 2).
    try:
        validator = schema_loader.load_validator(root, "ci-dependency-exceptions")
    except schema_loader.SchemaUnavailable as exc:
        result.add_violation(code="SCHEMA_UNAVAILABLE", message=str(exc), path=str(path.name))
        result.result = ResultStatus.error
        return set()
    errors = schema_loader.schema_errors(validator, data)
    if errors:
        for err in errors:
            result.add_violation(code="EXCEPTION_SCHEMA_INVALID",
                                 message=schema_loader.format_error(err), path=str(path.name))
        result.result = ResultStatus.error
        return set()
    active = set()
    today = datetime.now(timezone.utc).date()
    for entry in (data or {}).get("exceptions", []):
        code = entry.get("violation_code", "")
        path_val = entry.get("path", "")
        line_or_step = entry.get("line_or_step", "")
        expires_raw = entry.get("expires_on", "")
        created_raw = entry.get("created_at", "")
        # Wildcards are forbidden even though the schema pattern already blocks
        # them; kept as defense in depth for JSON inputs bypassing the pattern.
        if "*" in path_val or "*" in line_or_step:
            result.add_violation(code="EXCEPTION_WILDCARD_FORBIDDEN",
                                 message=f"wildcard exception not allowed: {path_val}/{line_or_step}")
            result.result = ResultStatus.failed
            continue
        try:
            expires = date.fromisoformat(expires_raw)
            created = date.fromisoformat(created_raw)
        except Exception:
            result.add_violation(code="EXCEPTION_SCHEMA_INVALID",
                                 message=f"invalid created_at/expires_on: {created_raw!r}/{expires_raw!r}")
            result.result = ResultStatus.error
            continue
        if expires < today:
            result.add_violation(code="EXCEPTION_EXPIRED",
                                 message=f"exception for {code} on {path_val}:{line_or_step} expired {expires_raw}")
            result.result = ResultStatus.failed
            continue
        # The window is measured from the exception's own creation date, not
        # from today. Measuring against today would let an over-long exception
        # (e.g. a 90-day grant) silently become valid once the clock is within
        # the window of expiry.
        if (expires - created).days > _MAX_EXCEPTION_WINDOW_DAYS:
            result.add_violation(code="EXCEPTION_WINDOW_TOO_LONG",
                                 message=f"exception for {code} spans {(expires - created).days}d from {created_raw} to {expires_raw}, exceeding {_MAX_EXCEPTION_WINDOW_DAYS}-day window")
            result.result = ResultStatus.failed
            continue
        active.add((code, path_val, line_or_step))
    return active


def _is_excepted(active, code, rel, jid, name):
    """An exception waives a finding only when the violation code, the workflow
    path, and the job/step identity all match. ``line_or_step`` may be either
    a bare step name or a ``job/step`` composite."""
    for exc_code, path_val, line_or_step in active:
        if exc_code != code:
            continue
        if path_val not in (rel, Path(rel).name):
            continue
        composite = f"{jid}/{name}"
        if line_or_step in (jid, name, composite):
            return True
    return False

def _normalize_cmd(run_block: str) -> str:
    """Collapse line-continuations and whitespace so a stored command identity
    is stable across cosmetic reflow but changes on any meaningful edit."""
    text = run_block.replace("\\\n", " ")
    return " ".join(text.split())


def _command_sha256(run_block: str) -> str:
    return hashlib.sha256(_normalize_cmd(run_block).encode()).hexdigest()


def _load_baseline(root: Path, result: GateResult):
    """Load and validate the phased-enforcement legacy baseline.

    Returns ``(index, digest)`` where ``index`` maps
    ``(code, path, job, step)`` -> ``command_sha256`` for every recorded legacy
    observation, and ``digest`` is the file's self-declared baseline_digest.

    Fail-closed: a missing baseline yields an empty index (no waivers). A
    malformed baseline, an unavailable schema, a schema-invalid document, a
    wildcard entry, or a self-digest that does not match the recomputed digest
    are all fatal (error / exit 2) so a tampered baseline can never widen the
    set of tolerated findings.
    """
    path = root / ".github" / "governance" / "ci-dependency-baseline.json"
    if not path.exists():
        return {}, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        result.add_violation(code="BASELINE_FILE_MALFORMED", message=f"{path.name}: {exc}", path=path.name)
        result.result = ResultStatus.error
        return {}, None
    try:
        validator = schema_loader.load_validator(root, "ci-dependency-baseline")
    except schema_loader.SchemaUnavailable as exc:
        result.add_violation(code="SCHEMA_UNAVAILABLE", message=str(exc), path=path.name)
        result.result = ResultStatus.error
        return {}, None
    errors = schema_loader.schema_errors(validator, data)
    if errors:
        for err in errors:
            result.add_violation(code="BASELINE_SCHEMA_INVALID", message=schema_loader.format_error(err), path=path.name)
        result.result = ResultStatus.error
        return {}, None
    entries = data.get("entries", [])
    # Recompute the digest over the canonical entry list and require it to match
    # the file's self-declared digest: this binds the metadata we later expose
    # to the exact content that was reviewed.
    canon = json.dumps(entries, sort_keys=True, separators=(",", ":"))
    recomputed = "sha256:" + hashlib.sha256(canon.encode()).hexdigest()
    if data.get("baseline_digest") != recomputed:
        result.add_violation(code="BASELINE_DIGEST_MISMATCH",
            message=f"{path.name}: declared {data.get('baseline_digest')!r} != recomputed {recomputed!r}", path=path.name)
        result.result = ResultStatus.error
        return {}, None
    index = {}
    for e in entries:
        if "*" in e["path"] or "*" in e["job"] or "*" in e["step"]:
            result.add_violation(code="BASELINE_WILDCARD_FORBIDDEN",
                message=f"wildcard baseline entry: {e['path']}:{e['job']}/{e['step']}", path=path.name)
            result.result = ResultStatus.error
            return {}, None
        index[(e["violation_code"], e["path"], e["job"], e["step"])] = e["command_sha256"]
    return index, recomputed


def run(root, output_json, fmt, quiet):
    result = GateResult(gate_id=GATE_ID, result=ResultStatus.passed)
    lock_path = root / 'requirements' / 'bootstrap.lock'
    if not (root / 'requirements').exists():
        result.add_violation(code="REQUIREMENTS_DIR_MISSING", message="requirements/ directory not found at repo root.")
        result.result = ResultStatus.error
        return _emit(result, output_json, fmt, quiet, 2)
    for v in _check_lock_file(lock_path):
        result.add_violation(**v)
        result.result = ResultStatus.failed
    active_exceptions = _load_exceptions(root, result)
    # A broken or untrustworthy exception registry is fatal: we cannot know
    # which findings are legitimately waived, so fail closed immediately
    # rather than continuing and risking a later status downgrade.
    if result.result == ResultStatus.error:
        return _emit(result, output_json, fmt, quiet, 2)
    baseline_index, baseline_digest = _load_baseline(root, result)
    # A broken/tampered baseline is equally fatal: it feeds the set of tolerated
    # legacy findings, so it must be trustworthy before we consult it.
    if result.result == ResultStatus.error:
        return _emit(result, output_json, fmt, quiet, 2)
    try:
        workflow_files = list(iter_workflow_files(root / '.github' / 'workflows', root))
    except ValueError as exc:
        result.add_violation(code="RESOURCE_LIMIT", message=str(exc))
        result.result = ResultStatus.error
        return _emit(result, output_json, fmt, quiet, 2)
    legacy_observations = []
    matched_baseline_keys = set()
    new_violation_count = 0

    def _handle(code, rel, jid, name, run_block, line_no, human):
        """Phased enforcement for one workflow install finding.

        1. bootstrap-managed installs (they touch the bootstrap lock) are held
           to the full standard: always a blocking violation here.
        2. a signed, in-window exception waives the finding entirely.
        3. otherwise, if the exact identity is in the checked-in baseline AND
           the normalized command still matches its recorded hash, the finding
           is a non-blocking legacy observation.
        4. anything else -- a new finding, or a baselined finding whose command
           changed -- is a blocking violation. This is what makes the baseline
           fail-closed against growth and against edits to legacy commands.
        """
        nonlocal new_violation_count
        bootstrap_managed = _BOOTSTRAP_LOCK_MARKER in run_block
        if not bootstrap_managed and _is_excepted(active_exceptions, code, rel, jid, name):
            return
        key = (code, rel, jid, name)
        cmd_sha = _command_sha256(run_block)
        if not bootstrap_managed and key in baseline_index:
            if baseline_index[key] == cmd_sha:
                matched_baseline_keys.add(key)
                legacy_observations.append({
                    "path": rel, "job": jid, "step": name,
                    "violation_code": code, "command_sha256": cmd_sha,
                    "status": "legacy_observation",
                })
                result.add_warning(code=f"{code}_LEGACY_OBSERVED",
                    message=f"{Path(rel).name}: job={jid} step={name!r}: {human} (pre-existing legacy observation; tracked in baseline for PR-C removal).",
                    path=rel, line=line_no or None,
                    details={"status": "legacy_observation", "command_sha256": cmd_sha})
                return
            # Baselined identity but the command changed: a modified pre-existing
            # install must not ride the old waiver.
            result.add_violation(code=f"{code}_BASELINE_CHANGED",
                message=f"{Path(rel).name}: job={jid} step={name!r}: {human} -- command changed from baseline; re-review required.",
                path=rel, line=line_no or None)
            result.result = ResultStatus.failed
            new_violation_count += 1
            return
        # Bootstrap-managed, or a brand-new finding not in the baseline.
        suffix = " (bootstrap-managed install must use --require-hashes)" if bootstrap_managed else ""
        result.add_violation(code=code,
            message=f"{Path(rel).name}: job={jid} step={name!r}: {human}{suffix}.",
            path=rel, line=line_no or None)
        result.result = ResultStatus.failed
        new_violation_count += 1

    for wf_path in workflow_files:
        rel = str(wf_path.relative_to(root))
        try:
            wf = load_yaml_file(wf_path)
        except Exception as exc:
            result.add_violation(code="WORKFLOW_PARSE_ERROR", message=f"{wf_path.name}: {exc}", path=rel)
            result.result = ResultStatus.error
            continue
        if not isinstance(wf, dict):
            continue
        for jid, idx, name, run_block, line_no in iter_run_blocks(wf):
            if not _PIP_INSTALL_RE.search(run_block):
                continue
            if _REQUIRE_HASHES.search(run_block):
                continue
            if _EDITABLE_NO_DEPS.search(run_block):
                continue
            if _UPGRADE_RE.search(run_block):
                _handle('UNCONDITIONAL_PIP_UPGRADE', rel, jid, name, run_block, line_no,
                        'pip install --upgrade/-U without --require-hashes')
                continue
            if _BRANCH_URL_RE.search(run_block):
                _handle('BRANCH_URL_INSTALL', rel, jid, name, run_block, line_no,
                        'git+https://...@<branch> install forbidden')
                continue
            m = _UNBOUNDED_RE.search(run_block)
            if m:
                _handle('UNBOUNDED_PIP_INSTALL', rel, jid, name, run_block, line_no,
                        f'unbounded pip install {m.group(1)!r}')
                continue
            _handle('UNPINNED_PIP_INSTALL', rel, jid, name, run_block, line_no,
                    'pip install without --require-hashes')

    # Fail-closed against baseline staleness: every recorded legacy entry must
    # still correspond to a live finding. A baseline entry that no longer
    # matches anything means the workflow changed without the reviewed baseline
    # being regenerated -- block until the baseline is updated.
    stale = set(baseline_index) - matched_baseline_keys
    for code, rel, jid, name in sorted(stale):
        result.add_violation(code="BASELINE_ENTRY_STALE",
            message=f"baseline records {code} at {rel}:{jid}/{name!r} but no matching live finding exists; regenerate the baseline.",
            path=rel)
        result.result = ResultStatus.failed
        new_violation_count += 1

    result.metadata = {
        'files_scanned': len(workflow_files),
        'lock_file': str(lock_path),
        'legacy_observation_count': len(legacy_observations),
        'new_violation_count': new_violation_count,
        'baseline_digest': baseline_digest,
        'legacy_observations': sorted(legacy_observations, key=lambda o: (o['path'], o['job'], o['step'], o['violation_code'])),
    }
    if result.result == ResultStatus.passed:
        result.finalize(); exit_code = 0
    else:
        try: result.finalize()
        except ValueError: pass
        exit_code = 1 if result.result == ResultStatus.failed else 2
    return _emit(result, output_json, fmt, quiet, exit_code)

def _emit(result, output_json, fmt, quiet, exit_code):
    data = result.to_dict()
    if output_json:
        write_json(data, output_json)
    if fmt == 'json':
        write_json_stdout(data)
    elif not quiet or exit_code != 0:
        print(f'[{result.result.value.upper()}] {GATE_ID}')
        for v in result.violations:
            print(f'  VIOLATION {v.code}: {v.message}')
    return exit_code

def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--root', default=None)
    p.add_argument('--output-json', default=None)
    p.add_argument('--format', choices=['text', 'json'], default='text')
    p.add_argument('--quiet', action='store_true')
    args = p.parse_args(argv)
    try:
        _root = repo_root(args.root)
        return run(_root, Path(args.output_json) if args.output_json else None, args.format, args.quiet)
    except Exception as exc:
        r = GateResult(gate_id=GATE_ID, result=ResultStatus.error)
        r.add_violation(code='EXECUTION_ERROR', message=str(exc))
        data = r.to_dict()
        if args.output_json:
            write_json(data, Path(args.output_json))
        if args.format == 'json':
            write_json_stdout(data)
        else:
            print(f'[ERROR] {GATE_ID}: {exc}', file=sys.stderr)
        return 2

if __name__ == '__main__':
    sys.exit(main())
