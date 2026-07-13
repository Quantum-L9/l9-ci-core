#!/usr/bin/env python3
"""Bootstrap gate: workflow/contracts"""
from __future__ import annotations
import argparse, json, re, sys
from datetime import date, datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from l9_bootstrap.models import GateResult, ResultStatus
from l9_bootstrap.output import write_json, write_json_stdout
from l9_bootstrap.paths import repo_root
from l9_bootstrap.workflow_scan import iter_jobs, iter_steps, iter_workflow_files
from l9_bootstrap.yaml_loader import load_yaml_file
from l9_bootstrap import schema_loader

GATE_ID = "workflow/contracts"
_RE_UNSAFE_SHELL = re.compile(r"\bset\s+\+e\b")
# Broadened failure-swallowing patterns: `|| true`, `|| :`, `|| echo ::warning::`,
# and a trailing `; exit 0`.
_RE_SWALLOW      = re.compile(r"\|\|\s*true\b|\|\|\s*:|\|\|\s*echo\s+::warning::|;\s*exit\s+0\b")
# A strict-shell preamble: `set -euo pipefail` (order/spacing tolerant).
_RE_STRICT_SHELL = re.compile(r"set\s+-euo\s+pipefail|set\s+-e\b.*-o\s+pipefail", re.DOTALL)
_MAX_DEBT_WINDOW_DAYS = 30


def _is_nontrivial_run(run_block: str) -> bool:
    """A run block is nontrivial if it has multiple real command lines.

    Blank lines, comments, and a lone `set ...` preamble do not count. This
    keeps single-command steps (e.g. one echo) out of strict-shell scope.
    """
    cmds = 0
    for raw in run_block.splitlines():
        s = raw.strip()
        if not s or s.startswith("#") or s.startswith("set "):
            continue
        cmds += 1
    return cmds >= 2 or (cmds >= 1 and len(run_block.strip()) > 40 and "\n" in run_block.strip())


def _load_debt(root: Path, result: GateResult):
    """Load and validate the workflow-contract-debt registry.

    Returns a set of (rule, path, job, step) tuples that are actively waived.
    Malformed, expired, or over-window debt is rejected and grants no waiver.
    """
    path = root / ".github" / "governance" / "workflow-contract-debt.yaml"
    if not path.exists():
        path = root / ".github" / "governance" / "workflow-contract-debt.json"
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.suffix == ".json" else load_yaml_file(path)
    except Exception as exc:
        result.add_violation(code="DEBT_FILE_MALFORMED", message=f"{path.name}: {exc}")
        result.result = ResultStatus.error
        return set()
    # Schema validation is mandatory (fail closed): the debt registry waives
    # security-relevant contract findings and cannot be trusted unless valid.
    try:
        validator = schema_loader.load_validator(root, "workflow-contract-debt")
    except schema_loader.SchemaUnavailable as exc:
        result.add_violation(code="SCHEMA_UNAVAILABLE", message=str(exc))
        result.result = ResultStatus.error
        return set()
    errors = schema_loader.schema_errors(validator, data)
    if errors:
        for err in errors:
            result.add_violation(code="DEBT_SCHEMA_INVALID", message=schema_loader.format_error(err))
        result.result = ResultStatus.error
        return set()
    active = set()
    today = datetime.now(timezone.utc).date()
    for entry in (data or {}).get("debt", []):
        try:
            expires = date.fromisoformat(entry.get("expires_on", ""))
            created = date.fromisoformat(entry.get("created_at", ""))
        except Exception:
            result.add_violation(code="DEBT_SCHEMA_INVALID", message=f"invalid created_at/expires_on: {entry.get('created_at')!r}/{entry.get('expires_on')!r}")
            result.result = ResultStatus.error
            continue
        if expires < today:
            result.add_violation(code="DEBT_EXPIRED",
                                 message=f"contract debt for {entry.get('rule')} on {entry.get('path')} expired {entry.get('expires_on')}")
            result.result = ResultStatus.failed
            continue
        # Window measured from the entry's own creation date, not today, so an
        # over-long grant cannot silently become valid near expiry.
        if (expires - created).days > _MAX_DEBT_WINDOW_DAYS:
            result.add_violation(code="DEBT_WINDOW_TOO_LONG",
                                 message=f"contract debt for {entry.get('rule')} spans {(expires - created).days}d from {entry.get('created_at')} to {entry.get('expires_on')}, exceeding {_MAX_DEBT_WINDOW_DAYS}-day window")
            result.result = ResultStatus.failed
            continue
        active.add((entry.get("rule", ""), entry.get("path", ""), entry.get("job", ""), entry.get("step", "")))
    return active


