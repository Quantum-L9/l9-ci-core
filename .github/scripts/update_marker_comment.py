#!/usr/bin/env python3
"""Upsert a stable-marker PR comment (Kernel comment_protocol).

Finds an existing comment whose body contains the marker and updates it;
creates one only when the marker is absent. Never creates duplicates and never
matches by vague title.

Inputs (env):
  GITHUB_TOKEN       : token with issues:write / pull-requests:write
  GITHUB_REPOSITORY  : owner/repo
  PR_NUMBER          : pull request number
  COMMENT_BODY_PATH  : file containing the comment body (must contain a marker)
  COMMENT_MARKER     : marker string to match (default: <!-- l9-ci-summary-marker: v1 -->)

Stdlib only (urllib). Fails soft: a comment problem must never break CI.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"


def _request(method: str, url: str, token: str, payload: dict | None = None) -> tuple[int, object]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "l9-ci-marker-comment")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        return exc.code, {"error": exc.read().decode(errors="replace")}
    except urllib.error.URLError as exc:
        return 0, {"error": str(exc)}


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    pr_number = os.environ.get("PR_NUMBER", "").strip()
    body_path = os.environ.get("COMMENT_BODY_PATH", "ci_summary.md")
    marker = os.environ.get("COMMENT_MARKER", "<!-- l9-ci-summary-marker: v1 -->")

    if not (token and repo and pr_number and pr_number.isdigit()):
        print("[WARN] missing token/repo/pr_number; skipping comment upsert")
        return 0
    try:
        with open(body_path, encoding="utf-8") as fh:
            body = fh.read()
    except OSError as exc:
        print(f"[WARN] cannot read {body_path}: {exc}; skipping")
        return 0
    if marker not in body:
        print(f"[WARN] marker {marker!r} not present in body; skipping to avoid unmanaged comment")
        return 0

    list_url = f"{API}/repos/{repo}/issues/{pr_number}/comments?per_page=100"
    status, comments = _request("GET", list_url, token)
    if status != 200 or not isinstance(comments, list):
        print(f"[WARN] could not list comments (status {status}); skipping")
        return 0

    existing = next(
        (c for c in comments if isinstance(c, dict) and marker in (c.get("body") or "")),
        None,
    )
    if existing:
        url = f"{API}/repos/{repo}/issues/comments/{existing['id']}"
        st, _ = _request("PATCH", url, token, {"body": body})
        print(f"[INFO] updated existing marker comment (status {st})")
    else:
        url = f"{API}/repos/{repo}/issues/{pr_number}/comments"
        st, _ = _request("POST", url, token, {"body": body})
        print(f"[INFO] created new marker comment (status {st})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
