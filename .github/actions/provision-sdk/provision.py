#!/usr/bin/env python3
"""Provision and verify the immutable l9-ci-sdk Phase 1 dependency."""

from __future__ import annotations

import hashlib
import os
import platform
import re
import subprocess
import sys
from pathlib import Path

EXPECTED_SOURCE = "git"
EXPECTED_REPOSITORY = "https://github.com/Quantum-L9/l9-ci-sdk.git"
# Fallback default only. The authoritative allowlist is `.l9/sdk-compatibility.yaml`
# (read by load_supported_revisions); keep this in sync with its `default.revision`.
EXPECTED_REVISION = "bc678190582694f6efee08b6b7ea39be7e09bd5c"
# Fallback default only. The verified contract Core emits is the one declared by
# the selected manifest entry (select_manifest_entry) and cross-checked against
# the SDK's own integration-contract.yaml; this constant is used only when no
# entry-specific contract is available.
EXPECTED_CONTRACT = "l9.integration-contract/v1"
RUNTIME_DIRECTORY = Path(".l9/runtime/sdk")
FULL_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
# Repo-root .l9/sdk-compatibility.yaml, relative to this action file
# (.github/actions/provision-sdk/provision.py -> parents[3] == repo root).
COMPATIBILITY_MANIFEST = (
    Path(__file__).resolve().parents[3] / ".l9" / "sdk-compatibility.yaml"
)
RUNTIME_LOCKS = Path(__file__).resolve().parent / "locks"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
LOCK_REQUIREMENTS_SHA256 = re.compile(r"^# \(sha256:([0-9a-f]{64})\) ")


class ProvisioningError(RuntimeError):
    pass


def _scalar(value: str, *, line_number: int) -> str | bool:
    """Parse the deliberately small scalar subset used by the Core manifest.

    Provisioning starts before the isolated runtime exists, so compatibility
    policy must be readable with the Python standard library alone. Supporting
    only plain/quoted strings and booleans also makes unsupported YAML features
    fail closed instead of acquiring an ambient parser from the network.
    """
    value = value.strip()
    if not value:
        raise ProvisioningError(
            f"empty scalar in compatibility manifest line {line_number}"
        )
    if value in {"true", "false"}:
        return value == "true"
    if value[0] in {'"', "'"}:
        if len(value) < 2 or value[-1] != value[0]:
            raise ProvisioningError(
                f"unterminated quoted scalar in compatibility manifest line {line_number}"
            )
        return value[1:-1]
    if any(token in value for token in ("{", "}", "[", "]", "&", "*", "!")):
        raise ProvisioningError(
            f"unsupported YAML scalar in compatibility manifest line {line_number}"
        )
    return value


def _key_value(content: str, *, line_number: int) -> tuple[str, str]:
    key, separator, value = content.partition(":")
    if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", key):
        raise ProvisioningError(
            f"invalid compatibility manifest mapping in line {line_number}"
        )
    return key, value.strip()


def _parse_manifest(text: str) -> dict:
    """Parse the fixed compatibility-manifest shape without third-party code."""
    document: dict = {}
    section: str | None = None
    current_entry: dict | None = None
    current_list: list[str] | None = None
    seen_lines = 0
    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip() or line.strip() == "---":
            continue
        seen_lines += 1
        if "\t" in line:
            raise ProvisioningError(
                f"tabs are forbidden in compatibility manifest line {line_number}"
            )
        indent = len(line) - len(line.lstrip(" "))
        content = line[indent:]
        if indent == 0:
            key, value = _key_value(content, line_number=line_number)
            current_entry = None
            current_list = None
            if value:
                document[key] = _scalar(value, line_number=line_number)
                section = None
            else:
                if key == "supported":
                    document[key] = []
                else:
                    document[key] = {}
                section = key
            continue
        if section in {"metadata", "default", "policy"} and indent == 2:
            key, value = _key_value(content, line_number=line_number)
            mapping = document[section]
            if key in mapping:
                raise ProvisioningError(f"duplicate compatibility manifest key {key!r}")
            mapping[key] = _scalar(value, line_number=line_number)
            continue
        if section == "supported" and indent == 2 and content.startswith("- "):
            key, value = _key_value(content[2:], line_number=line_number)
            current_entry = {key: _scalar(value, line_number=line_number)}
            document[section].append(current_entry)
            current_list = None
            continue
        if section == "supported" and indent == 4 and current_entry is not None:
            key, value = _key_value(content, line_number=line_number)
            if key in current_entry:
                raise ProvisioningError(f"duplicate SDK compatibility key {key!r}")
            if value:
                current_entry[key] = _scalar(value, line_number=line_number)
                current_list = None
            else:
                current_list = []
                current_entry[key] = current_list
            continue
        if section == "supported" and indent == 6 and content.startswith("- "):
            if current_list is None:
                raise ProvisioningError(
                    f"orphan compatibility list item in line {line_number}"
                )
            item = _scalar(content[2:], line_number=line_number)
            if not isinstance(item, str):
                raise ProvisioningError(
                    f"compatibility list item must be a string in line {line_number}"
                )
            current_list.append(item)
            continue
        raise ProvisioningError(
            f"unsupported compatibility manifest structure in line {line_number}"
        )
    if not seen_lines:
        raise ProvisioningError("SDK compatibility manifest is empty")
    return document


