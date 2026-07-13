#!/usr/bin/env python3
"""Bootstrap gate: workflow/download-integrity"""
from __future__ import annotations
import argparse, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from l9_bootstrap.models import GateResult, ResultStatus
from l9_bootstrap.output import write_json, write_json_stdout
from l9_bootstrap.paths import repo_root
from l9_bootstrap.workflow_scan import iter_run_blocks, iter_workflow_files
from l9_bootstrap.yaml_loader import load_yaml_file
from l9_bootstrap import schema_loader
from l9_bootstrap.limits import MAX_REGISTRY_ENTRIES

GATE_ID = "workflow/download-integrity"
_MARKER_RE   = re.compile(r"#\s*l9-download:\s*(\S+)")
_CURL_RE      = re.compile(r"\bcurl\b")
_WGET_RE      = re.compile(r"\bwget\b")
# PowerShell web download primitives.
_PWSH_FETCH   = re.compile(r"\b(?:Invoke-WebRequest|iwr|Invoke-RestMethod|irm|Start-BitsTransfer)\b")
# Inline interpreter-based downloads we refuse to reason about.
_PY_FETCH     = re.compile(r"\b(?:urllib\.request|urlretrieve|urlopen|requests\.get|httpx\.get)\b")
_PIPE_EXEC    = re.compile(r"\|\s*(bash|sh|zsh)\b")
_EXEC_CHK     = re.compile(r"\bsha(?:256|512)sum\b")
# PowerShell verification primitive (may span continuation lines, so matched
# per-line for ordering and block-wide for presence).
_GET_FILEHASH_LINE = re.compile(r"\bGet-FileHash\b", re.IGNORECASE)
_SHA_RE       = re.compile(r"^[0-9a-fA-F]{64}$")
_MUTABLE_URL  = re.compile(r"https://[^/]+/[^/]+/[^/]+/releases/latest/download/")
# Quoted assignments of the download URL / expected digest, POSIX and PowerShell.
_URL_ASSIGN   = re.compile(r'(?:TOOL_URL|ToolUrl)\s*=\s*"([^"]+)"')
_SHA_ASSIGN   = re.compile(r'(?:TOOL_SHA256|ToolSha256)\s*=\s*"([^"]+)"')
# A download command line (never counted as "execution of the artifact").
_DOWNLOAD_CMD = re.compile(r"\bcurl\b|\bwget\b|Invoke-WebRequest|iwr\b|Invoke-RestMethod|irm\b|Start-BitsTransfer|--output\b|-OutFile\b")
# Unpacking the artifact. Anchored as a command token (line start or after a
# shell separator) so that filenames like "tool.tar.gz" do not match.
_UNPACK       = re.compile(r"(?:^|[;&|]|\s)(?:tar|unzip|Expand-Archive)\b", re.IGNORECASE)
# A variable assignment line (name=...), never an execution.
_ASSIGNMENT   = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=|^\$[A-Za-z_]')
# Executing the downloaded artifact directly, e.g. "$RUNNER_TEMP/tool" version.
# The quoted path must be followed by a real argument token (not a shell line
# continuation), so that printf/echo argument lines feeding a checksum are not
# misread as execution.
_RUN_ARTIFACT = re.compile(r'"\$RUNNER_TEMP/[^"]+"\s+(?!\\\s*$)[^\s\\]')
# Lines that merely marshal data for verification, never execution.
_DATA_MARSHAL = re.compile(r"\bprintf\b|\becho\b")


# PowerShell verification structure. Continuations (backtick + newline) are
# normalized to spaces before matching so arguments may span lines.
_PWSH_GETFILEHASH = re.compile(
    r"Get-FileHash\b(?=[^\n]*?-Path\s+(?P<path>\S+))(?=[^\n]*?-Algorithm\s+SHA256\b)",
    re.IGNORECASE)