def _debt_matches(active, rule, rel, job_id, step_name):
    for d_rule, d_path, d_job, d_step in active:
        if d_rule != rule:
            continue
        if d_path not in (rel, Path(rel).name):
            continue
        if d_job and d_job != job_id:
            continue
        if d_step and d_step != step_name:
            continue
        return True
    return False


def _validate_public_contract(root: Path, wf, rel: str, result: GateResult):
    """Enforce the declared public interface of pr-pipeline.yml.

    The workflow must expose ``on.workflow_call`` and its declared inputs must
    match the frozen public interface (type/required/default per input).
    """
    # The frozen public interface is a test fixture, not a governance artifact:
    # it is the immutable contract that consumers depend on. It is REQUIRED to
    # exist whenever pr-pipeline.yml exists (fail, do not silently return).
    iface_path = root / "tests" / "fixtures" / "workflow-contracts" / "pr-pipeline-public-interface.json"
    if not iface_path.exists():
        result.add_violation(
            code="PUBLIC_INTERFACE_FIXTURE_MISSING",
            message=("pr-pipeline.yml exists but its frozen public-interface "
                     "fixture tests/fixtures/workflow-contracts/"
                     "pr-pipeline-public-interface.json is missing."),
            path=rel)
        result.result = ResultStatus.failed
        return
    try:
        iface = json.loads(iface_path.read_text(encoding="utf-8"))
    except Exception as exc:
        result.add_violation(code="PUBLIC_INTERFACE_UNREADABLE", message=str(exc), path=rel)
        result.result = ResultStatus.error
        return
    triggers = wf.get("on") or wf.get(True) or {}
    has_wc = isinstance(triggers, dict) and "workflow_call" in triggers
    if isinstance(triggers, list):
        has_wc = "workflow_call" in triggers
    if not has_wc:
        result.add_violation(code="PUBLIC_INTERFACE_BREAK",
                             message=f"{rel}: must declare on.workflow_call to honor its public interface.", path=rel)
        result.result = ResultStatus.failed
        return
    declared = {}
    if isinstance(triggers, dict):
        wc = triggers.get("workflow_call") or {}
        if isinstance(wc, dict):
            declared = wc.get("inputs") or {}
    for name, spec in (iface.get("inputs") or {}).items():
        if name not in declared:
            result.add_violation(code="PUBLIC_INTERFACE_BREAK",
                                 message=f"{rel}: missing declared input {name!r}.", path=rel)
            result.result = ResultStatus.failed
            continue
        actual = declared[name] or {}
        if spec.get("type") and actual.get("type") != spec.get("type"):
            result.add_violation(code="PUBLIC_INTERFACE_BREAK",
                                 message=f"{rel}: input {name!r} type {actual.get('type')!r} != {spec.get('type')!r}.", path=rel)
            result.result = ResultStatus.failed
        if bool(actual.get("required", False)) != bool(spec.get("required", False)):
            result.add_violation(code="PUBLIC_INTERFACE_BREAK",
                                 message=f"{rel}: input {name!r} required flag changed.", path=rel)
            result.result = ResultStatus.failed
        if "default" in spec and actual.get("default") != spec.get("default"):
            result.add_violation(code="PUBLIC_INTERFACE_BREAK",
                                 message=f"{rel}: input {name!r} default {actual.get('default')!r} != {spec.get('default')!r}.", path=rel)
            result.result = ResultStatus.failed

