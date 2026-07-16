"""Replaceable GitHub provider adapter.

Every remote side effect (branch push, PR open, issue create/update, PR status
read) goes through one of these adapters, so the delivery/issue/monitor layers
never shell out to a provider directly and are unit-testable with a fake.

Adapters:
  DryRunAdapter  — records intents, performs NO remote effect (default; tests + --dry-run)
  GhCliAdapter   — executes via the `gh` CLI when present and explicitly enabled

Every method returns a receipt dict: {ok, effect, dry_run, ...}. Nothing here
claims a remote action it did not perform: DryRunAdapter always reports dry_run=True.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any


class GitHubAdapter:
    """Interface. Subclasses implement the effects; the base is a safe no-op."""

    dry_run = True
    name = "base"

    def ensure_labels(self, repo: str, labels: list[str]) -> dict[str, Any]:
        return {"ok": True, "effect": "ensure_labels", "dry_run": self.dry_run, "labels": labels}

    def open_pr(self, repo: str, head: str, base: str, title: str, body: str) -> dict[str, Any]:
        raise NotImplementedError

    def find_pr(self, repo: str, head: str) -> dict[str, Any] | None:
        raise NotImplementedError

    def search_issues(self, repo: str, dedupe_key: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def create_issue(self, repo: str, title: str, body: str, labels: list[str]) -> dict[str, Any]:
        raise NotImplementedError

    def update_issue(self, repo: str, number: int, body: str, reopen: bool) -> dict[str, Any]:
        raise NotImplementedError

    def pr_status(self, repo: str, number: int) -> dict[str, Any]:
        raise NotImplementedError


class DryRunAdapter(GitHubAdapter):
    """Performs no remote effect; records intents as receipts. Safe default + tests.

    An optional `state` seeds find/search results so tests can drive idempotency
    and monitoring paths deterministically without a network.
    """

    dry_run = True
    name = "dry-run"

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self.state = state or {}
        self.intents: list[dict[str, Any]] = []

    def _record(self, effect: str, **kw: Any) -> dict[str, Any]:
        r = {"ok": True, "effect": effect, "dry_run": True, **kw}
        self.intents.append(r)
        return r

    def open_pr(self, repo: str, head: str, base: str, title: str, body: str) -> dict[str, Any]:
        return self._record("open_pr", repo=repo, head=head, base=base, title=title)

    def find_pr(self, repo: str, head: str) -> dict[str, Any] | None:
        return self.state.get("prs", {}).get(head)

    def search_issues(self, repo: str, dedupe_key: str) -> list[dict[str, Any]]:
        return [i for i in self.state.get("issues", []) if i.get("dedupe_key") == dedupe_key]

    def create_issue(self, repo: str, title: str, body: str, labels: list[str]) -> dict[str, Any]:
        return self._record("create_issue", repo=repo, title=title, labels=labels)

    def update_issue(self, repo: str, number: int, body: str, reopen: bool) -> dict[str, Any]:
        return self._record("update_issue", repo=repo, number=number, reopen=reopen)

    def pr_status(self, repo: str, number: int) -> dict[str, Any]:
        return self.state.get("pr_status", {"checks": "unknown", "reviews": []})


class GhCliAdapter(GitHubAdapter):
    """Executes via the `gh` CLI. Only usable when gh is installed AND enabled."""

    dry_run = False
    name = "gh-cli"

    @staticmethod
    def available() -> bool:
        return shutil.which("gh") is not None

    def _gh(self, args: list[str]) -> tuple[int, str]:
        p = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
        return p.returncode, (p.stdout or "") + (p.stderr or "")

    def open_pr(self, repo: str, head: str, base: str, title: str, body: str) -> dict[str, Any]:
        rc, out = self._gh(
            [
                "pr",
                "create",
                "--repo",
                repo,
                "--head",
                head,
                "--base",
                base,
                "--title",
                title,
                "--body",
                body,
            ]
        )
        return {"ok": rc == 0, "effect": "open_pr", "dry_run": False, "head": head, "output": out}

    def find_pr(self, repo: str, head: str) -> dict[str, Any] | None:
        rc, out = self._gh(
            [
                "pr",
                "list",
                "--repo",
                repo,
                "--head",
                head,
                "--state",
                "all",
                "--json",
                "number,url,state",
            ]
        )
        if rc != 0 or not out.strip():
            return None
        try:
            items = json.loads(out)
        except Exception:
            return None
        return items[0] if items else None

    def search_issues(self, repo: str, dedupe_key: str) -> list[dict[str, Any]]:
        rc, out = self._gh(
            [
                "issue",
                "list",
                "--repo",
                repo,
                "--state",
                "all",
                "--search",
                dedupe_key,
                "--json",
                "number,title,state,body",
            ]
        )
        if rc != 0 or not out.strip():
            return []
        try:
            return json.loads(out)
        except Exception:
            return []

    def create_issue(self, repo: str, title: str, body: str, labels: list[str]) -> dict[str, Any]:
        args = ["issue", "create", "--repo", repo, "--title", title, "--body", body]
        for lbl in labels:
            args += ["--label", lbl]
        rc, out = self._gh(args)
        return {"ok": rc == 0, "effect": "create_issue", "dry_run": False, "output": out}

    def update_issue(self, repo: str, number: int, body: str, reopen: bool) -> dict[str, Any]:
        rc, out = self._gh(["issue", "comment", str(number), "--repo", repo, "--body", body])
        if reopen:
            self._gh(["issue", "reopen", str(number), "--repo", repo])
        return {"ok": rc == 0, "effect": "update_issue", "dry_run": False, "number": number}

    def pr_status(self, repo: str, number: int) -> dict[str, Any]:
        rc, out = self._gh(
            [
                "pr",
                "view",
                str(number),
                "--repo",
                repo,
                "--json",
                "statusCheckRollup,reviewDecision,reviews",
            ]
        )
        try:
            return json.loads(out) if rc == 0 else {"error": out}
        except Exception:
            return {"error": out}


def get_adapter(dry_run: bool, enable_gh: bool) -> GitHubAdapter:
    """Choose an adapter. Real gh only when explicitly enabled AND installed AND
    not dry-run; otherwise the safe DryRunAdapter (default)."""
    if not dry_run and enable_gh and GhCliAdapter.available():
        return GhCliAdapter()
    return DryRunAdapter()