# Capture of the computed hash: `.Hash` property is read (directly, or via an
# assignment such as `$computed = (Get-FileHash ...).Hash`).
_PWSH_HASH_CAPTURE = re.compile(r"\.Hash\b", re.IGNORECASE)
# Comparison of the computed hash against the expected ToolSha256, e.g.
# `if ($computed -ne $ToolSha256)` or `-eq $ToolSha256`.
_PWSH_HASH_COMPARE = re.compile(r"-(?:ne|eq)\b[^\n]*ToolSha256", re.IGNORECASE)
# A failure action on mismatch: throw, or exit with a nonzero code.
_PWSH_FAIL_ACTION = re.compile(r"\bthrow\b|\bexit\s+[1-9]", re.IGNORECASE)


def _powershell_verification(run_block: str):
    """Return (ok, detail) for a PowerShell download-verification block.

    A compliant PowerShell verification MUST:
      * call ``Get-FileHash -Path <target> -Algorithm SHA256`` where ``<target>``
        resolves to the download destination,
      * capture the ``.Hash`` property,
      * compare it against ``$ToolSha256``, and
      * ``throw`` / ``exit <nonzero>`` on mismatch.

    Any PowerShell fetch that does not present this exact structure fails closed
    (returns ``(False, detail)``) rather than being treated as verified.
    """
    normalized = re.sub(r"`\s*\n\s*", " ", run_block)
    gm = _PWSH_GETFILEHASH.search(normalized)
    if gm is None:
        return False, "no Get-FileHash -Path <dest> -Algorithm SHA256"
    # The hashed path must resolve to the download destination.
    out_m = _DL_OUTPUT_TARGET.search(normalized)
    if out_m is not None:
        assigns = _assignments(run_block)
        dest = _resolve_target(out_m.group(1), assigns)
        hashed = _resolve_target(gm.group("path"), assigns)
        if hashed != dest:
            return False, f"Get-FileHash target {hashed!r} != download dest {dest!r}"
    if not _PWSH_HASH_CAPTURE.search(normalized):
        return False, "Get-FileHash result .Hash is never captured"
    if not _PWSH_HASH_COMPARE.search(normalized):
        return False, "computed hash is not compared against $ToolSha256"
    if not _PWSH_FAIL_ACTION.search(normalized):
        return False, "no throw/exit on hash mismatch"
    return True, ""


def _has_get_filehash_sha256(run_block: str) -> bool:
    """Back-compat boolean wrapper around :func:`_powershell_verification`."""
    ok, _ = _powershell_verification(run_block)
    return ok


def _load_registry(path):
    try:
        raw = load_yaml_file(path)
    except Exception as exc:
        return None, str(exc)
    if not isinstance(raw, dict):
        return None, "registry root is not a mapping"
    sv = raw.get("schema_version")
    if sv != "1.0":
        return None, f"unsupported schema_version {sv!r}"
    downloads = raw.get("downloads", {})
    if not isinstance(downloads, dict):
        return None, "downloads field must be a mapping"
    return downloads, None


def _load_registry_with_schema(path, root):
    """Load the registry and validate it against its schema (fail closed).

    Returns (downloads, error). The download-integrity schema is a *required*
    dependency: if jsonschema or the schema file is unavailable the registry
    cannot be trusted, so this raises :class:`schema_loader.SchemaUnavailable`
    (converted by the caller to an error/exit 2) rather than degrading.
    """
    downloads, err = _load_registry(path)
    if err:
        return None, err
    # Fail closed: missing library or schema is fatal (raises SchemaUnavailable).
    validator = schema_loader.load_validator(root, "download-integrity")
    raw = load_yaml_file(path)
    errors = schema_loader.schema_errors(validator, raw)
    if errors:
        return None, f"registry schema invalid: {schema_loader.format_error(errors[0])}"
    return downloads, None


