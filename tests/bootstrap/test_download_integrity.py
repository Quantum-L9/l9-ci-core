from __future__ import annotations
import json, shutil
from pathlib import Path
import pytest
import validate_download_integrity as vdi
FIXTURES = Path(__file__).parent.parent / "fixtures" / "download-integrity"
REGISTRY = '''schema_version: "1.0"
downloads:
  gitleaks-linux-x64:
    version: "8.18.4"
    url: "https://github.com/gitleaks/gitleaks/releases/download/v8.18.4/gitleaks_8.18.4_linux_x64.tar.gz"
    sha256: "a1b2c3d4e5f60718293a4b5c6d7e8f901234567890abcdef1234567890abcdef"
    workflows:
      - ".github/workflows/valid-curl-sha256.yml"
'''

def _run(fixture, tmp_path, registry=REGISTRY):
    wdir = tmp_path / ".github" / "workflows"
    wdir.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / fixture, wdir / fixture)
    gov = tmp_path / ".github" / "governance"
    gov.mkdir(parents=True, exist_ok=True)
    (gov / "download-integrity.yaml").write_text(registry)
    out = tmp_path / "result.json"
    ec = vdi.run(tmp_path, gov / "download-integrity.yaml", out, "text", True)
    return ec, json.loads(out.read_text())

def test_valid_curl_passes(tmp_path):
    ec, d = _run("valid-curl-sha256.yml", tmp_path)
    assert ec == 0 and d["result"] == "passed"

def test_pipe_bash_forbidden(tmp_path):
    ec, d = _run("invalid-curl-pipe-bash.yml", tmp_path)
    assert ec == 1 and any(v["code"] == "STREAMED_EXECUTION_FORBIDDEN" for v in d["violations"])

def test_wget_pipe_shell_forbidden(tmp_path):
    ec, d = _run("invalid-wget-pipe-shell.yml", tmp_path)
    assert ec == 1 and any(v["code"] == "STREAMED_EXECUTION_FORBIDDEN" for v in d["violations"])

def test_missing_marker_fails(tmp_path):
    ec, d = _run("invalid-missing-marker.yml", tmp_path)
    assert ec == 1 and any(v["code"] == "MISSING_DOWNLOAD_MARKER" for v in d["violations"])

def test_missing_registry_key_fails(tmp_path):
    ec, d = _run("invalid-missing-registry.yml", tmp_path)
    assert ec == 1 and any(v["code"] == "UNREGISTERED_DOWNLOAD" for v in d["violations"])

def test_mutable_latest_url_fails(tmp_path):
    ec, d = _run("invalid-mutable-latest-url.yml", tmp_path)
    assert ec == 1 and any(v["code"] == "MUTABLE_LATEST_URL" for v in d["violations"])


def test_valid_powershell_passes(tmp_path):
    ec, d = _run("valid-powershell-sha256.yml", tmp_path,
                 registry=REGISTRY.replace("gitleaks-linux-x64", "gitleaks-windows-x64")
                 .replace('_linux_x64.tar.gz', '_windows_x64.zip')
                 .replace("valid-curl-sha256.yml", "valid-powershell-sha256.yml"))
    assert ec == 0 and d["result"] == "passed"


def _inline_step(body, tmp_path, registry=REGISTRY):
    wdir = tmp_path / ".github" / "workflows"
    wdir.mkdir(parents=True, exist_ok=True)
    (wdir / "dl.yml").write_text(body)
    gov = tmp_path / ".github" / "governance"
    gov.mkdir(parents=True, exist_ok=True)
    (gov / "download-integrity.yaml").write_text(registry)
    out = tmp_path / "result.json"
    ec = vdi.run(tmp_path, gov / "download-integrity.yaml", out, "text", True)
    return ec, json.loads(out.read_text())


_URL = "https://github.com/gitleaks/gitleaks/releases/download/v8.18.4/gitleaks_8.18.4_linux_x64.tar.gz"
_SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f901234567890abcdef1234567890abcdef"


