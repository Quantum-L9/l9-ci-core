"""Run Core's consumer-facing Biome check (advisory or blocking).

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

    python3 check_biome.py --executable PATH --mode advisory|blocking [--root .] [--output FILE]

Modes are a Core rollout decision (``org-ci.yml`` sets the mode; a consumer
cannot): ``advisory`` records findings and exits ``0``; ``blocking`` records
the same findings and exits ``1`` so the required job fails. The record is
written before the exit in both modes, so the summary always carries it.

Exit codes: ``0`` recorded and passing (or any Biome result in advisory
mode), ``1`` findings in blocking mode, ``2`` invalid invocation or a
root/output path that resolves outside the workspace (checked before any
write, so a committed ``.l9`` symlink cannot redirect the report), ``3`` Core
contract/infrastructure failure (fatal in every mode).
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
from typing import NoReturn

SCHEMA = "l9.biome-check/v1"
CONFIG_FILES = ("biome.json", "biome.jsonc")
MODES = ("advisory", "blocking")
FINDINGS_EXIT = 1


def _refuse(message: str) -> NoReturn:
    """Invalid invocation or a containment breach: exit 2 before any write."""
    print(f"run-biome-check: {message}", file=sys.stderr)
    raise SystemExit(2)


def _workspace() -> pathlib.Path:
    return pathlib.Path(os.environ.get("GITHUB_WORKSPACE") or ".").resolve()


def _contained(workspace: pathlib.Path, path: pathlib.Path, label: str) -> pathlib.Path:
    """Resolve ``path`` and refuse it unless it stays inside the workspace.

    Resolution follows symlinks, so a consumer-committed ``.l9`` (or report
    file) linking outside the checkout is caught here, before any write.
    """
    resolved = path.resolve()
    if resolved != workspace and workspace not in resolved.parents:
        _refuse(f"{label} escapes the workspace")
    return resolved


def _write(output: pathlib.Path, record: dict[str, object]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", "utf-8")


def _summary(record: dict[str, object]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [
        f"### Biome (Core, {record['mode']})",
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
            f"run-biome-check: mode {args.mode!r} is not supported; defined "
            f"modes: {', '.join(MODES)}",
            file=sys.stderr,
        )
        return 2

    workspace = _workspace()
    root = _contained(workspace, workspace / args.root, f"root {args.root!r}")
    output = _contained(workspace, root / args.output, f"output {args.output!r}")
    if output == workspace:
        _refuse("output must name a file")
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
        if args.mode == "blocking":
            print(
                "::error title=Biome (blocking)::Biome reported diagnostics; "
                "blocking mode fails the required job (fix them or run "
                "`biome check --write .`)"
            )
            return FINDINGS_EXIT
        print(
            "::notice title=Biome (advisory)::Biome reported diagnostics; "
            "advisory mode records them without failing the job"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
