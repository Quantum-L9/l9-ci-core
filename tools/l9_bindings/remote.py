"""What the producer actually published.

Three questions, answered without trusting anything local:

* **Is a tag present?** ``git ls-remote --exit-code`` is tri-state: exit 0
  present, 2 absent, anything else (notably 128) unreachable. Collapsing
  "unreachable" into "absent" is how a consumer silently decides a release does
  not exist because the network blinked, so the third state is a hard error.
* **What is the channel head?** The newest ``vMAJOR.MINOR.PATCH`` in the
  channel, with its peeled commit. ``ls-remote`` only emits the ``^{}`` peel
  line when the pattern is wider than one exact ref, so the channel query uses
  ``refs/tags/vN.*``; the presence query uses the exact ref and reads only
  presence from it.
* **What is the release identity?** ``l9-release.json`` when the producer
  publishes one, otherwise derived from the tag and ``SHA256SUMS``. The
  derived form is weaker (no channel assertion) and is reported as such, so a
  consumer can tell an adopted producer from a legacy one.

The token never reaches ``argv``: git receives it through a credential helper
that reads the environment, and the REST calls carry it in a header.
"""

from __future__ import annotations

import enum
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass

API = "https://api.github.com"
_VERSION = re.compile(r"^v(?P<version>[0-9]+\.[0-9]+\.[0-9]+)$")
_TOKEN = "L9_GIT_TOKEN"


class RemoteError(Exception):
    """The producer's state could not be established, so nothing may proceed."""


class RemoteState(enum.Enum):
    PRESENT = "present"
    ABSENT = "absent"
    UNREACHABLE = "unreachable"


@dataclass(frozen=True)
class ChannelHead:
    channel: str
    tag: str
    version: str
    commit: str
    object_sha: str | None

    @property
    def key(self) -> tuple[int, int, int]:
        major, minor, patch = self.version.split(".")
        return int(major), int(minor), int(patch)


@dataclass(frozen=True)
class ReleaseIdentity:
    repository: str
    channel: str
    tag: str
    version: str
    commit: str
    files: dict[str, str]
    source: str
    release_url: str | None


def _git(
    arguments: list[str], token: str | None, cwd: str | None = None
) -> subprocess.CompletedProcess[str]:
    command = ["git"]
    if token:
        helper = (
            '!f() { echo username=x-access-token; echo "password=$' + _TOKEN + '"; }; f'
        )
        command += ["-c", "credential.helper=", "-c", f"credential.helper={helper}"]
    command += arguments
    environment = {"GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(
        command,
        cwd=cwd,
        env={**os.environ, **environment, _TOKEN: token or ""},
        capture_output=True,
        text=True,
        check=False,
    )


def _refs(repository: str, pattern: str, token: str | None) -> dict[str, str]:
    url = f"https://github.com/{repository}.git"
    result = _git(["ls-remote", "--tags", url, pattern], token)
    if result.returncode != 0:
        raise RemoteError(
            f"git ls-remote {repository} {pattern} failed with "
            f"{result.returncode}: {result.stderr.strip() or 'no output'}"
        )
    refs: dict[str, str] = {}
    for line in result.stdout.splitlines():
        sha, _, ref = line.partition("\t")
        if len(sha) != 40 or not ref.startswith("refs/tags/"):
            continue
        refs[ref.removeprefix("refs/tags/")] = sha
    return refs


def tag_state(repository: str, tag: str, token: str | None = None) -> RemoteState:
    url = f"https://github.com/{repository}.git"
    result = _git(
        ["ls-remote", "--exit-code", "--tags", url, f"refs/tags/{tag}"], token
    )
    if result.returncode == 0:
        return RemoteState.PRESENT
    if result.returncode == 2:
        return RemoteState.ABSENT
    raise RemoteError(
        f"git ls-remote {repository} refs/tags/{tag} failed with "
        f"{result.returncode}: {result.stderr.strip() or 'no output'}"
    )


