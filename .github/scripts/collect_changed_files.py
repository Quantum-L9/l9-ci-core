#!/usr/bin/env python3
"""Thin CLI: acquire the changed-file set for a normalized event context.

Business logic lives in :mod:`l9_ci_core.control_plane.changed_files`; this
wrapper reads the event-context JSON, runs the diff, validates the output
against ``schemas/changed-files.schema.json``, and writes the file. A diff
failure is reported as ``unknown_diff`` (exit 0) so downstream planning can
still fail closed; only genuine invocation errors exit non-zero.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from l9_ci_core.control_plane import changed_files, schemas
from l9_ci_core.control_plane.models import EventContext


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect changed files for a context.")
    parser.add_argument("--event-context", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--root", default=".", help="repository root for git diff")
    args = parser.parse_args(argv)

    try:
        ctx_data = json.loads(Path(args.event_context).read_text(encoding="utf-8"))
        schemas.validate("event-context", ctx_data)
        ctx = EventContext.from_dict(ctx_data)
    except (OSError, ValueError) as exc:
        print(f"[FATAL] invalid event-context input: {exc}", file=sys.stderr)
        return 2

    result = changed_files.collect_for_context(ctx, root=args.root)
    data = result.to_dict()
    try:
        schemas.validate("changed-files", data)
    except ValueError as exc:
        print(f"[FATAL] changed-files output is schema-invalid: {exc}", file=sys.stderr)
        return 2

    out = Path(args.output_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = (
        f"unknown_diff ({data['reason']})"
        if data["unknown_diff"]
        else f"{len(data['files'])} file(s)"
    )
    print(f"[OK] changed-files -> {out} ({summary})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
