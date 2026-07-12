#!/usr/bin/env python3
"""Bootstrap gate: workflow/download-integrity"""
from __future__ import annotations
import argparse, json, re, sys
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
_SHA256_TOKEN      = re.compile(r"\bSHA256\b", re.IGNORECASE)
# Structural PowerShell verification: Get-FileHash must name an explicit -Path
# target and select the SHA256 algorithm. Backtick line continuations mean the
# arguments may be spread across lines, so we normalize continuations before
# matching. This rejects a bare `Get-FileHash $x` (default algorithm) or a
# Get-FileHash with no -Path binding.
_GET_FILEHASH_STRUCTURAL = re.compile(
    r"Get-FileHash\b(?=(?:[^\n]|`\s*\n)*?-Path\s+\S)(?=(?:[^\n]|`\s*\n)*?-Algorithm\s+SHA256\b)",
    re.IGNORECASE,
)
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


def _has_get_filehash_sha256(run_block: str) -> bool:
    """True if a *structurally correct* PowerShell Get-FileHash verification is
    present: an explicit ``-Path <target>`` and ``-Algorithm SHA256``.

    Backtick continuations are normalized to spaces first so the arguments can
    span lines. Presence of the bare tokens is insufficient; the structure must
    bind a target path and the SHA256 algorithm.
    """
    normalized = re.sub(r"`\s*\n\s*", " ", run_block)
    return bool(re.search(
        r"Get-FileHash\b(?=[^\n]*?-Path\s+\S)(?=[^\n]*?-Algorithm\s+SHA256\b)",
        normalized, re.IGNORECASE))


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


# --- Checksum target identity (HIGH-04) -------------------------------------
_DL_OUTPUT_TARGET = re.compile(r'(?:--output|-o|-O|-OutFile)\s+"?([^"\s]+)"?', re.IGNORECASE)
_POSIX_CHECK_TARGET = re.compile(r'sha(?:256|512)sum\b[^\n]*?(["\$][^"\s]*|/[^"\s]+)', re.IGNORECASE)
_UNPACK_TARGET = re.compile(
    r'(?:tar\b[^\n]*?-[a-zA-Z]*f\s+"?([^"\s]+)"?'
    r'|unzip\s+"?([^"\s]+)"?'
    r'|Expand-Archive\b[^\n]*?-Path\s+"?([^"\s]+)"?)', re.IGNORECASE)


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


def _checksum_target_identity(run_block: str):
    """Return (ok, detail). ``ok`` is False when the download destination, the
    checksum target, and any unpack target are not the same path.

    Only enforced when a download output target can be identified. Returns
    (True, "") when there is nothing to compare (e.g. checksum via heredoc that
    names the same var as the output, or no unpack step).
    """
    out_m = _DL_OUTPUT_TARGET.search(run_block)
    if not out_m:
        return True, ""
    dest = _norm_target(out_m.group(1))
    targets = {dest}
    # Unpack target (if any) must match the download destination.
    for um in _UNPACK_TARGET.finditer(run_block):
        unpacked = _norm_target(next(g for g in um.groups() if g))
        if unpacked and unpacked != dest:
            return False, f"unpack target {unpacked!r} != download dest {dest!r}"
    # POSIX checksum target: the file argument fed to sha256sum. When a heredoc
    # form is used ("<<< \"$SHA  $DEST\"") the dest appears verbatim in the block.
    if _EXEC_CHK.search(run_block) and dest not in _norm_target(run_block):
        return False, f"checksum does not reference download dest {dest!r}"
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
            # Verification must be present (sha256sum on POSIX, Get-FileHash on
            # PowerShell) ...
            has_verify = bool(_EXEC_CHK.search(run_block) or _has_get_filehash_sha256(run_block))
            if not has_verify:
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
        try: result.finalize()
        except ValueError: pass
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