def channel_head(
    repository: str, channel: str, token: str | None = None
) -> ChannelHead | None:
    """Newest release in the channel, or ``None`` when it holds none."""
    refs = _refs(repository, f"refs/tags/{channel}.*", token)
    best: tuple[tuple[int, int, int], str, str] | None = None
    for name, sha in refs.items():
        if name.endswith("^{}"):
            continue
        match = _VERSION.match(name)
        if match is None:
            continue
        peeled = refs.get(f"{name}^{{}}")
        commit = peeled or sha
        major, minor, patch = (int(part) for part in match.group("version").split("."))
        key = (major, minor, patch)
        if best is None or key > best[0]:
            best = (key, name, commit)
    if best is None:
        return None
    _, name, commit = best
    match = _VERSION.match(name)
    assert match is not None
    direct = refs[name]
    return ChannelHead(
        channel=channel,
        tag=name,
        version=match.group("version"),
        commit=commit,
        object_sha=direct if direct != commit else None,
    )


def _request(
    url: str, token: str | None, accept: str = "application/vnd.github+json"
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, method="GET")
    request.add_header("Accept", accept)
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return 404, b""
        raise RemoteError(f"GET {url} failed with HTTP {error.code}") from error
    except urllib.error.URLError as error:
        raise RemoteError(f"GET {url} failed: {error.reason}") from error


def _release(repository: str, tag: str, token: str | None) -> dict | None:
    status, body = _request(f"{API}/repos/{repository}/releases/tags/{tag}", token)
    if status == 404:
        return None
    try:
        document = json.loads(body)
    except json.JSONDecodeError as error:
        raise RemoteError(f"release {tag} of {repository} is not JSON") from error
    if not isinstance(document, dict):
        raise RemoteError(f"release {tag} of {repository} is not an object")
    return document


def _asset(release: dict, name: str, token: str | None) -> bytes | None:
    for asset in release.get("assets") or []:
        if isinstance(asset, dict) and asset.get("name") == name:
            url = asset.get("url")
            if not isinstance(url, str):
                raise RemoteError(f"asset {name} carries no API url")
            status, body = _request(url, token, accept="application/octet-stream")
            if status != 200:
                raise RemoteError(f"asset {name} could not be downloaded")
            return body
    return None


def _sums(body: bytes, tag: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in body.decode("utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        if separator and len(digest) == 64 and name:
            entries[name] = digest
    if not entries:
        raise RemoteError(f"SHA256SUMS of {tag} names no artifact")
    return entries


def fetch_identity(
    repository: str,
    head: ChannelHead,
    token: str | None = None,
) -> ReleaseIdentity:
    """The identity a consumer may pin: document when present, else derived."""
    release = _release(repository, head.tag, token)
    if release is None:
        return ReleaseIdentity(
            repository=repository,
            channel=head.channel,
            tag=head.tag,
            version=head.version,
            commit=head.commit,
            files={},
            source="tag-only",
            release_url=None,
        )
    url = release.get("html_url") if isinstance(release.get("html_url"), str) else None
    document_bytes = _asset(release, "l9-release.json", token)
    if document_bytes is not None:
        try:
            document = json.loads(document_bytes)
        except json.JSONDecodeError as error:
            raise RemoteError(f"l9-release.json of {head.tag} is not JSON") from error
        if not isinstance(document, dict):
            raise RemoteError(f"l9-release.json of {head.tag} is not an object")
        expected = {
            "schema": "l9.release-identity/v1",
            "repository": repository,
            "tag": head.tag,
            "version": head.version,
            "commit": head.commit,
            "channel": head.channel,
        }
        for name, value in expected.items():
            if document.get(name) != value:
                raise RemoteError(
                    f"l9-release.json of {head.tag} says {name}="
                    f"{document.get(name)!r}, but the tag says {value!r}"
                )
        files = document.get("files")
        if not isinstance(files, dict) or not files:
            raise RemoteError(f"l9-release.json of {head.tag} names no artifact")
        return ReleaseIdentity(
            repository=repository,
            channel=head.channel,
            tag=head.tag,
            version=head.version,
            commit=head.commit,
            files={str(name): str(digest) for name, digest in files.items()},
            source="document",
            release_url=url,
        )
    sums_bytes = _asset(release, "SHA256SUMS", token)
    return ReleaseIdentity(
        repository=repository,
        channel=head.channel,
        tag=head.tag,
        version=head.version,
        commit=head.commit,
        files=_sums(sums_bytes, head.tag) if sums_bytes else {},
        source="derived",
        release_url=url,
    )
