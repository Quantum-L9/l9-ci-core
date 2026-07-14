#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from l9_bootstrap.output import write_json
from l9_bootstrap.paths import repo_root
from l9_bootstrap import schema_loader

VALIDATORS = [
    ("workflow/action-pins", "validate_action_pins.py", "action-pins.json"),
    ("workflow/download-integrity", "validate_download_integrity.py", "download-integrity.json"),
    ("dependencies/ci-lock", "validate_ci_dependencies.py", "ci-dependencies.json"),
    ("workflow/contracts", "validate_workflow_contracts.py", "workflow-contracts.json"),
]

# Canonical mapping between a gate's declared result and the exit code it must
# have produced. This is the contract the aggregate manifest schema enforces.
_RESULT_TO_EXIT = {"passed": 0, "failed": 1, "error": 2}


def run(root, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    scripts_dir = Path(__file__).parent

    # Fail closed: the result schema is a required contract. Without it we
    # cannot certify any emitted evidence, so load it BEFORE executing any gate
    # and exit 2 (error) if it (or jsonschema) is unavailable.
    try:
        result_validator = schema_loader.load_validator(root, "bootstrap-gate-result.schema.json")
    except schema_loader.SchemaUnavailable as exc:
        print(f"[ERROR ] result schema unavailable: {exc}", file=sys.stderr)
        return 2

    results = []
    any_failed = False
    all_semantically_valid = True
    for gate_id, script_name, out_fname in VALIDATORS:
        out_path = output_dir / out_fname
        cmd = [
            sys.executable,
            str(scripts_dir / script_name),
            "--root",
            str(root),
            "--output-json",
            str(out_path),
            "--format",
            "text",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        exit_code = proc.returncode
        if exit_code != 0:
            any_failed = True
        result_str = "unknown"
        semantic_valid = False
        data = None
        if out_path.exists():
            try:
                data = json.loads(out_path.read_text())
                result_str = data.get("result", "unknown")
            except Exception:
                result_str = "error"
                data = None
        else:
            result_str = "error"
            any_failed = True

        # Schema-validate the emitted evidence AND confirm the gate identity the
        # validator claims matches the gate we invoked. A result that fails its
        # own contract, or reports the wrong gate_id, is not trustworthy.
        if data is not None:
            errors = schema_loader.schema_errors(result_validator, data)
            if errors:
                for error in errors[:5]:
                    print(
                        f"  SCHEMA_INVALID: {gate_id}: {schema_loader.format_error(error)}",
                        file=sys.stderr,
                    )
                result_str = "error"
                any_failed = True
            elif data.get("gate_id") != gate_id:
                print(
                    f"  GATE_ID_MISMATCH: invoked {gate_id!r} but evidence "
                    f"declares {data.get('gate_id')!r}",
                    file=sys.stderr,
                )
                result_str = "error"
                any_failed = True
            else:
                semantic_valid = True
        # Coerce any unrecognized result into the terminal "error" state so the
        # manifest never carries an out-of-contract result value.
        if result_str not in _RESULT_TO_EXIT:
            result_str = "error"
            any_failed = True
        # Enforce exit-code / result consistency. The manifest schema requires
        # passed=0, failed=1, error=2. If a validator reported a result that
        # disagrees with its process exit code, treat the gate as errored: a
        # validator that cannot honor this contract is not trustworthy.
        canonical_exit = _RESULT_TO_EXIT[result_str]
        if exit_code != canonical_exit:
            print(
                f"  INCONSISTENT: {gate_id} reported result={result_str!r} "
                f"but exited {exit_code} (expected {canonical_exit})",
                file=sys.stderr,
            )
            result_str = "error"
            canonical_exit = _RESULT_TO_EXIT["error"]
            any_failed = True
            semantic_valid = False
        if result_str in ("failed", "error"):
            any_failed = True
        # A gate is only semantically valid when it produced schema-conformant
        # evidence with a matching gate_id AND its exit/result contract held.
        if not semantic_valid:
            all_semantically_valid = False
        print(f"[{result_str.upper():6}] {gate_id}  (exit={exit_code})")
        for line in proc.stdout.strip().splitlines():
            print(f"  {line}")
        if proc.stderr.strip() and exit_code != 0:
            for line in proc.stderr.strip().splitlines()[:5]:
                print(f"  STDERR: {line}", file=sys.stderr)
        results.append(
            {
                "gate_id": gate_id,
                "file": out_fname,
                "result": result_str,
                "exit_code": canonical_exit,
            }
        )
    # complete is true ONLY when we produced exactly one result per expected
    # gate, each result file exists, AND every gate was semantically valid
    # (schema-conformant evidence, matching gate_id, consistent exit/result).
    complete = (
        len(results) == len(VALIDATORS)
        and all((output_dir / r["file"]).exists() for r in results)
        and all_semantically_valid
    )
    if not complete:
        any_failed = True
    overall = "passed" if not any_failed else "failed"
    manifest = {
        "schema_version": "1.0",
        "expected_gates": [g for g, _, _ in VALIDATORS],
        "results": results,
        "complete": complete,
        "overall_result": overall,
    }
    write_json(manifest, output_dir / "bootstrap-manifest.json")
    print(
        f"\n[{'PASS' if not any_failed else 'FAIL'}] Bootstrap manifest: overall={overall}  complete={complete}"
    )

    # Final gate: re-validate every emitted result plus the manifest through the
    # schema-backed results validator. This guarantees the evidence we just
    # wrote is itself well-formed before it can be consumed downstream.
    aggregate = subprocess.run(
        [
            sys.executable,
            str(scripts_dir / "validate_bootstrap_results.py"),
            "--results-dir",
            str(output_dir),
            "--root",
            str(root),
            "--quiet",
        ],
        capture_output=True,
        text=True,
    )
    if aggregate.returncode != 0:
        any_failed = True
        for line in aggregate.stderr.strip().splitlines()[:10]:
            print(f"  RESULTS_VALIDATION: {line}", file=sys.stderr)

    return 1 if any_failed else 0


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--root", default=None)
    p.add_argument("--output-dir", required=True)
    args = p.parse_args(argv)
    _root = repo_root(args.root)
    return run(_root, Path(args.output_dir))


if __name__ == "__main__":
    sys.exit(main())
