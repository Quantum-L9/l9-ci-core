#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from l9_bootstrap import limits
from l9_bootstrap.paths import repo_root
from l9_bootstrap.schema_loader import (
    SchemaUnavailable,
    format_error as _format_schema_error,
    load_validator,
    schema_errors as _schema_errors,
)

GATE_ID = "bootstrap-result-validator"

# Exit codes: 0 = all valid, 1 = a result failed/invalid, 2 = fatal
# governance error (e.g. a required schema is missing -> fail closed).
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_ERROR = 2
EXPECTED = [
    ("action-pins.json",        "workflow/action-pins"),
    ("download-integrity.json", "workflow/download-integrity"),
    ("ci-dependencies.json",    "dependencies/ci-lock"),
    ("workflow-contracts.json", "workflow/contracts"),
]


def _load_json(path: Path) -> dict:
    """Read a JSON object with a hard size cap; reject non-object roots."""
    if path.stat().st_size > limits.MAX_RESULT_FILE_BYTES:
        raise ValueError(
            f"{path.name} exceeds result-size limit {limits.MAX_RESULT_FILE_BYTES}"
        )
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} root must be a JSON object")
    return value


# Canonical exit code each result value must carry (mirrors the orchestrator).
_RESULT_TO_EXIT = {"passed": 0, "failed": 1, "error": 2}


def run(results_dir, root, quiet=False):
    results_dir = Path(results_dir)
    overall_ok = True
    seen: dict = {}
    actual_by_file: dict = {}
    # Required schemas: their absence is fatal (fail closed, exit 2). Evidence
    # cannot be trusted without the contract that describes it.
    try:
        result_validator = load_validator(root, "bootstrap-gate-result.schema.json")
        manifest_validator = load_validator(root, "bootstrap-manifest.schema.json")
    except SchemaUnavailable as exc:
        print(f"[SCHEMA_UNAVAILABLE] {exc}", file=sys.stderr)
        if not quiet:
            print("[ERROR] Bootstrap result validation cannot run without required schemas.", file=sys.stderr)
        return EXIT_ERROR
    for fname, expected_gid in EXPECTED:
        fpath = results_dir / fname
        if not fpath.exists():
            print(f"[MISSING] {fname} — expected gate_id={expected_gid!r}", file=sys.stderr)
            overall_ok = False
            continue
        try:
            data = _load_json(fpath)
        except Exception as exc:
            print(f"[MALFORMED] {fname}: {exc}", file=sys.stderr)
            overall_ok = False
            continue
        schema_errors = _schema_errors(result_validator, data)
        if schema_errors:
            for error in schema_errors:
                print(
                    f"[SCHEMA_INVALID] {fname}: {_format_schema_error(error)}",
                    file=sys.stderr,
                )
            overall_ok = False
            continue
        actual = data.get("gate_id", "")
        if actual != expected_gid:
            print(f"[WRONG_GATE_ID] {fname}: expected={expected_gid!r} got={actual!r}", file=sys.stderr)
            overall_ok = False
            continue
        if actual in seen:
            print(f"[DUPLICATE] {actual!r} in {seen[actual]} and {fname}", file=sys.stderr)
            overall_ok = False
            continue
        seen[actual] = fname
        result_val = data.get("result", "")
        # Record the authoritative view of this result derived from the actual
        # evidence file, for later cross-check against the manifest's claims.
        actual_by_file[fname] = {
            "gate_id": actual,
            "file": fname,
            "result": result_val,
            "exit_code": _RESULT_TO_EXIT[result_val],
        }
        if result_val in ("failed", "error"):
            overall_ok = False
        if not quiet:
            print(f"[{result_val.upper():6}] {actual}")

    # The aggregate manifest must exist and satisfy its schema.
    manifest_path = results_dir / "bootstrap-manifest.json"
    if not manifest_path.exists():
        print("[MISSING] bootstrap-manifest.json", file=sys.stderr)
        overall_ok = False
    else:
        try:
            manifest = _load_json(manifest_path)
            manifest_errors = _schema_errors(manifest_validator, manifest)
            for error in manifest_errors:
                print(
                    f"[MANIFEST_SCHEMA_INVALID] {_format_schema_error(error)}",
                    file=sys.stderr,
                )
            if manifest_errors:
                overall_ok = False
            else:
                # Cross-check every manifest result entry against the actual
                # result file it references. The manifest is a claim; the
                # per-gate evidence files are the ground truth.
                manifest_results = {
                    entry.get("file"): entry
                    for entry in manifest.get("results", [])
                }

                if set(manifest_results) != set(actual_by_file):
                    print(
                        "[MANIFEST_RESULT_SET_MISMATCH] manifest result files "
                        f"{sorted(manifest_results)} != actual result files "
                        f"{sorted(actual_by_file)}",
                        file=sys.stderr,
                    )
                    overall_ok = False

                for fname, actual_entry in actual_by_file.items():
                    manifest_entry = manifest_results.get(fname)
                    if manifest_entry is None:
                        # Already reported by the set mismatch above.
                        continue
                    # Compare gate_id, file, result, and canonical exit code.
                    if manifest_entry != actual_entry:
                        print(
                            f"[MANIFEST_RESULT_MISMATCH] {fname}: "
                            f"manifest={manifest_entry!r} "
                            f"actual={actual_entry!r}",
                            file=sys.stderr,
                        )
                        overall_ok = False

                # Recompute complete/overall_result from ground truth and
                # reject any manifest that disagrees.
                expected_complete = (
                    len(actual_by_file) == len(EXPECTED)
                    and set(actual_by_file) == {f for f, _ in EXPECTED}
                )
                if bool(manifest.get("complete")) != expected_complete:
                    print(
                        "[MANIFEST_COMPLETENESS_MISMATCH] "
                        f"manifest={manifest.get('complete')!r} "
                        f"actual={expected_complete!r}",
                        file=sys.stderr,
                    )
                    overall_ok = False

                expected_overall = (
                    "passed"
                    if (
                        expected_complete
                        and all(
                            entry["result"] == "passed"
                            for entry in actual_by_file.values()
                        )
                    )
                    else "failed"
                )
                if manifest.get("overall_result") != expected_overall:
                    print(
                        "[MANIFEST_OVERALL_MISMATCH] "
                        f"manifest={manifest.get('overall_result')!r} "
                        f"actual={expected_overall!r}",
                        file=sys.stderr,
                    )
                    overall_ok = False
        except Exception as exc:
            print(f"[MANIFEST_MALFORMED] {exc}", file=sys.stderr)
            overall_ok = False

    if not overall_ok:
        if not quiet:
            print("[FAIL] Bootstrap result validation failed.", file=sys.stderr)
        return EXIT_FAILED
    if not quiet:
        print("[PASS] All bootstrap gate results validated.")
    return EXIT_OK

def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--results-dir", required=True)
    p.add_argument("--root", default=None)
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)
    _root = repo_root(args.root)
    return run(Path(args.results_dir), _root, args.quiet)

if __name__ == "__main__":
    sys.exit(main())
