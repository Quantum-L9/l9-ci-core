"""Run Core's consumer-facing Biome check (advisory).

Core owns the Biome check end to end: the version (toolchain lock), the
verified binary (``install-biome``), and this execution. Core does not parse
Biome's report — ``.l9/architecture.yaml`` prohibits Core parsing
provider-native scanner reports. Biome writes its own GitHub annotations with
``--reporter=github`` straight to the job log, and this module records only the
process exit code.

Exit-code classes, observed from the Biome 2.5.8 binary:

* ``0`` — no diagnostics at error level;
* ``1`` — Biome emitted error diagnostics (formatting, lint, or a configuration
  diagnostic it reports through the same reporter);
* anything else, or a process that cannot be started — a Biome crash or a
  missing/corrupt executable. That is a Core contract/infrastructure failure
  and is fatal in every mode (AGENTS.md §9).

A consumer without ``biome.json``/``biome.jsonc`` at the checkout root is
``not_applicable``: Core cannot require a consumer file.

    python3 run.py --executable PATH --mode advisory [--root .] [--output FILE]

Exit codes: ``0`` recorded (any Biome result in advisory mode), ``2`` invalid
invocation, ``3`` Core contract/infrastructure failure.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys

SCHEMA = "l9.biome-check/v1"
CONFIG_FILES = ("biome.json", "biome.jsonc")
MODES = ("advisory",)
FINDINGS_EXIT = 1


def _workspace(root: str) -> pathlib.Path:
    workspace = pathlib.Path(os.environ.get("GITHUB_WORKSPACE") or ".").resolve()
    candidate = (workspace / root).resolve()
    if candidate != workspace and workspace not in candidate.parents:
        raise SystemExit(f"run-biome-check: root {root!r} escapes the workspace")
    return candidate


def _write(output: pathlib.Path, record: dict[str, object]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", "utf-8")


def _summary(record: dict[str, object]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [
        "### Biome (Core, advisory)",
        "",
        f"- Status: `{record['status']}`",
        f"- Biome version: `{record['provider_version'] or 'unknown'}`",
        f"- Biome exit code: `{record['biome_exit_code']}`",
    ]
    with open(path, "a", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Core's Biome check")
    parser.add_argument("--executable", default="")
    parser.add_argument("--mode", required=True)
    parser.add_argument("--root", default=".")
    parser.add_argument("--provider-version", default="")
    parser.add_argument("--output", default=".l9/runtime/biome-check.json")
    args = parser.parse_args(argv)

    if args.mode not in MODES:
        print(
            f"run-biome-check: mode {args.mode!r} is not supported; only "
            f"{', '.join(MODES)} is defined for this Core revision",
            file=sys.stderr,
        )
        return 2

    root = _workspace(args.root)
    output = root / args.output
    record: dict[str, object] = {
        "schema": SCHEMA,
        "mode": args.mode,
        "provider_version": args.provider_version or None,
        "biome_exit_code": None,
    }

    if not any((root / name).is_file() for name in CONFIG_FILES):
        record["status"] = "not_applicable"
        _write(output, record)
        _summary(record)
        print("run-biome-check: no biome.json/biome.jsonc at the root; not applicable")
        return 0

    executable = pathlib.Path(args.executable)
    if (
        not args.executable
        or not executable.is_absolute()
        or not executable.is_file()
        or not os.access(executable, os.X_OK)
    ):
        print(
            f"run-biome-check: Biome executable unavailable ({args.executable!r}); "
            "this is a Core infrastructure failure, not a consumer finding",
            file=sys.stderr,
        )
        return 3

    # stdout/stderr are inherited, never captured: Biome's GitHub reporter
    # writes workflow commands the runner turns into annotations, and Core does
    # not read them.
    try:
        completed = subprocess.run(  # noqa: S603 - verified absolute executable
            [str(executable), "ci", "--reporter=github", "."],
            cwd=root,
            check=False,
        )
    except OSError as error:
        print(f"run-biome-check: Biome could not be started: {error}", file=sys.stderr)
        return 3

    code = completed.returncode
    record["biome_exit_code"] = code
    if code == 0:
        record["status"] = "pass"
    elif code == FINDINGS_EXIT:
        record["status"] = "findings"
    else:
        record["status"] = "error"
        _write(output, record)
        _summary(record)
        print(
            f"run-biome-check: Biome exited {code}, outside its diagnostic exit "
            "class; treating it as a crash (Core infrastructure failure)",
            file=sys.stderr,
        )
        return 3

    _write(output, record)
    _summary(record)
    if record["status"] == "findings":
        print(
            "::notice title=Biome (advisory)::Biome reported diagnostics; "
            "advisory mode records them without failing the job"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