def test_missing_url_binding_fails(tmp_path):
    body = (
        "on: push\njobs:\n  d:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - run: |\n          set -euo pipefail\n"
        "          # l9-download: gitleaks-linux-x64\n"
        f'          TOOL_SHA256="{_SHA}"\n'
        '          curl --fail -L "$TOOL_URL" --output "$RUNNER_TEMP/gl.tar.gz"\n'
        '          sha256sum --check --strict <<< "$TOOL_SHA256  $RUNNER_TEMP/gl.tar.gz"\n'
    )
    ec, d = _inline_step(body, tmp_path)
    assert ec == 1 and any(v["code"] == "DOWNLOAD_URL_BINDING_MISSING" for v in d["violations"])


def test_missing_digest_binding_fails(tmp_path):
    body = (
        "on: push\njobs:\n  d:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - run: |\n          set -euo pipefail\n"
        "          # l9-download: gitleaks-linux-x64\n"
        f'          TOOL_URL="{_URL}"\n'
        '          curl --fail -L "$TOOL_URL" --output "$RUNNER_TEMP/gl.tar.gz"\n'
        '          sha256sum --check --strict <<< "$TOOL_SHA256  $RUNNER_TEMP/gl.tar.gz"\n'
    )
    ec, d = _inline_step(body, tmp_path)
    assert ec == 1 and any(v["code"] == "DOWNLOAD_DIGEST_BINDING_MISSING" for v in d["violations"])


def test_checksum_target_mismatch_fails(tmp_path):
    # Downloads to gl.tar.gz but unpacks a different file -> identity violation.
    body = (
        "on: push\njobs:\n  d:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - run: |\n          set -euo pipefail\n"
        "          # l9-download: gitleaks-linux-x64\n"
        f'          TOOL_URL="{_URL}"\n'
        f'          TOOL_SHA256="{_SHA}"\n'
        '          curl --fail -L "$TOOL_URL" --output "$RUNNER_TEMP/gl.tar.gz"\n'
        '          sha256sum --check --strict <<< "$TOOL_SHA256  $RUNNER_TEMP/gl.tar.gz"\n'
        '          tar -xzf "$RUNNER_TEMP/other.tar.gz" -C "$RUNNER_TEMP"\n'
    )
    ec, d = _inline_step(body, tmp_path)
    assert ec == 1 and any(v["code"] == "CHECKSUM_TARGET_MISMATCH" for v in d["violations"])


def test_powershell_nonstructural_filehash_fails(tmp_path):
    # Get-FileHash without -Path/-Algorithm SHA256 is not accepted as verify.
    body = (
        "on: push\njobs:\n  d:\n    runs-on: windows-latest\n    steps:\n"
        "      - shell: pwsh\n        run: |\n"
        "          # l9-download: gitleaks-linux-x64\n"
        f'          $ToolUrl = "{_URL}"\n'
        f'          $ToolSha256 = "{_SHA}"\n'
        '          $Archive = Join-Path $env:RUNNER_TEMP "gl.zip"\n'
        '          Invoke-WebRequest -Uri $ToolUrl -OutFile $Archive -UseBasicParsing\n'
        '          $Actual = (Get-FileHash $Archive).Hash\n'
    )
    ec, d = _inline_step(body, tmp_path)
    assert ec == 1 and any(v["code"] == "DOWNLOAD_CHECKSUM_MISSING" for v in d["violations"])


def test_schema_unavailable_fails_closed(tmp_path, monkeypatch):
    import l9_bootstrap.schema_loader as sl
    def _boom(root, name):
        raise sl.SchemaUnavailable("missing")
    monkeypatch.setattr(sl, "load_validator", _boom)
    ec, d = _run("valid-curl-sha256.yml", tmp_path)
    assert ec == 2 and any(v["code"] == "SCHEMA_UNAVAILABLE" for v in d["violations"])
