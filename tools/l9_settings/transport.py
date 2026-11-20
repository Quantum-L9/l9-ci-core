"""The one place the settings engine's bytes leave the process.

Tests replace ``Transport`` with an in-memory fake. The real transport is
``urllib`` with a bearer token (never logged) and refuses to follow a
redirect to another host with the token attached.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.client import HTTPMessage
from typing import IO, Protocol

from .errors import SettingsInfrastructureError

API_VERSION = "2022-11-28"
USER_AGENT = "l9-ci-core-settings-engine"


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes


class Transport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response: ...


class _NoCredentialRedirect(urllib.request.HTTPRedirectHandler):
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
            "User-Agent": USER_AGENT,
            "Authorization": f"Bearer {self._token}",
        }
        merged.update(headers or {})
        request = urllib.request.Request(url, data=body, method=method, headers=merged)
        try:
            with self._opener.open(request, timeout=60) as response:
                return Response(response.status, response.read())
        except urllib.error.HTTPError as error:
            return Response(error.code, error.read())
        except OSError as error:  # URLError and TimeoutError are OSErrors
            path = urllib.parse.urlsplit(url).path
            raise SettingsInfrastructureError(
                f"GitHub {method} {path} failed: {error}"
            ) from error
