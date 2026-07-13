#!/usr/bin/env python3
"""Thin CLI: normalize a GitHub event into a canonical event-context JSON.

Business logic lives in :mod:`l9_ci_core.control_plane.context`; this wrapper
only parses arguments, validates the output against
``schemas/event-context.schema.json``, and writes the file. It imports the
installed package normally — no sys.path manipulation.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from l9_ci_core.control_plane import context, schemas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Normalize a GitHub event context.")
    parser.add_argument("--event-file", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--subject-sha", help="explicit subject SHA (workflow_dispatch)")
    parser.add_argument("--base-sha", help="explicit base SHA (workflow_dispatch)")
    args = parser.parse_args(argv)

    try:
        ctx = context.normalize_from_env(
            args.event_file,
            dispatch_subject_sha=args.subject_sha,
            dispatch_base_sha=args.base_sha,
        )
    except context.ContextError as exc:
        print(f"[FATAL] event-context normalization failed: {exc}", file=sys.stderr)
        return 2

    data = ctx.to_dict()
    try:
        schemas.validate("event-context", data)
    except ValueError as exc:
        print(f"[FATAL] normalized context is schema-invalid: {exc}", file=sys.stderr)
        return 2

    out = Path(args.output_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"[OK] event-context -> {out} "
        f"(event={data['event_type']} subject={data['subject_sha'][:12]} "
        f"base={data['base_sha'] or 'unknown'})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
