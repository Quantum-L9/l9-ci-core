#!/usr/bin/env python3
"""Regenerate a wheel-only, hash-locked SDK runtime dependency closure.

The selected SDK checkout is executed from source, but its requirements file is
never installed. This operator tool downloads that file at an allowlisted full
revision, records its exact SHA-256 digest, asks pip to resolve the closure for
the named Core runtime platform, and obtains each accepted wheel digest from
PyPI. Workflows never run this generator; promotion is a reviewed two-commit
operation where the lock and compatibility entry land before any workflow pin
can select them.

    python3 .github/actions/provision-sdk/lock_runtime.py \
      --revision bc678190582694f6efee08b6b7ea39be7e09bd5c
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import ssl
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

SDK_RAW = "https://raw.githubusercontent.com/Quantum-L9/l9-ci-sdk"
PYPI = "https://pypi.org/pypi"
PLATFORMS = (
    "manylinux_2_34_x86_64",
    "manylinux_2_28_x86_64",
    "manylinux_2_17_x86_64",
    "manylinux2014_x86_64",
)
DEFAULT_PIP = "26.2.1"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


class LockError(RuntimeError):
    """The lock could not be produced honestly."""


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def download(url: str, context: ssl.SSLContext) -> bytes:
    request = urllib.request.Request(
        url, headers={"Accept": "application/octet-stream"}
    )
    # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected -- fixed HTTPS origins above
    with urllib.request.urlopen(request, context=context, timeout=60) as response:  # noqa: S310
        return response.read()


def fetch_requirements(revision: str, context: ssl.SSLContext) -> bytes:
    return download(f"{SDK_RAW}/{revision}/requirements.txt", context)


def fetch_resolver_wheel(
    version: str, context: ssl.SSLContext, destination: Path
) -> Path:
    """Fetch and verify the exact universal pip wheel used as the resolver."""
    request = urllib.request.Request(
        f"{PYPI}/pip/{version}/json", headers={"Accept": "application/json"}
    )
    # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected -- fixed HTTPS PyPI prefix above
    with urllib.request.urlopen(request, context=context, timeout=60) as response:  # noqa: S310
        document = json.load(response)
    filename = f"pip-{version}-py3-none-any.whl"
    candidates = [
        entry
        for entry in document.get("urls") or []
        if entry.get("filename") == filename
        and entry.get("packagetype") == "bdist_wheel"
        and not entry.get("yanked")
    ]
    if len(candidates) != 1:
        raise LockError(f"pip=={version}: expected one non-yanked universal wheel")
    candidate = candidates[0]
    url = str(candidate.get("url", ""))
    expected = str((candidate.get("digests") or {}).get("sha256", ""))
    if not url.startswith("https://files.pythonhosted.org/") or not re.fullmatch(
        r"[0-9a-f]{64}", expected
    ):
        raise LockError(f"pip=={version}: invalid PyPI wheel metadata")
    payload = download(url, context)
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected:
        raise LockError(f"pip=={version}: resolver wheel digest mismatch")
    destination.mkdir(parents=True, exist_ok=False)
    wheel = destination / filename
    wheel.write_bytes(payload)
    return wheel


def resolve_closure(
    requirements: Path,
    python: str,
    pip_version: str,
    resolver: Path,
    destination: Path,
) -> list[Path]:
    abi = "cp" + python.replace(".", "")
    argv = [
        sys.executable,
        str(resolver / "pip"),
        "download",
        "-r",
        str(requirements),
        f"pip=={pip_version}",
        "--dest",
        str(destination),
        "--only-binary",
        ":all:",
        "--python-version",
        python,
        "--implementation",
        "cp",
        "--abi",
        abi,
        "--quiet",
    ]
    for supported in PLATFORMS:
        argv.extend(["--platform", supported])
    completed = subprocess.run(argv, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise LockError(f"pip download failed:\n{completed.stderr.strip()}")
    wheels = sorted(destination.glob("*.whl"))
    if not wheels:
        raise LockError("pip download produced no wheels")
    return wheels


def wheel_digests(
    name: str, version: str, context: ssl.SSLContext
) -> tuple[str, list[str]]:
    request = urllib.request.Request(
        f"{PYPI}/{name}/{version}/json", headers={"Accept": "application/json"}
    )
    # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected -- fixed HTTPS PyPI prefix above
    with urllib.request.urlopen(request, context=context, timeout=60) as response:  # noqa: S310
        document = json.load(response)
    digests = sorted(
        {
            str(entry["digests"]["sha256"])
            for entry in document.get("urls") or []
            if entry.get("packagetype") == "bdist_wheel" and not entry.get("yanked")
        }
    )
    if not digests:
        raise LockError(f"{name}=={version}: PyPI lists no wheel for this release")
    return canonical(str(document["info"]["name"])), digests


def render(
    revision: str,
    requirements_sha256: str,
    python: str,
    entries: list[tuple[str, str, list[str]]],
) -> str:
    lines = [
        "# Hash-locked install contract for the l9-ci-sdk source runtime.",
        f"# Derived by lock_runtime.py from SDK {revision} requirements.txt",
        f"# (sha256:{requirements_sha256}) for CPython {python} on Linux x86_64.",
        "# Every requirement is exact and wheel-only; provisioning installs this",
        "# Core-owned closure and never installs the checkout requirements file.",
        "# Regenerate with lock_runtime.py; never hand-edit a digest.",
        "",
    ]
    for project, pinned, digests in entries:
        lines.append(f"{project}=={pinned} \\")
        for index, digest in enumerate(digests):
            tail = " \\" if index < len(digests) - 1 else ""
            lines.append(f"    --hash=sha256:{digest}{tail}")
    return "\n".join(lines) + "\n"


def build(
    revision: str,
    python: str,
    pip_version: str,
    context: ssl.SSLContext,
) -> tuple[str, str]:
    requirements_bytes = fetch_requirements(revision, context)
    requirements_sha256 = hashlib.sha256(requirements_bytes).hexdigest()
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        requirements = root / "requirements.txt"
        requirements.write_bytes(requirements_bytes)
        resolver = fetch_resolver_wheel(pip_version, context, root / "resolver")
        resolution_python = ".".join(python.split(".")[:2])
        wheels = resolve_closure(
            requirements,
            resolution_python,
            pip_version,
            resolver,
            root / "wheels",
        )
        entries: list[tuple[str, str, list[str]]] = []
        seen: set[str] = set()
        for wheel in wheels:
            name, pinned = wheel.name.split("-")[:2]
            project, digests = wheel_digests(name, pinned, context)
            if project in seen:
                raise LockError(f"{project}: resolved twice")
            local = hashlib.sha256(wheel.read_bytes()).hexdigest()
            if local not in digests:
                raise LockError(f"{project}=={pinned}: downloaded wheel is not on PyPI")
            seen.add(project)
            # Lock the exact wheel pip selected for the declared platform, not
            # every wheel uploaded for the release. The runtime platform guard
            # in provision.py makes that intentionally narrow lock portable
            # only across the named CPython/Linux/x86_64 runner class.
            entries.append((project, pinned, [local]))
    entries.sort(key=lambda entry: entry[0])
    return requirements_sha256, render(revision, requirements_sha256, python, entries)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--revision", required=True, help="allowlisted full SDK SHA")
    parser.add_argument("--python", default="3.12.14", help="exact CPython version")
    parser.add_argument("--pip", default=DEFAULT_PIP, help="pip version to lock")
    parser.add_argument(
        "--output",
        default=None,
        help="lock path (default: locks/cpython-<python>-linux-x86_64.txt)",
    )
    parser.add_argument(
        "--ca-bundle",
        default=os.environ.get("SSL_CERT_FILE") or None,
        help="CA bundle for GitHub/PyPI",
    )
    arguments = parser.parse_args(argv)
    if not FULL_SHA.fullmatch(arguments.revision):
        print("lock_runtime: --revision must be a full lowercase SHA", file=sys.stderr)
        return 2
    if not re.fullmatch(r"3\.[0-9]+\.[0-9]+", arguments.python):
        print("lock_runtime: --python must be an exact 3.N.P", file=sys.stderr)
        return 2
    output = (
        Path(arguments.output)
        if arguments.output
        else Path(__file__).resolve().parent
        / "locks"
        / f"cpython-{arguments.python}-linux-x86_64.txt"
    )
    context = ssl.create_default_context(cafile=arguments.ca_bundle)
    try:
        requirements_sha256, text = build(
            arguments.revision,
            arguments.python,
            arguments.pip,
            context,
        )
    except (LockError, OSError, KeyError, ValueError) as error:
        print(f"lock_runtime: {error}", file=sys.stderr)
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    print(f"lock_runtime: requirements_sha256={requirements_sha256}")
    print(f"lock_runtime: wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
