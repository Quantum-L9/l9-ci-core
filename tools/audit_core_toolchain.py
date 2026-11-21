#!/usr/bin/env python3
"""Resolve Core's pinned toolchain into one pinned requirements set to audit.

Nothing else audits the tools Core pins for every governed repository: a
consumer's lock export leaves them out by design, and Core's own lock holds
two packages. This builds the set ``pip-audit --no-deps`` audits from the
installs Core actually performs, each at its pinned versions:

* the consumer CI toolchain (``install-consumer-ci/requirements-consumer-ci.txt``),
  resolved together, as ``install.sh`` installs it;
* Semgrep, from its hash-locked install contract
  (``install-semgrep/locks/semgrep-<version>.txt``), which is the closure
  ``install-semgrep`` installs, byte for byte;
* every other Python tool of the toolchain lock (bandit, pip-audit today),
  resolved alone, as ``security.yml`` installs each.

Every tool ``core-owned-tools.json`` lists under the ``pip`` ecosystem must be
covered at the version ``toolchain-lock.json`` pins, or the set is refused: a
tool the audit cannot see is not a clean result (L9-UNKNOWN-001). biome (npm),
gitleaks (a binary) and the interpreter are not Python packages, so pip-audit
cannot judge them; they are out of this audit's scope.

Resolution uses Core's pinned uv for CPython 3.12 on manylinux x86_64, the
runner every install above targets. Separate installs may pin one package at
different versions, and pip-audit refuses a requirements file that names a
package twice, so the deduplicated set is written as layers
(``layer-1.txt``, ``layer-2.txt``, ...), each naming every package at most
once; every layer is audited. The number of distinct pins is printed.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

CONSUMER_CI = Path(".github/actions/install-consumer-ci")
LOCK = CONSUMER_CI / "toolchain-lock.json"
OWNERSHIP = CONSUMER_CI / "core-owned-tools.json"
CONSUMER_REQUIREMENTS = CONSUMER_CI / "requirements-consumer-ci.txt"
SEMGREP_LOCKS = Path(".github/actions/install-semgrep/locks")
PYTHON_VERSION = "3.12"
PLATFORM = "x86_64-manylinux_2_28"
PIN = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*==\s*(?P<version>[^\s;\\]+)"
)


class AuditError(Exception):
    """The toolchain cannot be resolved into a complete set (exit 2)."""


def canonical(name: str) -> str:
    """PEP 503 normalised project name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def pins(text: str) -> dict[str, str]:
    """``{canonical name: version}`` of every ``name==version`` line."""
    found: dict[str, str] = {}
    for line in text.splitlines():
        match = PIN.match(line.strip())
        if match:
            found[canonical(match.group("name"))] = match.group("version")
    return found


@dataclass(frozen=True)
class Source:
    """One install Core performs: what it installs, and the tools it covers."""

    label: str
    requirements: str
    covers: tuple[str, ...]
    locked: bool  # a closure already pinned, audited as written


def python_tools(root: Path) -> dict[str, str]:
    """``{pip package: pinned version}`` for every Python tool Core pins."""
    lock = json.loads((root / LOCK).read_text(encoding="utf-8"))
    ownership = json.loads((root / OWNERSHIP).read_text(encoding="utf-8"))
    tools: dict[str, str] = {}
    for tool in ownership["tools"]:
        for package in tool.get("ecosystems", {}).get("pip", []):
            version = lock.get(tool["name"])
            if not isinstance(version, str) or not version:
                msg = f"{tool['name']} has a pip package but no version in {LOCK}"
                raise AuditError(msg)
            tools[canonical(package)] = version
    if not tools:
        raise AuditError(f"{OWNERSHIP} names no Python tool")
    return tools


def sources(root: Path, tools: dict[str, str]) -> list[Source]:
    """The installs that together cover every Python tool, in audit order."""
    consumer_text = (root / CONSUMER_REQUIREMENTS).read_text(encoding="utf-8")
    planned = [
        Source(
            "consumer CI toolchain",
            consumer_text,
            tuple(sorted(pins(consumer_text))),
            locked=False,
        )
    ]
    semgrep = tools.get("semgrep")
    if semgrep is not None:
        path = root / SEMGREP_LOCKS / f"semgrep-{semgrep}.txt"
        if not path.is_file():
            msg = f"semgrep {semgrep} has no hash-locked closure at {path}"
            raise AuditError(msg)
        planned.append(
            Source(
                f"semgrep {semgrep}",
                path.read_text(encoding="utf-8"),
                ("semgrep",),
                locked=True,
            )
        )
    covered = {name for source in planned for name in source.covers}
    planned.extend(
        Source(name, f"{name}=={tools[name]}\n", (name,), locked=False)
        for name in sorted(set(tools) - covered)
    )
    return planned


