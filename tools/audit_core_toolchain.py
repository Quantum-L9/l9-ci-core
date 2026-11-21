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

Resolution uses Core's pinned uv for the exact CPython the lock pins
(``python``, e.g. 3.12.14; a bare 3.12 would mean 3.12.0 to uv) on manylinux
x86_64, the runner every install above targets. Separate installs may pin one package at
different versions, and pip-audit refuses a requirements file that names a
package twice, so the deduplicated set is written as layers
(``layer-1.txt``, ``layer-2.txt``, ...), each naming every package at most
once; every layer is audited. The number of distinct pins is printed.

``judge`` then reads pip-audit's JSON for every layer (run without any
ignore) against Core's one exceptions file
(``install-semgrep/audit-exceptions.yaml``, ``l9.toolchain-audit-exceptions/v1``).
An exception names one advisory on one ``package@version`` with a concrete
reason it is not reachable in Core's use, an owner, a creation date, an expiry
at most 14 days later and a removal trigger. A malformed or expired entry, an
entry that matches no finding (stale), a finding no entry covers (open), or a
pinned package pip-audit did not audit fails the audit; the log names how many
pins were audited, how many findings are excepted and how many are open.
``exceptions`` validates the file and prints the advisory ids that
``--ignore-vuln`` takes for pip-audit's human-readable report.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
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
EXCEPTIONS = Path(".github/actions/install-semgrep/audit-exceptions.yaml")
EXCEPTIONS_SCHEMA = "l9.toolchain-audit-exceptions/v1"
EXCEPTION_KEYS = frozenset(
    {
        "id",
        "package",
        "version",
        "reason",
        "owner",
        "created",
        "expires",
        "removal_trigger",
    }
)
MAX_EXCEPTION_DAYS = 14
EXACT_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")
EXACT_VERSION = re.compile(
    r"\d+(?:\.\d+)*(?:(?:a|b|rc)\d+)?(?:\.post\d+)?(?:\.dev\d+)?"
)
ADVISORY = re.compile(
    r"^(?:PYSEC-\d{4}-\d+|GHSA(?:-[23456789cfghjmpqrvwx]{4}){3}|CVE-\d{4}-\d+)$"
)
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


def python_version(root: Path) -> str:
    """The exact CPython the lock pins, which every audited install runs on."""
    lock = json.loads((root / LOCK).read_text(encoding="utf-8"))
    version = lock.get("python")
    if not isinstance(version, str) or not re.fullmatch(r"3\.\d+\.\d+", version):
        msg = f"{LOCK} pins no exact Python version (got {version!r})"
        raise AuditError(msg)
    return version


def uv_resolver(uv: str, python: str) -> Resolver:
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
                    python,
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


def confined(path: Path) -> Path:
    """``path`` resolved, refused unless it lies under an allowed directory.

    The layer files are written to a CLI-named directory, which must resolve
    inside the working directory, the runner's temp directory or the system
    temp directory; a path that escapes them is refused, never followed.
    """
    resolved = Path(os.path.realpath(path))
    roots = [Path.cwd(), Path(tempfile.gettempdir())]
    runner_temp = os.environ.get("RUNNER_TEMP", "")
    if runner_temp:
        roots.append(Path(runner_temp))
    for root in roots:
        base = Path(os.path.realpath(root))
        if resolved == base or resolved.is_relative_to(base):
            return resolved
    msg = f"{path} is outside the working and temp directories"
    raise AuditError(msg)


@dataclass(frozen=True)
class AdvisoryException:
    """One Core-owned exception: an advisory on one package@version."""

    advisory: str
    package: str
    version: str
    expires: dt.date


def _date(entry: dict[str, str], key: str, where: str) -> dt.date:
    value = entry[key]
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        msg = f"{where}: {key} {value!r} is not a YYYY-MM-DD date"
        raise AuditError(msg) from None


def _entry_shape(entry: object, where: str) -> dict[str, str]:
    """The entry's fields, refused unless exactly the schema's, all non-empty text."""
    if not isinstance(entry, dict) or set(entry) != EXCEPTION_KEYS:
        msg = f"{where} must hold exactly {', '.join(sorted(EXCEPTION_KEYS))}"
        raise AuditError(msg)
    blank = sorted(
        key
        for key, value in entry.items()
        if not isinstance(value, str) or not value.strip()
    )
    if blank:
        raise AuditError(f"{where}: {', '.join(blank)} must be non-empty text")
    if not ADVISORY.match(entry["id"]):
        raise AuditError(f"{where}: {entry['id']!r} is not an advisory id")
    if not (
        EXACT_NAME.fullmatch(entry["package"])
        and EXACT_VERSION.fullmatch(entry["version"])
    ):
        msg = f"{where}: {entry['package']}@{entry['version']} is not a pin"
        raise AuditError(msg)
    return entry


