"""GitHub Releases REST client over one stubbable transport boundary.

``Transport.request`` is the only place bytes leave the process, so tests
replace it with an in-memory fake and exercise every release state without a
network. The default transport is ``urllib`` with the token from ``GH_TOKEN``
or ``GITHUB_TOKEN`` (never logged) against ``GITHUB_API_URL``.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.client import HTTPMessage
from typing import IO, Any, Protocol

from .errors import ReleaseInfrastructureError

API_VERSION = "2022-11-28"


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ReleaseInfrastructureError(
                f"GitHub returned non-JSON (HTTP {self.status})"
            ) from error


class Transport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response: ...


class _NoCredentialRedirect(urllib.request.HTTPRedirectHandler):
    """Follow a redirect without replaying the token to another host.

    Asset downloads redirect to object storage; ``urllib`` would otherwise copy
    the ``Authorization`` header onto the redirected request.
    """

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            old_host = urllib.parse.urlsplit(req.full_url).netloc
            if urllib.parse.urlsplit(newurl).netloc != old_host:
                new.remove_header("Authorization")
        return new


class UrllibTransport:
    """Real transport. A cross-host redirect drops the Authorization header."""

    def __init__(self, token: str) -> None:
        self._token = token
        self._opener = urllib.request.build_opener(_NoCredentialRedirect())

    def request(
        self,
        method: str,
        url: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        merged = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "l9-ci-core-release-engine",
            "Authorization": f"Bearer {self._token}",
        }
        merged.update(headers or {})
        request = urllib.request.Request(url, data=body, method=method, headers=merged)
        try:
            with self._opener.open(request, timeout=120) as response:
                return Response(response.status, response.read())
        except urllib.error.HTTPError as error:
            return Response(error.code, error.read())
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise ReleaseInfrastructureError(
                f"GitHub {method} {urllib.parse.urlsplit(url).path} failed: {error}"
            ) from error


def default_transport() -> UrllibTransport:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ReleaseInfrastructureError(
            "GH_TOKEN or GITHUB_TOKEN is required to reach the GitHub Releases API"
        )
    return UrllibTransport(token)


@dataclass(frozen=True)
class Asset:
    id: int
    name: str
    url: str


@dataclass(frozen=True)
class Release:
    id: int
    tag_name: str
    upload_url: str
    assets: tuple[Asset, ...]


def _release(document: Any) -> Release:
    if not isinstance(document, dict):
        raise ReleaseInfrastructureError("GitHub release payload is not an object")
    try:
        assets = tuple(
            Asset(id=int(item["id"]), name=str(item["name"]), url=str(item["url"]))
            for item in document.get("assets", [])
        )
        return Release(
            id=int(document["id"]),
            tag_name=str(document["tag_name"]),
            upload_url=str(document["upload_url"]).split("{", 1)[0],
            assets=assets,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ReleaseInfrastructureError(f"malformed GitHub release payload: {error}")


class GitHubReleases:
    def __init__(
        self,
        repository: str,
        transport: Transport,
        api_url: str | None = None,
    ) -> None:
        if repository.count("/") != 1:
            raise ReleaseInfrastructureError(f"invalid repository {repository!r}")
        self.repository = repository
        self.transport = transport
        self.api = (
            api_url or os.environ.get("GITHUB_API_URL") or "https://api.github.com"
        ).rstrip("/")

    def _url(self, path: str) -> str:
        return f"{self.api}/repos/{self.repository}{path}"

    def _expect(self, response: Response, *ok: int, what: str) -> Response:
        if response.status not in ok:
            detail = response.body[:300].decode("utf-8", "replace")
            raise ReleaseInfrastructureError(
                f"{what}: HTTP {response.status}: {detail}"
            )
        return response

    def get_by_tag(self, tag: str) -> Release | None:
        response = self.transport.request(
            "GET", self._url(f"/releases/tags/{urllib.parse.quote(tag, safe='')}")
        )
        if response.status == 404:
            return None
        self._expect(response, 200, what=f"read release {tag}")
        return _release(response.json())

    def create(self, tag: str, name: str, body: str) -> Release:
        payload = {
            "tag_name": tag,
            "name": name,
            "body": body,
            "draft": False,
            "prerelease": False,
            "make_latest": "legacy",
        }
        response = self.transport.request(
            "POST",
            self._url("/releases"),
            json.dumps(payload).encode("utf-8"),
            {"Content-Type": "application/json"},
        )
        self._expect(response, 201, what=f"create release {tag}")
        return _release(response.json())

    def upload_asset(self, release: Release, name: str, data: bytes) -> Asset:
        query = urllib.parse.urlencode({"name": name})
        response = self.transport.request(
            "POST",
            f"{release.upload_url}?{query}",
            data,
            {"Content-Type": "application/octet-stream"},
        )
        self._expect(response, 201, what=f"upload asset {name}")
        document = response.json()
        return Asset(
            id=int(document["id"]), name=str(document["name"]), url=str(document["url"])
        )

    def download_asset(self, asset: Asset) -> bytes:
        response = self.transport.request(
            "GET", asset.url, headers={"Accept": "application/octet-stream"}
        )
        self._expect(response, 200, what=f"download asset {asset.name}")
        return response.body