def check_coverage(tools: dict[str, str], planned: Sequence[Source]) -> None:
    """Every Python tool is installed by some source at its pinned version."""
    for name, version in sorted(tools.items()):
        holders = [source for source in planned if name in source.covers]
        if not holders:
            raise AuditError(f"{name}=={version} is installed by no audited source")
        for source in holders:
            stated = pins(source.requirements).get(name)
            if stated != version:
                msg = (
                    f"{source.label} pins {name}=={stated}, but {LOCK} pins "
                    f"{version}: the lock and the install disagree"
                )
                raise AuditError(msg)


Resolver = Callable[[str], str]


def uv_resolver(uv: str) -> Resolver:
    """Resolve requirements with uv for the runner Core installs on."""

    def resolve(requirements: str) -> str:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "in.txt")
            output = Path(directory, "out.txt")
            source.write_text(requirements, encoding="utf-8")
            completed = subprocess.run(
                [
                    uv,
                    "pip",
                    "compile",
                    str(source),
                    "--python-version",
                    PYTHON_VERSION,
                    "--python-platform",
                    PLATFORM,
                    "--no-header",
                    "--no-annotate",
                    "--quiet",
                    "--output-file",
                    str(output),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                msg = f"uv could not resolve: {completed.stderr.strip()}"
                raise AuditError(msg)
            return output.read_text(encoding="utf-8")

    return resolve


def resolve_all(
    planned: Sequence[Source], resolver: Resolver
) -> tuple[dict[str, set[str]], list[tuple[str, int]]]:
    """``({name: versions}, [(source label, package count)])`` of every source."""
    union: dict[str, set[str]] = {}
    counts: list[tuple[str, int]] = []
    for source in planned:
        text = source.requirements if source.locked else resolver(source.requirements)
        resolved = pins(text)
        if not resolved:
            raise AuditError(f"{source.label} resolved to no pinned package")
        missing = [name for name in source.covers if name not in resolved]
        if missing:
            raise AuditError(f"{source.label} did not resolve {', '.join(missing)}")
        counts.append((source.label, len(resolved)))
        for name, version in resolved.items():
            union.setdefault(name, set()).add(version)
    return union, counts


def layers(union: dict[str, set[str]]) -> list[str]:
    """The audited set as requirement files that each name a package once.

    Layer ``k`` holds the ``k``-th version (sorted) of every package pinned at
    ``k`` or more versions, so the layers together hold every distinct pin
    exactly once.
    """
    depth = max((len(versions) for versions in union.values()), default=0)
    return [
        "".join(
            f"{name}=={sorted(union[name])[index]}\n"
            for name in sorted(union)
            if len(union[name]) > index
        )
        for index in range(depth)
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="audit_core_toolchain")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--uv", default="uv", help="Core's pinned uv executable")
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="directory the layer-<n>.txt requirement files are written to",
    )
    args = parser.parse_args(argv)
    try:
        tools = python_tools(args.root)
        planned = sources(args.root, tools)
        check_coverage(tools, planned)
        union, counts = resolve_all(planned, uv_resolver(args.uv))
    except (AuditError, OSError, ValueError, KeyError) as error:
        print(f"::error::toolchain audit: {error}", file=sys.stderr)
        return 2
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for number, text in enumerate(layers(union), start=1):
        (args.output_dir / f"layer-{number}.txt").write_text(text, encoding="utf-8")
    for label, count in counts:
        print(f"toolchain audit: {label}: {count} pinned package(s)")
    total = sum(len(versions) for versions in union.values())
    print(
        f"toolchain audit: {total} distinct pinned package(s) ({len(union)} "
        f"project(s)) from {len(tools)} Core-pinned Python tool(s): "
        f"{', '.join(sorted(tools))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
