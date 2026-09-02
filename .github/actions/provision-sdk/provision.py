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
EXPECTED_REVISION = "0efd762d1617a1c8635005d0611b1cf6f2303987"
# Fallback default only. The verified contract Core emits is the one declared by
# the selected manifest entry (select_manifest_entry) and cross-checked against
# the SDK's own integration-contract.yaml; this constant is used only when no
# entry-specific contract is available.
EXPECTED_CONTRACT = "l9.integration-contract/v1"
# Semgrep runtime floor used only when the pinned SDK checkout declares no
# `[project.optional-dependencies].semgrep` group of its own. `semgrep run`
# shells out to the semgrep binary, so the provisioned SDK venv must carry it;
# the pin travels with the pinned SDK revision whenever the SDK declares it.
SEMGREP_RUNTIME_FALLBACK = "semgrep>=1.100"
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
        run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--quiet",
                "--disable-pip-version-check",
                "pyyaml",
            ]
        )
        import yaml
    return yaml


def _load_manifest(manifest_path: Path) -> dict:
    """Parse the compatibility manifest. It is the single source of truth for the
    allowlist, so fail closed if it is missing or malformed."""
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
    return data


def load_supported_entries(
    manifest_path: Path = COMPATIBILITY_MANIFEST,
) -> list[dict]:
    """Every full SDK compatibility record — the `supported[]` entries. The
    top-level `default` block is only a pointer (source/repository/revision) to
    one of these, not a record of its own, so it is not returned here. Fail
    closed if the manifest is unreadable or lists no supported revisions."""
    data = _load_manifest(manifest_path)
    supported = data.get("supported")
    entries = (
        [entry for entry in supported if isinstance(entry, dict)]
        if isinstance(supported, list)
        else []
    )
    entries = [
        entry
        for entry in entries
        if isinstance(entry.get("revision"), str) and entry["revision"].strip()
    ]
    if not entries:
        raise ProvisioningError(
            "SDK compatibility manifest lists no supported revisions"
        )
    return entries


def load_supported_revisions(
    manifest_path: Path = COMPATIBILITY_MANIFEST,
) -> frozenset[str]:
    """The set of SDK revisions Core allows: every `supported[]` revision plus
    the `default` pointer's revision. Fail closed if the manifest is unreadable."""
    revisions = {
        entry["revision"].strip().lower()
        for entry in load_supported_entries(manifest_path)
    }
    default = _load_manifest(manifest_path).get("default")
    if isinstance(default, dict) and isinstance(default.get("revision"), str):
        revisions.add(default["revision"].strip().lower())
    return frozenset(revisions)


def select_manifest_entry(
    revision: str,
    manifest_path: Path = COMPATIBILITY_MANIFEST,
) -> dict:
    """The single `supported[]` compatibility record matching ``revision``. The
    manifest is the executable contract: the returned entry drives contract
    verification and the CLI probes, so fail closed if the entry is absent or
    omits the fields Core proves (``integration_contract``, a nonempty
    ``required_cli_paths`` list)."""
    wanted = revision.strip().lower()
    matches = [
        entry
        for entry in load_supported_entries(manifest_path)
        if entry["revision"].strip().lower() == wanted
    ]
    if not matches:
        raise ProvisioningError(
            f"no compatibility entry for revision {revision!r} in {manifest_path.name}"
        )
    entry = matches[0]
    contract = entry.get("integration_contract")
    if not isinstance(contract, str) or not contract.strip():
        raise ProvisioningError(
            f"compatibility entry for {revision!r} omits integration_contract"
        )
    paths = entry.get("required_cli_paths")
    if (
        not isinstance(paths, list)
        or not paths
        or not all(isinstance(path, str) and path.strip() for path in paths)
    ):
        raise ProvisioningError(
            f"compatibility entry for {revision!r} omits required_cli_paths"
        )
    return entry


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


