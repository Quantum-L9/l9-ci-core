"""Shared fixtures for the release engine suite.

``FakeGitHub`` replaces the Releases REST API at the engine's transport
boundary (``tools/l9_release/github.py`` ``Transport``); nothing else is
stubbed. Git runs for real against a bare-repository ``origin``. ``UV_SHIM``
stands in for the ``uv`` executable, which GitHub-hosted runners do not ship:
it emits a deliberately non-reproducible sdist envelope (wall-clock mtimes, a
random uid, a gzip header carrying a filename and the current time), exactly
the defect setuptools exhibits, plus a reproducible wheel, so the engine's
normalisation and two-round proof are exercised hermetically.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import urllib.parse
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from l9_release.github import Response  # noqa: E402

API = "https://api.github.test"
UPLOADS = "https://uploads.github.test"
REPOSITORY = "Quantum-L9/release-fixture"

UV_SHIM = textwrap.dedent(
    '''\
    #!/usr/bin/env python3
    """Test stand-in for `uv build --sdist --wheel --out-dir DIR`."""
    import gzip, io, os, pathlib, random, sys, tarfile, time, tomllib, zipfile

    argv = sys.argv[1:]
    assert argv[:3] == ["build", "--sdist", "--wheel"], argv
    assert argv[3] == "--out-dir", argv
    out = pathlib.Path(argv[4])
    assert os.environ["SOURCE_DATE_EPOCH"].isdigit()
    project = tomllib.loads(pathlib.Path("pyproject.toml").read_text())["project"]
    name, version = project["name"].replace("-", "_"), project["version"]
    base = f"{name}-{version}"
    sources = sorted(p for p in pathlib.Path(".").rglob("*")
                     if p.is_file() and ".git" not in p.parts)
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for path in reversed(sources):  # deliberately unsorted
            info = tar.gettarinfo(str(path), arcname=f"{base}/{path.as_posix()}")
            info.mtime = time.time() + random.random() * 1000
            info.uid = random.randint(1, 60000)
            info.uname = "builder"
            with open(path, "rb") as stream:
                tar.addfile(info, stream)
    with open(out / f"{base}.tar.gz", "wb") as target:
        with gzip.GzipFile(filename=f"{base}.tar", mode="wb", fileobj=target,
                           mtime=int(time.time()) + random.randint(1, 99)) as gz:
            gz.write(raw.getvalue())
    with zipfile.ZipFile(out / f"{base}-py3-none-any.whl", "w") as wheel:
        for path in sources:
            if path.suffix == ".py":
                entry = zipfile.ZipInfo(path.as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
                wheel.writestr(entry, path.read_bytes())
    (out / ".gitignore").write_text("*\\n")
    '''
)


def run(cwd: pathlib.Path, *command: str) -> str:
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if completed.returncode != 0:
        raise AssertionError(f"{command} failed: {completed.stderr}{completed.stdout}")
    return completed.stdout


def git(cwd: pathlib.Path, *arguments: str) -> str:
    return run(
        cwd,
        "git",
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.test",
        "-c",
        "commit.gpgsign=false",
        "-c",
        "tag.gpgsign=false",
        *arguments,
    )


class FakeGitHub:
    """In-memory GitHub Releases API behind the engine's Transport protocol."""

    def __init__(self, repository: str = REPOSITORY) -> None:
        self.repository = repository
        self.releases: dict[str, dict[str, Any]] = {}
        self.blobs: dict[int, bytes] = {}
        self.calls: list[tuple[str, str]] = []
        self.fail_upload_after: int | None = None
        self._uploads = 0
        self._next = 100

    def _id(self) -> int:
        self._next += 1
        return self._next

    def _payload(self, release: dict[str, Any]) -> bytes:
        return json.dumps(release).encode("utf-8")

    def request(
        self,
        method: str,
        url: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        self.calls.append((method, url))
        parts = urllib.parse.urlsplit(url)
        path = parts.path
        prefix = f"/repos/{self.repository}"
        if parts.netloc == urllib.parse.urlsplit(API).netloc and path.startswith(
            prefix + "/releases/tags/"
        ):
            tag = urllib.parse.unquote(path[len(prefix + "/releases/tags/") :])
            release = self.releases.get(tag)
            if release is None:
                return Response(404, b'{"message": "Not Found"}')
            return Response(200, self._payload(release))
        if method == "POST" and path == prefix + "/releases":
            document = json.loads((body or b"{}").decode("utf-8"))
            tag = document["tag_name"]
            if tag in self.releases:
                return Response(422, b'{"message": "already_exists"}')
            release_id = self._id()
            self.releases[tag] = {
                "id": release_id,
                "tag_name": tag,
                "name": document["name"],
                "body": document["body"],
                "upload_url": f"{UPLOADS}{prefix}/releases/{release_id}/assets"
                "{?name,label}",
                "assets": [],
            }
            return Response(201, self._payload(self.releases[tag]))
        if method == "POST" and parts.netloc == urllib.parse.urlsplit(UPLOADS).netloc:
            if self.fail_upload_after is not None and (
                self._uploads >= self.fail_upload_after
            ):
                return Response(502, b'{"message": "upstream"}')
            self._uploads += 1
            name = urllib.parse.parse_qs(parts.query)["name"][0]
            release_id = int(path.rsplit("/", 2)[-2])
            release = next(r for r in self.releases.values() if r["id"] == release_id)
            if any(asset["name"] == name for asset in release["assets"]):
                return Response(422, b'{"message": "already_exists"}')
            asset_id = self._id()
            self.blobs[asset_id] = body or b""
            asset = {
                "id": asset_id,
                "name": name,
                "url": f"{API}{prefix}/releases/assets/{asset_id}",
            }
            release["assets"].append(asset)
            return Response(201, json.dumps(asset).encode("utf-8"))
        if method == "GET" and path.startswith(prefix + "/releases/assets/"):
            asset_id = int(path.rsplit("/", 1)[-1])
            return Response(200, self.blobs[asset_id])
        return Response(404, b'{"message": "unhandled by fake"}')


class Fixture:
    """A throwaway consumer repository with a bare ``origin``."""

    def __init__(self, base: pathlib.Path) -> None:
        self.base = base
        self.origin = base / "origin.git"
        self.work = base / "work"
        self.bin = base / "bin"
        run(
            base,
            "git",
            "init",
            "--quiet",
            "--bare",
            "--initial-branch=main",
            str(self.origin),
        )
        run(base, "git", "init", "--quiet", "--initial-branch=main", str(self.work))
        git(self.work, "remote", "add", "origin", str(self.origin))
        self.bin.mkdir()
        shim = self.bin / "uv"
        shim.write_text(UV_SHIM, encoding="utf-8")
        shim.chmod(0o755)

    def write(self, relative: str, text: str) -> None:
        path = self.work / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit(self, message: str) -> str:
        git(self.work, "add", "--all")
        git(self.work, "commit", "--quiet", "-m", message)
        git(self.work, "push", "--quiet", "origin", "HEAD:refs/heads/main")
        return git(self.work, "rev-parse", "HEAD").strip()

    def remote_tag(self, name: str) -> str | None:
        """The commit a remote tag points at, annotated tags peeled."""

        refs: dict[str, str] = {}
        for line in git(self.work, "ls-remote", "--tags", "origin").splitlines():
            sha, _, ref = line.partition("\t")
            refs[ref] = sha
        ref = f"refs/tags/{name}"
        return refs.get(ref + "^{}", refs.get(ref))

    def env(self) -> dict[str, str]:
        return {
            "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "GITHUB_API_URL": API,
        }


def release_json(adapter: str, major_tag: bool) -> str:
    authority = {"python-uv": "pyproject.toml", "node-npm": "package.json"}[adapter]
    return (
        json.dumps(
            {
                "schema": "l9.release/v1",
                "adapter": adapter,
                "version_authority": authority,
                "changelog": "CHANGELOG.md",
                "major_tag": major_tag,
            },
            indent=2,
        )
        + "\n"
    )


def python_project(fixture: Fixture, version: str, dated: bool = True) -> None:
    fixture.write(".l9/release.json", release_json("python-uv", False))
    fixture.write(
        "pyproject.toml",
        textwrap.dedent(f"""\
        [project]
        name = "release-fixture"
        version = "{version}"
        """),
    )
    fixture.write("src/release_fixture/__init__.py", f'VERSION = "{version}"\n')
    heading = f"## {version} - 2026-11-14" if dated else f"## {version}"
    fixture.write("CHANGELOG.md", f"# Changelog\n\n{heading}\n\n- Release {version}.\n")


def node_project(fixture: Fixture, version: str, major_tag: bool = True) -> None:
    fixture.write(".l9/release.json", release_json("node-npm", major_tag))
    fixture.write(
        "package.json",
        json.dumps(
            {
                "name": "release-fixture",
                "version": version,
                "files": ["index.js"],
                "license": "UNLICENSED",
            },
            indent=2,
        )
        + "\n",
    )
    fixture.write("index.js", f'module.exports = "{version}";\n')
    fixture.write(
        "CHANGELOG.md", f"# Changelog\n\n## {version} - 2026-11-14\n\n- {version}\n"
    )


def temporary() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory(prefix="l9-release-")