def _entry_window(entry: dict[str, str], where: str, today: dt.date) -> dt.date:
    """The entry's expiry, refused when premature, too long or expired."""
    created = _date(entry, "created", where)
    expires = _date(entry, "expires", where)
    if created > today:
        raise AuditError(f"{where}: created {created} is after today {today}")
    if expires < created or (expires - created).days > MAX_EXCEPTION_DAYS:
        msg = (
            f"{where}: expires {expires} must be within {MAX_EXCEPTION_DAYS} "
            f"days of created {created}"
        )
        raise AuditError(msg)
    if expires < today:
        msg = f"{where}: {entry['id']} expired on {expires}; fix or renew it"
        raise AuditError(msg)
    return expires


def load_exceptions(path: Path, today: dt.date) -> list[AdvisoryException]:
    """Core's exceptions, refused when malformed, premature or expired."""
    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(document, dict)
        or set(document) != {"schema", "exceptions"}
        or document["schema"] != EXCEPTIONS_SCHEMA
        or not isinstance(document["exceptions"], list)
    ):
        msg = f"{path} is not an {EXCEPTIONS_SCHEMA} document"
        raise AuditError(msg)
    found: list[AdvisoryException] = []
    for index, raw in enumerate(document["exceptions"]):
        where = f"{path} exception #{index + 1}"
        entry = _entry_shape(raw, where)
        item = AdvisoryException(
            entry["id"],
            canonical(entry["package"]),
            entry["version"],
            _entry_window(entry, where, today),
        )
        if any(
            (other.advisory, other.package, other.version)
            == (item.advisory, item.package, item.version)
            for other in found
        ):
            key = (item.advisory, item.package, item.version)
            raise AuditError(f"{where}: duplicate exception for {key}")
        found.append(item)
    return found


@dataclass(frozen=True)
class Verdict:
    audited: int
    excepted: list[tuple[str, str, str]]
    open: list[tuple[str, str, str]]
    stale: list[AdvisoryException]


Finding = tuple[str, str, str, frozenset[str]]


def _findings(
    reports: Sequence[dict[str, object]],
) -> tuple[set[tuple[str, str]], list[Finding]]:
    """``(audited pins, findings)`` of pip-audit's reports; a skip is refused."""
    audited: set[tuple[str, str]] = set()
    findings: list[Finding] = []
    for report in reports:
        dependencies = report.get("dependencies")
        if not isinstance(dependencies, list):
            raise AuditError("a pip-audit report holds no dependency list")
        for dependency in dependencies:
            name = canonical(str(dependency["name"]))
            version = str(dependency.get("version", ""))
            if "skip_reason" in dependency or not version:
                reason = dependency.get("skip_reason", "no version")
                raise AuditError(f"pip-audit did not audit {name}: {reason}")
            audited.add((name, version))
            findings.extend(
                (
                    name,
                    version,
                    str(vuln["id"]),
                    frozenset([str(vuln["id"]), *map(str, vuln.get("aliases", []))]),
                )
                for vuln in dependency.get("vulns", [])
            )
    return audited, findings


def judge(
    layer_texts: Sequence[str],
    reports: Sequence[dict[str, object]],
    exceptions: Sequence[AdvisoryException],
) -> Verdict:
    """Every pinned package audited; every finding excepted or open."""
    expected = {
        (name, version) for text in layer_texts for name, version in pins(text).items()
    }
    audited, findings = _findings(reports)
    unaudited = sorted(expected - audited)
    if unaudited:
        listed = ", ".join(f"{n}=={v}" for n, v in unaudited)
        raise AuditError(f"pip-audit reported no result for {listed}")
    used: set[AdvisoryException] = set()
    excepted: list[tuple[str, str, str]] = []
    still_open: list[tuple[str, str, str]] = []
    for name, version, advisory, ids in findings:
        cover = [
            item
            for item in exceptions
            if (item.package, item.version) == (name, version) and item.advisory in ids
        ]
        used.update(cover)
        (excepted if cover else still_open).append((name, version, advisory))
    stale = [item for item in exceptions if item not in used]
    return Verdict(len(audited), excepted, still_open, stale)


