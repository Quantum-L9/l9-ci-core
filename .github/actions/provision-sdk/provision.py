#!/usr/bin/env python3
"""Provision and verify the immutable l9-ci-sdk Phase 1 dependency."""

from __future__ import annotations
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

EXPECTED_SOURCE = "git"
EXPECTED_REPOSITORY = "https://github.com/Quantum-L9/l9-ci-sdk.git"
# Fallback default only. The authoritative allowlist is `.l9/sdk-compatibility.yaml`
# (read by load_supported_revisions); keep this in sync with its `default.revision`.
EXPECTED_REVISION = "6368ba17a98231d461a13b71e149e114ad766834"
EXPECTED_CONTRACT = "l9.integration-contract/v1"
FULL_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
# Repo-root .l9/sdk-compatibility.yaml, relative to this action file
# (.github/actions/provision-sdk/provision.py -> parents[3] == repo root).
COMPATIBILITY_MANIFEST = (
    Path(__file__).resolve().parents[3] / ".l9" / "sdk-compatibility.yaml"
)


class ProvisioningError(RuntimeError):
    pass


def _load_yaml_module():
    # provision.py runs on the runner's system python3, before any venv exists,
    # so PyYAML may be absent; install it on demand rather than failing.
    try:
        import yaml
    except ModuleNotFoundError:
        run([sys.executable, "-m", "pip", "install", "--quiet",
             "--disable-pip-version-check", "pyyaml"])
        import yaml
    return yaml


def load_supported_revisions(
    manifest_path: Path = COMPATIBILITY_MANIFEST,
) -> frozenset[str]:
    """The set of SDK revisions Core allows, read from the compatibility
    manifest (its `default` plus every `supported[]` entry). The file is the
    single source of truth for the allowlist; fail closed if it is unreadable."""
    if not manifest_path.is_file():
        raise ProvisioningError(
            f"SDK compatibility manifest not found: {manifest_path}"
        )
    yaml = _load_yaml_module()
    try:
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as error:
        raise ProvisioningError(
            f"SDK compatibility manifest is not valid YAML: {error}"
        ) from error
    entries = []
    default = data.get("default")
    if isinstance(default, dict):
        entries.append(default)
    supported = data.get("supported")
    if isinstance(supported, list):
        entries.extend(entry for entry in supported if isinstance(entry, dict))
    revisions = {
        entry["revision"].strip().lower()
        for entry in entries
        if isinstance(entry.get("revision"), str) and entry["revision"].strip()
    }
    if not revisions:
        raise ProvisioningError(
            "SDK compatibility manifest lists no supported revisions"
        )
    return frozenset(revisions)


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )
    if result.returncode != 0:
        output = result.stdout or ""
        raise ProvisioningError(
            f"command failed with exit code {result.returncode}: "
            f"{' '.join(command)}\n{output}"
        )
    return result


def require_environment(name: str, default: str) -> str:
    value = os.environ.get(name, default).strip()
    if not value:
        raise ProvisioningError(f"{name} must not be empty")
    return value


def validate_inputs(source: str, repository: str, revision: str) -> None:
    if source != EXPECTED_SOURCE:
        raise ProvisioningError(
            f"unsupported sdk-source {source!r}; Phase 1 permits only "
            f"{EXPECTED_SOURCE!r}"
        )
    if repository != EXPECTED_REPOSITORY:
        raise ProvisioningError(
            "sdk-repository is not the authoritative SDK repository"
        )
    if not FULL_SHA.fullmatch(revision):
        raise ProvisioningError(
            "sdk-revision must be a full 40-character hexadecimal commit SHA"
        )
    if revision.lower() not in load_supported_revisions():
        raise ProvisioningError(
            "sdk-revision is not listed in .l9/sdk-compatibility.yaml"
        )


def checkout_sdk(repository: str, revision: str, checkout: Path) -> None:
    if checkout.exists():
        shutil.rmtree(checkout)
    checkout.mkdir(parents=True)
    run(["git", "init", "--quiet"], cwd=checkout)
    run(["git", "remote", "add", "origin", repository], cwd=checkout)
    run(
        [
            "git",
            "-c",
            "protocol.version=2",
            "fetch",
            "--quiet",
            "--depth=1",
            "origin",
            revision,
        ],
        cwd=checkout,
    )
    run(["git", "checkout", "--quiet", "--detach", "FETCH_HEAD"], cwd=checkout)
    actual = run(
        ["git", "rev-parse", "HEAD"],
        cwd=checkout,
        capture=True,
    ).stdout.strip()
    if actual != revision.lower():
        raise ProvisioningError(
            f"checked-out SDK revision {actual!r} does not match {revision!r}"
        )