# --- Artifact target identity (HIGH-04) -------------------------------------
# Each of the three roles -- download destination, checksum-verified target,
# and extraction/execution target -- is parsed *separately* and then required
# to resolve to the same concrete artifact. Shell variable assignments are
# resolved so that `$Archive` referring to `gitleaks.tar.gz` compares equal to
# a literal `gitleaks.tar.gz`.
_DL_OUTPUT_TARGET = re.compile(r'(?:--output|-o|-O|-OutFile)\s+"?([^"\s]+)"?', re.IGNORECASE)
# POSIX sha256sum/sha512sum invoked with a file argument, e.g.
# `sha256sum --check --strict <<< "$SHA  gitleaks.tar.gz"` or
# `echo "$SHA  gitleaks.tar.gz" | sha256sum -c`.
# The expected-digest token may be a hex literal or a shell variable
# ($TOOL_SHA256); the file argument is the last whitespace-delimited token
# inside the quoted "<digest>  <file>" pair.
_POSIX_CHECK_HEREDOC = re.compile(
    r'(?:sha(?:256|512)sum\b[^\n]*?<<<\s*"(?:[0-9a-fA-F]{64}|\$[^\s"]+)\s+([^"\s]+)"'
    r'|"(?:[0-9a-fA-F]{64}|\$[^\s"]+)\s+([^"\s]+)"\s*\|\s*sha(?:256|512)sum\b)',
    re.IGNORECASE)
_UNPACK_TARGET = re.compile(
    r'(?:tar\b[^\n]*?-[a-zA-Z]*f\s+"?([^"\s]+)"?'
    r'|unzip\s+"?([^"\s]+)"?'
    r'|Expand-Archive\b[^\n]*?-Path\s+"?([^"\s]+)"?)', re.IGNORECASE)
# A simple shell/PowerShell variable assignment: NAME="value" or $Name = "value".
_VAR_ASSIGN = re.compile(
    r'^\s*\$?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"?([^"\n]+?)"?\s*$',
    re.MULTILINE)


def _norm_target(tok: str) -> str:
    """Normalize a shell path token for identity comparison.

    Strips surrounding quotes and shell variable syntax so that ``"$Archive"``,
    ``$Archive`` and ``${Archive}`` compare equal, and collapses the POSIX
    ``$RUNNER_TEMP`` / PowerShell ``$env:RUNNER_TEMP`` spellings.
    """
    if not tok:
        return ""
    t = tok.strip().strip('"').strip("'")
    t = t.replace("${", "$").replace("}", "")
    t = t.replace("$env:", "$")
    return t


def _assignments(run_block: str) -> dict:
    """Map assigned variable names (normalized, $-prefixed) to their values."""
    out = {}
    for m in _VAR_ASSIGN.finditer(run_block):
        out["$" + m.group(1)] = _norm_target(m.group(2))
    return out


def _resolve_target(tok: str, assigns: dict) -> str:
    """Resolve a normalized target through one level of variable assignment."""
    norm = _norm_target(tok)
    return assigns.get(norm, norm)


def _checksum_target_identity(run_block: str):
    """Return (ok, detail). ``ok`` is False when the parsed download
    destination, checksum target, and extraction target do not all resolve to
    the same artifact.

    Each role is parsed independently and then compared after variable
    resolution. If a download destination is present, a POSIX checksum target
    and an extraction target (when present) must resolve to it exactly.
    """
    out_m = _DL_OUTPUT_TARGET.search(run_block)
    if not out_m:
        return True, ""
    assigns = _assignments(run_block)
    dest = _resolve_target(out_m.group(1), assigns)

    # Extraction/execution target (if any) must equal the download destination.
    for um in _UNPACK_TARGET.finditer(run_block):
        unpacked = _resolve_target(next(g for g in um.groups() if g), assigns)
        if unpacked and unpacked != dest:
            return False, f"extraction target {unpacked!r} != download dest {dest!r}"

    # POSIX checksum target: the file argument bound to sha256sum/sha512sum.
    # When a checksum verification is present we require that its explicit file
    # argument resolves to the download destination -- verifying a different
    # file than the one downloaded is the exact gap CHECKSUM_TARGET_MISMATCH
    # guards against.
    if _EXEC_CHK.search(run_block):
        cm = _POSIX_CHECK_HEREDOC.search(run_block)
        if cm is None:
            return False, (
                "checksum verification present but no explicit file target "
                f"could be parsed to compare against download dest {dest!r}"
            )
        checked = _resolve_target(next(g for g in cm.groups() if g), assigns)
        if checked != dest:
            return False, f"checksum target {checked!r} != download dest {dest!r}"
    return True, ""