def _load_manifest(manifest_path: Path) -> dict:
    """Parse the compatibility manifest. It is the single source of truth for the
    allowlist, so fail closed if it is missing or malformed."""
    if not manifest_path.is_file():
        raise ProvisioningError(
            f"SDK compatibility manifest not found: {manifest_path}"
        )
    try:
        data = _parse_manifest(manifest_path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as error:
        raise ProvisioningError(
            f"SDK compatibility manifest is not UTF-8: {error}"
        ) from error
    if data.get("schema") != "l9.sdk-compatibility/v1":
        raise ProvisioningError("unexpected SDK compatibility manifest schema")
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
    requirements_sha256 = entry.get("requirements_sha256")
    if not isinstance(requirements_sha256, str) or not SHA256.fullmatch(
        requirements_sha256
    ):
        raise ProvisioningError(
            f"compatibility entry for {revision!r} omits requirements_sha256"
        )
    runtime_locks = entry.get("runtime_locks")
    if (
        not isinstance(runtime_locks, list)
        or not runtime_locks
        or not all(
            isinstance(lock, str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.txt", lock)
            for lock in runtime_locks
        )
    ):
        raise ProvisioningError(
            f"compatibility entry for {revision!r} omits runtime_locks"
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
        raise ProvisioningError(f"SDK checkout already exists: {checkout}")
    checkout.mkdir()
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
    expected_contract = entry["integration_contract"].strip()
    declared: str | None = None
    executable: str | None = None
    in_cli = False
    for raw in contract.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip() or line.strip() == "---":
            continue
        indent = len(line) - len(line.lstrip(" "))
        content = line.strip()
        if indent == 0 and content.startswith("schema:"):
            declared = content.partition(":")[2].strip()
        elif indent == 0:
            in_cli = content == "CLI:"
        elif in_cli and indent == 2 and content.startswith("executable:"):
            executable = content.partition(":")[2].strip()
    if declared != expected_contract:
        raise ProvisioningError(
            "SDK integration contract schema "
            f"{declared!r} does not match the compatibility entry "
            f"{expected_contract!r}"
        )
    if executable != "l9-ci":
        raise ProvisioningError(
            f"SDK integration contract declares executable {executable!r}, "
            "expected 'l9-ci'"
        )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_requirements_file(checkout: Path, expected_sha256: str) -> None:
    """Bind the selected checkout's dependency declaration to Core policy."""
    requirements = checkout / "requirements.txt"
    if not requirements.is_file():
        raise ProvisioningError("SDK is missing requirements.txt")
    actual = sha256_file(requirements)
    if actual != expected_sha256:
        raise ProvisioningError(
            "SDK requirements.txt digest "
            f"{actual!r} does not match the compatibility entry {expected_sha256!r}"
        )


def runtime_platform() -> str:
    implementation = sys.implementation.name
    python = ".".join(str(part) for part in sys.version_info[:3])
    system = platform.system().lower()
    machine = platform.machine().lower()
    if (implementation, python, system, machine) != (
        "cpython",
        "3.12.14",
        "linux",
        "x86_64",
    ):
        raise ProvisioningError(
            "unsupported SDK runtime platform: "
            f"{implementation}-{python}-{system}-{machine}; "
            "Core ships a wheel lock only for cpython-3.12.14-linux-x86_64"
        )
    return "cpython-3.12.14-linux-x86_64"


def select_runtime_lock(entry: dict, lock_root: Path = RUNTIME_LOCKS) -> Path:
    name = f"{runtime_platform()}.txt"
    if name not in entry["runtime_locks"]:
        raise ProvisioningError(
            f"compatibility entry does not permit runtime lock {name!r}"
        )
    path = lock_root / name
    if not path.is_file() or path.is_symlink():
        raise ProvisioningError(f"SDK runtime lock not found: {path}")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        raise ProvisioningError(f"SDK runtime lock is unreadable: {path}") from error
    digests = [
        match.group(1)
        for line in lines
        if (match := LOCK_REQUIREMENTS_SHA256.match(line)) is not None
    ]
    if digests != [entry["requirements_sha256"]]:
        raise ProvisioningError(
            "SDK runtime lock requirements digest does not match the "
            "compatibility entry"
        )
    return path


def install_runtime_dependencies(venv_python: Path, lock: Path) -> None:
    """Install the complete dependency closure without executing source builds."""
    run(
        [
            str(venv_python),
            "-m",
            "pip",
            "install",
            "--quiet",
            "--disable-pip-version-check",
            "--only-binary",
            ":all:",
            "--require-hashes",
            "-r",
            str(lock),
        ]
    )


def register_sdk_source(venv: Path, checkout: Path) -> None:
    """Expose only the verified checkout through the isolated venv.

    The launcher uses Python isolated mode, which intentionally ignores
    ``PYTHONPATH`` and the caller's current directory. A data-only ``.pth`` file
    inside the action-owned venv is therefore the sole source-code edge.
    """
    source = str(checkout.resolve())
    if "\n" in source or "\r" in source:
        raise ProvisioningError("SDK checkout path must not contain line breaks")
    if os.name == "nt":
        site_packages = venv / "Lib" / "site-packages"
    else:
        version = f"python{sys.version_info.major}.{sys.version_info.minor}"
        site_packages = venv / "lib" / version / "site-packages"
    try:
        site_packages.mkdir(parents=True, exist_ok=True)
        (site_packages / "l9-ci-sdk-source.pth").write_text(
            f"{source}\n", encoding="utf-8"
        )
    except OSError as error:
        raise ProvisioningError(
            f"could not register the verified SDK source in the runtime: {error}"
        ) from error


def create_runtime(checkout: Path, runtime: Path, lock: Path) -> Path:
    venv = runtime / "venv"
    run([sys.executable, "-m", "venv", str(venv)])
    venv_python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    install_runtime_dependencies(venv_python, lock)
    register_sdk_source(venv, checkout)
    # Core installs provider executables separately. Do not install or prioritize
    # an SDK-local Semgrep: the generated shim inherits the caller's PATH so
    # `l9-ci semgrep run` resolves the one hash-locked provider selected by Core.
    # Isolated mode ignores the caller's current directory, PYTHONPATH, user site,
    # and Python environment variables while retaining the venv's trusted .pth.
    if os.name == "nt":
        scripts = venv / "Scripts"
        python = scripts / "python.exe"
        executable = runtime / "l9-ci.cmd"
        executable.write_text(
            "@echo off\r\n"
            'set "PYTHONPATH="\r\n'
            'set "PYTHONHOME="\r\n'
            f'"{python}" -I -m l9_ci %*\r\n',
            encoding="utf-8",
        )
    else:
        bin_directory = venv / "bin"
        python = bin_directory / "python"
        executable = runtime / "bin" / "l9-ci"
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "unset PYTHONPATH PYTHONHOME\n"
            f'exec "{python}" -I -m l9_ci "$@"\n',
            encoding="utf-8",
        )
        executable.chmod(0o755)
    return executable.resolve()


def resolve_runtime_directory(workspace: Path, runtime_input: str) -> Path:
    """Resolve the sole action-owned runtime without following workspace links.

    ``runtime-directory`` remains as a compatibility input, but accepting an
    arbitrary workspace-relative value would let a caller select unrelated
    content. Provisioning is create-only, so even this fixed location must not
    exist and none of its path components may be a symlink.
    """
    requested = Path(runtime_input)
    if requested.is_absolute() or requested != RUNTIME_DIRECTORY:
        raise ProvisioningError(
            f"runtime-directory must be exactly {RUNTIME_DIRECTORY.as_posix()}"
        )
    runtime = workspace / RUNTIME_DIRECTORY
    current = workspace
    for part in RUNTIME_DIRECTORY.parts:
        current = current / part
        if current.is_symlink():
            raise ProvisioningError(
                f"runtime-directory must not traverse a symlink: {current}"
            )
    if runtime.exists():
        raise ProvisioningError(
            f"runtime-directory already exists; provisioning is create-only: {runtime}"
        )
    return runtime


def create_runtime_directory(runtime: Path) -> None:
    """Create a new runtime tree without deleting or replacing existing paths."""
    try:
        runtime.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise ProvisioningError(
            f"runtime-directory already exists; provisioning is create-only: {runtime}"
        ) from error


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
            RUNTIME_DIRECTORY.as_posix(),
        )
        validate_inputs(source, repository, revision)
        entry = select_manifest_entry(revision)
        contract = entry["integration_contract"].strip()
        required_cli_paths = [path.strip() for path in entry["required_cli_paths"]]
        workspace = Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd())).resolve()
        runtime = resolve_runtime_directory(workspace, runtime_input)
        create_runtime_directory(runtime)
        checkout = runtime / "source"
        checkout_sdk(repository, revision, checkout)
        verify_contract_file(checkout, entry)
        verify_requirements_file(checkout, entry["requirements_sha256"])
        lock = select_runtime_lock(entry)
        executable = create_runtime(checkout, runtime, lock)
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