def verify_contract_file(checkout: Path) -> None:
    contract = checkout / ".l9" / "integration-contract.yaml"
    if not contract.is_file():
        raise ProvisioningError("SDK is missing .l9/integration-contract.yaml")
    text = contract.read_text(encoding="utf-8")
    required_fragments = (
        "schema: l9.integration-contract/v1",
        "executable: l9-ci",
        "semgrep normalize",
        "bundle validate",
        "bundle project-agent-payload",
        "compatibility check",
    )
    missing = [fragment for fragment in required_fragments if fragment not in text]
    if missing:
        raise ProvisioningError(
            "SDK integration contract is incompatible; missing: " + ", ".join(missing)
        )


def create_runtime(checkout: Path, runtime: Path) -> Path:
    venv = runtime / "venv"
    run([sys.executable, "-m", "venv", str(venv)])
    venv_python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    # SDK runs from source via PYTHONPATH and ships no build; install its
    # committed, pinned dependency manifest into the isolated venv or
    # `python -m l9_ci` fails at import (ModuleNotFoundError: yaml).
    requirements = checkout / "requirements.txt"
    if requirements.is_file():
        run([str(venv_python), "-m", "pip", "install", "--quiet",
             "--disable-pip-version-check", "-r", str(requirements)])
    if os.name == "nt":
        python = venv / "Scripts" / "python.exe"
        executable = runtime / "l9-ci.cmd"
        executable.write_text(
            "@echo off\r\n"
            f'set "PYTHONPATH={checkout};%PYTHONPATH%"\r\n'
            f'"{python}" -m l9_ci %*\r\n',
            encoding="utf-8",
        )
    else:
        python = venv / "bin" / "python"
        executable = runtime / "bin" / "l9-ci"
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f'export PYTHONPATH="{checkout}${{PYTHONPATH:+:$PYTHONPATH}}"\n'
            f'exec "{python}" -m l9_ci "$@"\n',
            encoding="utf-8",
        )
        executable.chmod(0o755)
    return executable.resolve()


def probe_cli(executable: Path) -> None:
    probes = (
        ["--help"],
        ["semgrep", "--help"],
        ["bundle", "--help"],
        ["compatibility", "--help"],
    )
    for arguments in probes:
        result = subprocess.run(
            [str(executable), *arguments],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        if result.returncode != 0:
            raise ProvisioningError(
                f"SDK CLI probe failed: {' '.join(arguments)}\n{result.stdout}"
            )
    root_help = subprocess.run(
        [str(executable), "--help"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    ).stdout
    for command in ("semgrep", "bundle", "compatibility"):
        if command not in root_help:
            raise ProvisioningError(f"SDK CLI root help does not expose {command!r}")


def emit_output(name: str, value: str) -> None:
    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with open(output_file, "a", encoding="utf-8") as stream:
            stream.write(f"{name}={value}\n")
    else:
        print(f"{name}={value}")


def main() -> int:
    try:
        source = require_environment("INPUT_SDK_SOURCE", EXPECTED_SOURCE)
        repository = require_environment(
            "INPUT_SDK_REPOSITORY",
            EXPECTED_REPOSITORY,
        )
        revision = require_environment(
            "INPUT_SDK_REVISION",
            EXPECTED_REVISION,
        ).lower()
        runtime_input = require_environment(
            "INPUT_RUNTIME_DIRECTORY",
            ".l9/runtime/sdk",
        )
        validate_inputs(source, repository, revision)
        workspace = Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd())).resolve()
        runtime = (workspace / runtime_input).resolve()
        try:
            runtime.relative_to(workspace)
        except ValueError as error:
            raise ProvisioningError(
                "runtime-directory must remain inside GITHUB_WORKSPACE"
            ) from error
        if runtime.exists():
            shutil.rmtree(runtime)
        runtime.mkdir(parents=True)
        checkout = runtime / "source"
        checkout_sdk(repository, revision, checkout)
        verify_contract_file(checkout)
        executable = create_runtime(checkout, runtime)
        probe_cli(executable)
        emit_output("executable", str(executable))
        emit_output("sdk-root", str(checkout.resolve()))
        emit_output("sdk-revision", revision)
        emit_output("contract", EXPECTED_CONTRACT)
        print(f"Provisioned l9-ci-sdk {revision} with contract {EXPECTED_CONTRACT}")
        return 0
    except ProvisioningError as error:
        print(f"provision-sdk: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