def verify_contract_file(checkout: Path, entry: dict) -> None:
    """Cross-check the SDK's own integration contract against the compatibility
    entry Core selected. The manifest — not a hand-picked list of text fragments
    — is the contract of record: the SDK must declare the same
    ``integration_contract`` schema and the ``l9-ci`` executable, or provisioning
    fails closed. Actual command existence is proven by execution in probe_cli."""
    contract = checkout / ".l9" / "integration-contract.yaml"
    if not contract.is_file():
        raise ProvisioningError("SDK is missing .l9/integration-contract.yaml")
    yaml = _load_yaml_module()
    try:
        data = yaml.safe_load(contract.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as error:
        raise ProvisioningError(
            f"SDK integration-contract.yaml is not valid YAML: {error}"
        ) from error
    expected_contract = entry["integration_contract"].strip()
    declared = data.get("schema")
    if declared != expected_contract:
        raise ProvisioningError(
            "SDK integration contract schema "
            f"{declared!r} does not match the compatibility entry "
            f"{expected_contract!r}"
        )
    cli = data.get("CLI")
    executable = cli.get("executable") if isinstance(cli, dict) else None
    if executable != "l9-ci":
        raise ProvisioningError(
            f"SDK integration contract declares executable {executable!r}, "
            "expected 'l9-ci'"
        )


def resolve_semgrep_requirements(checkout: Path) -> list[str]:
    """Semgrep runtime specifiers sourced from the pinned SDK checkout.

    Prefer the SDK's own ``[project.optional-dependencies].semgrep`` group so the
    pin travels with the pinned SDK revision. Fall back to the documented
    provider floor only when the checkout declares no such group. The manifest
    of record is the SDK the revision resolves to, never a hand-picked version
    in Core."""
    pyproject = checkout / "pyproject.toml"
    if pyproject.is_file():
        try:
            import tomllib
        except ModuleNotFoundError:  # pragma: no cover - py<3.11 runners
            tomllib = None  # type: ignore[assignment]
        if tomllib is not None:
            try:
                data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            except tomllib.TOMLDecodeError as error:
                raise ProvisioningError(
                    f"SDK pyproject.toml is not valid TOML: {error}"
                ) from error
            project = data.get("project")
            extras = (
                project.get("optional-dependencies")
                if isinstance(project, dict)
                else None
            )
            group = extras.get("semgrep") if isinstance(extras, dict) else None
            if isinstance(group, list):
                specifiers = [
                    item.strip()
                    for item in group
                    if isinstance(item, str) and item.strip()
                ]
                if specifiers:
                    return specifiers
    return [SEMGREP_RUNTIME_FALLBACK]


def install_semgrep_runtime(checkout: Path, venv_python: Path) -> list[str]:
    """Install the SDK's optional Semgrep execution runtime into the venv.

    ``semgrep run`` executes the semgrep binary, so a provisioned SDK that only
    carries the import-time requirements cannot run it. Returns the installed
    specifiers for evidence."""
    specifiers = resolve_semgrep_requirements(checkout)
    run(
        [
            str(venv_python),
            "-m",
            "pip",
            "install",
            "--quiet",
            "--disable-pip-version-check",
            *specifiers,
        ]
    )
    return specifiers


def create_runtime(checkout: Path, runtime: Path) -> Path:
    venv = runtime / "venv"
    run([sys.executable, "-m", "venv", str(venv)])
    venv_python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    # SDK runs from source via PYTHONPATH and ships no build; install its
    # committed, pinned dependency manifest into the isolated venv or
    # `python -m l9_ci` fails at import (ModuleNotFoundError: yaml).
    requirements = checkout / "requirements.txt"
    if requirements.is_file():
        run(
            [
                str(venv_python),
                "-m",
                "pip",
                "install",
                "--quiet",
                "--disable-pip-version-check",
                "-r",
                str(requirements),
            ]
        )
    # SDK-owned Semgrep execution (`semgrep run`) needs the semgrep binary in the
    # provisioned venv; the pin is sourced from the pinned SDK checkout.
    install_semgrep_runtime(checkout, venv_python)
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


def probe_cli(executable: Path, required_cli_paths: list[str]) -> None:
    """Prove that every CLI path the selected compatibility entry declares
    actually exists on the provisioned SDK by executing ``<path> --help``. The
    manifest is the executable contract: Core must not claim a command is
    required without proving it resolves. Fail closed on the first missing path."""
    root = subprocess.run(
        [str(executable), "--help"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if root.returncode != 0:
        raise ProvisioningError(f"SDK CLI probe failed: --help\n{root.stdout}")
    for path in required_cli_paths:
        arguments = [*path.split(), "--help"]
        result = subprocess.run(
            [str(executable), *arguments],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        if result.returncode != 0:
            raise ProvisioningError(
                f"SDK CLI path {path!r} is not available "
                f"(probe `{' '.join(arguments)}` failed)\n{result.stdout}"
            )


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
        entry = select_manifest_entry(revision)
        contract = entry["integration_contract"].strip()
        required_cli_paths = [path.strip() for path in entry["required_cli_paths"]]
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
        verify_contract_file(checkout, entry)
        executable = create_runtime(checkout, runtime)
        probe_cli(executable, required_cli_paths)
        emit_output("executable", str(executable))
        emit_output("sdk-root", str(checkout.resolve()))
        emit_output("sdk-revision", revision)
        emit_output("contract", contract)
        print(f"Provisioned l9-ci-sdk {revision} with contract {contract}")
        return 0
    except ProvisioningError as error:
        print(f"provision-sdk: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