def _verified_too_late(run_block: str) -> bool:
    """True if the artifact is executed/unpacked before the checksum verify.

    We locate the first checksum-verification line and check whether any line
    that runs or unpacks the downloaded artifact appears before it. Download
    commands (curl/wget/Invoke-WebRequest, or any line writing an --output/
    -OutFile target) are never treated as execution.
    """
    lines = run_block.splitlines()
    verify_idx = None
    for i, line in enumerate(lines):
        if _EXEC_CHK.search(line) or _GET_FILEHASH_LINE.search(line):
            verify_idx = i
            break
    if verify_idx is None:
        return False
    for line in lines[:verify_idx]:
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # Never count a download command line as artifact execution.
        if _DOWNLOAD_CMD.search(line):
            continue
        # printf/echo lines only marshal the expected digest for checking.
        if _DATA_MARSHAL.search(line):
            continue
        # Variable assignments (including URL/digest) are not execution.
        if _ASSIGNMENT.match(stripped):
            continue
        if _UNPACK.search(line) or _RUN_ARTIFACT.search(line):
            return True
    return False


def run(root, registry_path, output_json, fmt, quiet):
    result = GateResult(gate_id=GATE_ID, result=ResultStatus.passed)
    if registry_path is None:
        registry_path = root / ".github" / "governance" / "download-integrity.yaml"
    try:
        registry, err = _load_registry_with_schema(registry_path, root)
    except schema_loader.SchemaUnavailable as exc:
        result.add_violation(code="SCHEMA_UNAVAILABLE", message=str(exc))
        result.result = ResultStatus.error
        return _emit(result, output_json, fmt, quiet, 2)
    if err:
        result.add_violation(code="REGISTRY_LOAD_ERROR", message=err)
        result.result = ResultStatus.error
        return _emit(result, output_json, fmt, quiet, 2)
    # Bound the registry size (resource-limit invariant).
    if len(registry) > MAX_REGISTRY_ENTRIES:
        result.add_violation(code="REGISTRY_TOO_LARGE",
            message=f"download registry has {len(registry)} entries (max {MAX_REGISTRY_ENTRIES}).")
        result.result = ResultStatus.error
        return _emit(result, output_json, fmt, quiet, 2)
    for key, entry in registry.items():
        sha = entry.get("sha256", "")
        if not _SHA_RE.fullmatch(str(sha)):
            result.add_violation(code="INVALID_REGISTRY_SHA256",
                message=f"Registry entry {key!r} has invalid sha256={sha!r}")
            result.result = ResultStatus.failed
    try:
        workflow_files = list(iter_workflow_files(root / ".github" / "workflows", root))
    except ValueError as exc:
        result.add_violation(code="RESOURCE_LIMIT", message=str(exc))
        result.result = ResultStatus.error
        return _emit(result, output_json, fmt, quiet, 2)
    for wf_path in workflow_files:
        rel = str(wf_path.relative_to(root))
        try:
            wf = load_yaml_file(wf_path)
        except Exception as exc:
            result.add_violation(code="WORKFLOW_PARSE_ERROR", message=f"{wf_path.name}: {exc}", path=rel)
            result.result = ResultStatus.error
            continue
        if not isinstance(wf, dict):
            continue
        for jid, idx, name, run_block, line_no in iter_run_blocks(wf):
            has_curl = bool(_CURL_RE.search(run_block))
            has_wget = bool(_WGET_RE.search(run_block))
            has_pwsh = bool(_PWSH_FETCH.search(run_block))
            has_py   = bool(_PY_FETCH.search(run_block))
            if not (has_curl or has_wget or has_pwsh or has_py):
                continue
            # Inline interpreter downloads (e.g. python urlretrieve) cannot be
            # statically reasoned about and are refused outright.
            if has_py:
                result.add_violation(code="UNSUPPORTED_DOWNLOAD_PATTERN",
                    message=f"{wf_path.name}: job={jid} step={name!r}: inline interpreter download is not a supported pattern.",
                    path=rel, line=line_no or None)
                result.result = ResultStatus.failed
                continue
            if _PIPE_EXEC.search(run_block):
                result.add_violation(code="STREAMED_EXECUTION_FORBIDDEN",
                    message=f"{wf_path.name}: job={jid} step={name!r}: streaming to shell forbidden.",
                    path=rel, line=line_no or None)
                result.result = ResultStatus.failed
                continue
            m = _MARKER_RE.search(run_block)
            if not m:
                result.add_violation(code="MISSING_DOWNLOAD_MARKER",
                    message=f"{wf_path.name}: job={jid} step={name!r}: missing # l9-download: <key> marker.",
                    path=rel, line=line_no or None)
                result.result = ResultStatus.failed
                continue
            key = m.group(1)
            if key not in registry:
                result.add_violation(code="UNREGISTERED_DOWNLOAD",
                    message=f"{wf_path.name}: job={jid} step={name!r}: key={key!r} not registered.",
                    path=rel, line=line_no or None)
                result.result = ResultStatus.failed
                continue
            entry = registry.get(key, {})
            if _MUTABLE_URL.search(run_block):
                result.add_violation(code="MUTABLE_LATEST_URL",
                    message=f"{wf_path.name}: job={jid} step={name!r}: /releases/latest/ URL forbidden.",
                    path=rel, line=line_no or None)
                result.result = ResultStatus.failed
            # Mandatory binding: a download step must bind BOTH the URL and the
            # expected digest inline (TOOL_URL/ToolUrl and TOOL_SHA256/ToolSha256
            # assignments). A step that only references undeclared env variables
            # provides no static provenance and is rejected.
            url_m = _URL_ASSIGN.search(run_block)
            sha_m = _SHA_ASSIGN.search(run_block)
            if url_m is None:
                result.add_violation(code="DOWNLOAD_URL_BINDING_MISSING",
                    message=f"{wf_path.name}: job={jid} step={name!r}: no inline TOOL_URL/ToolUrl binding for key={key!r}.",
                    path=rel, line=line_no or None)
                result.result = ResultStatus.failed
            if sha_m is None:
                result.add_violation(code="DOWNLOAD_DIGEST_BINDING_MISSING",
                    message=f"{wf_path.name}: job={jid} step={name!r}: no inline TOOL_SHA256/ToolSha256 binding for key={key!r}.",
                    path=rel, line=line_no or None)
                result.result = ResultStatus.failed
            # Inline URL / digest, if present, must agree with the registry.
            if url_m and entry.get("url") and url_m.group(1) != entry.get("url"):
                result.add_violation(code="DOWNLOAD_URL_MISMATCH",
                    message=f"{wf_path.name}: job={jid} step={name!r}: URL {url_m.group(1)!r} != registry {entry.get('url')!r}.",
                    path=rel, line=line_no or None)
                result.result = ResultStatus.failed
            if sha_m:
                inline_sha = sha_m.group(1)
                if not _SHA_RE.fullmatch(inline_sha):
                    result.add_violation(code="DOWNLOAD_DIGEST_MISMATCH",
                        message=f"{wf_path.name}: job={jid} step={name!r}: inline digest {inline_sha!r} is not a valid sha256.",
                        path=rel, line=line_no or None)
                    result.result = ResultStatus.failed
                elif entry.get("sha256") and inline_sha.lower() != str(entry.get("sha256")).lower():
                    result.add_violation(code="DOWNLOAD_DIGEST_MISMATCH",
                        message=f"{wf_path.name}: job={jid} step={name!r}: digest {inline_sha!r} != registry.",
                        path=rel, line=line_no or None)
                    result.result = ResultStatus.failed
            # Verification must be present (sha256sum on POSIX, a structurally
            # complete Get-FileHash comparison on PowerShell). Unsupported or
            # incomplete PowerShell verification forms fail closed with detail.
            posix_verify = bool(_EXEC_CHK.search(run_block))
            pwsh_ok, pwsh_detail = _powershell_verification(run_block)
            has_verify = posix_verify or pwsh_ok
            if not has_verify:
                # If this is a PowerShell fetch, surface *why* its verification
                # was rejected rather than a generic "missing" message.
                if has_pwsh and not posix_verify:
                    detail = pwsh_detail or "unsupported PowerShell verification form"
                    result.add_violation(code="DOWNLOAD_CHECKSUM_MISSING",
                        message=f"{wf_path.name}: job={jid} step={name!r}: PowerShell verification rejected: {detail}.",
                        path=rel, line=line_no or None)
                else:
                    result.add_violation(code="DOWNLOAD_CHECKSUM_MISSING",
                        message=f"{wf_path.name}: job={jid} step={name!r}: no sha256 verification (sha256sum/Get-FileHash).",
                        path=rel, line=line_no or None)
                result.result = ResultStatus.failed
            # ... and must occur before the artifact is executed or unpacked.
            elif _verified_too_late(run_block):
                result.add_violation(code="DOWNLOAD_VERIFIED_TOO_LATE",
                    message=f"{wf_path.name}: job={jid} step={name!r}: artifact executed/unpacked before checksum verification.",
                    path=rel, line=line_no or None)
                result.result = ResultStatus.failed
            # The downloaded file, the checksum-verified file, and any unpacked
            # file must be the same artifact (no verify-one / use-another gap).
            ok, detail = _checksum_target_identity(run_block)
            if not ok:
                result.add_violation(code="CHECKSUM_TARGET_MISMATCH",
                    message=f"{wf_path.name}: job={jid} step={name!r}: {detail}.",
                    path=rel, line=line_no or None)
                result.result = ResultStatus.failed
    result.metadata = {"files_scanned": len(workflow_files), "registry_entries": len(registry)}
    if result.result == ResultStatus.passed:
        result.finalize(); exit_code = 0
    else:
        try:
            result.finalize()
        except ValueError:
            # finalize() may raise when the result carries no recorded findings
            # yet is not in the passed state; the exit code is derived directly
            # from result.result below, so this is intentionally ignored.
            pass
        exit_code = 1 if result.result == ResultStatus.failed else 2
    return _emit(result, output_json, fmt, quiet, exit_code)


def _emit(result, output_json, fmt, quiet, exit_code):
    data = result.to_dict()
    if output_json:
        write_json(data, output_json)
    if fmt == "json":
        write_json_stdout(data)
    elif not quiet or exit_code != 0:
        print(f"[{result.result.value.upper()}] {GATE_ID}")
        for v in result.violations:
            print(f"  VIOLATION {v.code}: {v.message}")
    return exit_code


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--root", default=None)
    p.add_argument("--registry", default=None)
    p.add_argument("--output-json", default=None)
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)
    try:
        _root = repo_root(args.root)
        _reg  = Path(args.registry) if args.registry else None
        return run(_root, _reg, Path(args.output_json) if args.output_json else None, args.format, args.quiet)
    except Exception as exc:
        r = GateResult(gate_id=GATE_ID, result=ResultStatus.error)
        r.add_violation(code="EXECUTION_ERROR", message=str(exc))
        data = r.to_dict()
        if args.output_json:
            write_json(data, Path(args.output_json))
        if args.format == "json":
            write_json_stdout(data)
        else:
            print(f"[ERROR] {GATE_ID}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