def run(root, output_json, fmt, quiet):
    result = GateResult(gate_id=GATE_ID, result=ResultStatus.passed)
    try:
        workflow_files = list(iter_workflow_files(root / '.github' / 'workflows', root))
    except ValueError as exc:
        result.add_violation(code="RESOURCE_LIMIT", message=str(exc))
        result.result = ResultStatus.error
        return _emit(result, output_json, fmt, quiet, 2)
    if not workflow_files:
        result.add_violation(code="NO_WORKFLOW_FILES", message="No workflow files found.")
        result.result = ResultStatus.error
        return _emit(result, output_json, fmt, quiet, 2)
    active_debt = _load_debt(root, result)
    # A broken/untrustworthy debt registry is fatal: fail closed before scanning
    # so a later failed status cannot mask the error.
    if result.result == ResultStatus.error:
        return _emit(result, output_json, fmt, quiet, 2)
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
        triggers = wf.get('on') or wf.get(True) or {}
        if isinstance(triggers, dict) and 'pull_request_target' in triggers:
            result.add_violation(code="PR_TARGET_FORBIDDEN", message=f"{wf_path.name}: pull_request_target is forbidden.", path=rel)
            result.result = ResultStatus.failed
        if isinstance(triggers, list) and 'pull_request_target' in triggers:
            result.add_violation(code="PR_TARGET_FORBIDDEN", message=f"{wf_path.name}: pull_request_target is forbidden.", path=rel)
            result.result = ResultStatus.failed
        # The frozen public pipeline must not break its declared interface.
        if wf_path.name == "pr-pipeline.yml":
            _validate_public_contract(root, wf, rel, result)
        for job_id, job in iter_jobs(wf):
            is_bootstrap = 'bootstrap' in job_id.lower()
            if not is_bootstrap:
                # HIGH-08: shell discipline is a bootstrap *invariant*, but the
                # rest of the repository is still observed. Non-bootstrap jobs
                # cannot fail this gate (that would break pre-existing green
                # workflows out of scope for PR-A), yet every nontrivial run
                # block that lacks a strict-shell preamble or that swallows a
                # failure is surfaced as a non-blocking observation so the debt
                # is visible rather than silent.
                for step_idx, step in iter_steps(job):
                    step_name = str(step.get('name', f'step-{step_idx}'))
                    run_block = str(step.get('run', ''))
                    if not run_block:
                        continue
                    if _is_nontrivial_run(run_block) and not _RE_STRICT_SHELL.search(run_block):
                        result.add_warning(code="STRICT_SHELL_OBSERVED",
                            message=f"{wf_path.name}: job={job_id} step={step_name!r} nontrivial run block missing 'set -euo pipefail' (non-bootstrap; observed).",
                            path=rel)
                    if _RE_SWALLOW.search(run_block):
                        result.add_warning(code="FAILURE_SWALLOW_OBSERVED",
                            message=f"{wf_path.name}: job={job_id} step={step_name!r} swallows failure (non-bootstrap; observed).",
                            path=rel)
            if is_bootstrap:
                if 'permissions' not in job:
                    result.add_violation(code="BOOTSTRAP_PERMISSIONS_MISSING", message=f"{wf_path.name}: job={job_id} missing permissions block.", path=rel)
                    result.result = ResultStatus.failed
                if 'timeout-minutes' not in job:
                    result.add_violation(code="BOOTSTRAP_TIMEOUT_MISSING", message=f"{wf_path.name}: job={job_id} missing timeout-minutes.", path=rel)
                    result.result = ResultStatus.failed
                for step_idx, step in iter_steps(job):
                    step_name = str(step.get('name', f'step-{step_idx}'))
                    uses_val  = str(step.get('uses', ''))
                    run_block = str(step.get('run',  ''))
                    if 'upload-artifact' in uses_val or 'upload-artifact' in step_name.lower():
                        if_cond = str(step.get('if', '')).strip()
                        if 'always()' not in if_cond:
                            result.add_violation(code="BOOTSTRAP_EVIDENCE_NOT_ALWAYS", message=f"{wf_path.name}: job={job_id} step={step_name!r} missing if: always().", path=rel)
                            result.result = ResultStatus.failed
                        with_block = step.get('with') or {}
                        if str(with_block.get('if-no-files-found', '')) != 'error':
                            result.add_violation(code="BOOTSTRAP_EVIDENCE_OPTIONAL", message=f"{wf_path.name}: job={job_id} step={step_name!r} missing if-no-files-found: error.", path=rel)
                            result.result = ResultStatus.failed
                    if run_block and len(run_block.strip()) > 40:
                        if _RE_UNSAFE_SHELL.search(run_block) and 'status' not in run_block:
                            result.add_violation(code="BOOTSTRAP_SHELL_UNSAFE", message=f"{wf_path.name}: job={job_id} step={step_name!r} uses set +e without status capture.", path=rel)
                            result.result = ResultStatus.failed
                    # Failure-swallowing is forbidden regardless of block size.
                    if run_block and _RE_SWALLOW.search(run_block):
                        if not _debt_matches(active_debt, "FAILURE_SWALLOW_FORBIDDEN", rel, job_id, step_name):
                            result.add_violation(code="FAILURE_SWALLOWED", message=f"{wf_path.name}: job={job_id} step={step_name!r} swallows failure.", path=rel)
                            result.result = ResultStatus.failed
                    # Any nontrivial bootstrap run block must open with a strict
                    # shell preamble unless explicitly covered by contract debt.
                    if run_block and _is_nontrivial_run(run_block) and not _RE_STRICT_SHELL.search(run_block):
                        if not _debt_matches(active_debt, "STRICT_SHELL_REQUIRED", rel, job_id, step_name):
                            result.add_violation(code="STRICT_SHELL_REQUIRED", message=f"{wf_path.name}: job={job_id} step={step_name!r} nontrivial run block missing 'set -euo pipefail'.", path=rel)
                            result.result = ResultStatus.failed
    result.metadata = {'files_scanned': len(workflow_files)}
    if result.result == ResultStatus.passed:
        result.finalize(); exit_code = 0
    else:
        try:
            result.finalize()
        except ValueError:
            # finalize() may raise when the result carries no recorded findings
            # yet is not in the passed state; the exit code is derived directly
            # from result.result below, so this is intentionally ignored.
            pass
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