def _cmd_resolve(args: argparse.Namespace) -> int:
    try:
        output_dir = confined(args.output_dir)
        tools = python_tools(args.root)
        planned = sources(args.root, tools)
        check_coverage(tools, planned)
        resolver = uv_resolver(args.uv, python_version(args.root))
        union, counts = resolve_all(planned, resolver)
    except (AuditError, OSError, ValueError, KeyError) as error:
        print(f"::error::toolchain audit: {error}", file=sys.stderr)
        return 2
    output_dir.mkdir(parents=True, exist_ok=True)
    for number, text in enumerate(layers(union), start=1):
        (output_dir / f"layer-{number}.txt").write_text(text, encoding="utf-8")
    for label, count in counts:
        print(f"toolchain audit: {label}: {count} pinned package(s)")
    total = sum(len(versions) for versions in union.values())
    print(
        f"toolchain audit: {total} distinct pinned package(s) ({len(union)} "
        f"project(s)) from {len(tools)} Core-pinned Python tool(s): "
        f"{', '.join(sorted(tools))}"
    )
    return 0


def _cmd_exceptions(args: argparse.Namespace) -> int:
    try:
        loaded = load_exceptions(args.root / EXCEPTIONS, args.today)
    except (AuditError, OSError, ValueError) as error:
        print(f"::error::toolchain audit exceptions: {error}", file=sys.stderr)
        return 2
    for advisory in sorted({item.advisory for item in loaded}):
        print(advisory)
    return 0


def _cmd_judge(args: argparse.Namespace) -> int:
    try:
        directory = confined(args.reports)
        loaded = load_exceptions(args.root / EXCEPTIONS, args.today)
        texts = [
            p.read_text(encoding="utf-8") for p in sorted(directory.glob("layer-*.txt"))
        ]
        jsons = sorted(directory.glob("layer-*.json"))
        if not texts or len(jsons) != len(texts):
            msg = f"{directory} holds {len(texts)} layer(s) and {len(jsons)} report(s)"
            raise AuditError(msg)
        reports = [json.loads(p.read_text(encoding="utf-8")) for p in jsons]
        verdict = judge(texts, reports, loaded)
    except (AuditError, OSError, ValueError, KeyError) as error:
        print(f"::error::toolchain audit: {error}", file=sys.stderr)
        return 2
    for name, version, advisory in verdict.excepted:
        print(f"toolchain audit: excepted {advisory} on {name}=={version}")
    for name, version, advisory in verdict.open:
        print(f"::error::toolchain audit: open {advisory} on {name}=={version}")
    for item in verdict.stale:
        print(
            f"::error::toolchain audit: stale exception {item.advisory} on "
            f"{item.package}=={item.version} matches no finding; remove it"
        )
    print(
        f"toolchain audit: {verdict.audited} pinned package(s) audited, "
        f"{len(verdict.excepted)} finding(s) excepted, {len(verdict.open)} open"
    )
    if verdict.stale:
        return 2
    return 1 if verdict.open else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="audit_core_toolchain")
    parser.add_argument("--root", type=Path, default=Path("."))
    commands = parser.add_subparsers(dest="command", required=True)
    resolve = commands.add_parser("resolve", help="write the layered pinned set")
    resolve.add_argument("--uv", default="uv", help="Core's pinned uv executable")
    resolve.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="directory the layer-<n>.txt requirement files are written to",
    )
    resolve.set_defaults(handler=_cmd_resolve)
    today = {
        "type": dt.date.fromisoformat,
        "required": True,
        "help": "YYYY-MM-DD (UTC)",
    }
    exceptions = commands.add_parser("exceptions", help="validate; print ignore ids")
    exceptions.add_argument("--today", **today)  # type: ignore[arg-type]
    exceptions.set_defaults(handler=_cmd_exceptions)
    verdict = commands.add_parser("judge", help="judge pip-audit's JSON reports")
    verdict.add_argument("--today", **today)  # type: ignore[arg-type]
    verdict.add_argument("--reports", type=Path, required=True)
    verdict.set_defaults(handler=_cmd_judge)
    args = parser.parse_args(argv)
    handler: Callable[[argparse.Namespace], int] = args.handler
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
