"""Converge every declared binding onto its channel head.

``plan`` answers one question per binding: does what this consumer pins equal
what the producer last released in that channel? ``converge`` acts on the
answer, under the policy the binding declares:

* ``library``  — run the declared refresh, commit, open a pull request, and
  enable auto-merge only when the binding says so. The consumer's own required
  checks remain the gate.
* ``contract`` — rewrite the declared fields, open a pull request, never
  auto-merge. The proof workflow the binding names is the gate, and it runs in
  the consumer against the consumer's code.
* ``deployed`` — no pull request exists to open. ``l9-deploy`` owns the
  environment, and pretending otherwise would make a rollback require a source
  change. The engine's whole job is to ask for the deployment: it proves the
  producer published an ``l9.deployment-request/v2`` with the release
  (``dispatch.py``), re-binds it to this binding's environment and dispatches
  ``l9.release.requested.v2``. A release with no published request stays
  ``delegated`` and says so; emission is opt-in (``converge --dispatch``), so a
  ``plan`` can never deploy.

A blocked binding is never a silent pass: an unreachable producer, a channel
with no release, a release whose identity disagrees with its tag, or a lock
that cannot be read all stop that binding and say why.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass

from . import apply as writer
from . import dispatch as broker
from . import locks, model, remote
from .model import Binding, Bindings
from .remote import (
    API,
    ChannelHead,
    ReleaseIdentity,
    RemoteError,
    channel_head,
    fetch_identity,
)

CONVERGED = "converged"
DRIFTED = "drifted"
PROPOSED = "proposed"
BLOCKED = "blocked"
DELEGATED = "delegated"
REQUESTED = "requested"

# The engine authors its own commits, so it carries the identity to make them.
# A hosted runner has no ``user.email`` configured, and inheriting one would
# attribute a machine-generated lock refresh to whoever happens to own the
# host. ``core <core@invalid>`` is the identity the org's workflows already use.
IDENTITY = ("-c", "user.name=core", "-c", "user.email=core@invalid")


@dataclass(frozen=True)
class Verdict:
    binding: Binding
    state: str
    detail: str
    current: str | None = None
    target: str | None = None
    head: ChannelHead | None = None
    identity: ReleaseIdentity | None = None
    request: dict[str, object] | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.binding.id,
            "producer": self.binding.producer,
            "channel": self.binding.channel,
            "class": self.binding.klass,
            "state": self.state,
            "current": self.current,
            "target": self.target,
            "tag": self.head.tag if self.head else None,
            "commit": self.identity.commit if self.identity else None,
            "environment": self.binding.environment,
            "request_id": (self.request or {}).get("request_id"),
            "detail": self.detail,
        }


def _satisfied(pinned: str | None, identity: ReleaseIdentity) -> bool:
    """Whether a lock's pin is the release the channel resolves to.

    A lock names one or the other. An index or ``uv`` lock records the
    version; a hash-locked requirements file that follows a git channel
    records the commit it resolved to, because pip cannot hash-verify a ref.
    Both are the same claim — "this is the release at the channel head" — so
    both satisfy it, and nothing else does.
    """
    return pinned is not None and pinned in {identity.version, identity.commit}


def evaluate(binding: Binding, workspace: pathlib.Path, token: str | None) -> Verdict:
    try:
        head = channel_head(binding.producer, binding.channel, token)
        if head is None:
            return Verdict(binding, BLOCKED, "the channel holds no release")
        identity = fetch_identity(binding.producer, head, token)
        if binding.klass == "deployed":
            body = remote.release_asset(
                binding.producer, head.tag, broker.REQUEST_ASSET, token
            )
            if body is None:
                return Verdict(
                    binding,
                    DELEGATED,
                    f"environment {binding.environment} is owned by l9-deploy; "
                    f"{head.tag} publishes no {broker.REQUEST_ASSET}, so there is "
                    "no request to dispatch",
                    target=identity.version,
                    head=head,
                    identity=identity,
                )
            try:
                document = json.loads(body)
                broker.validate(
                    document, producer=binding.producer, commit=identity.commit
                )
            except (json.JSONDecodeError, broker.DispatchError) as error:
                return Verdict(
                    binding,
                    BLOCKED,
                    f"{head.tag} {broker.REQUEST_ASSET} is not a request this "
                    f"release can prove: {error}",
                    target=identity.version,
                    head=head,
                    identity=identity,
                )
            return Verdict(
                binding,
                DELEGATED,
                f"environment {binding.environment} is owned by l9-deploy; "
                f"{head.tag} publishes a valid {broker.REQUEST_ASSET} "
                "(dispatch is opt-in: converge --dispatch)",
                target=identity.version,
                head=head,
                identity=identity,
                request=document,
            )
        if binding.klass == "library":
            if binding.lock is None or binding.package is None:
                return Verdict(binding, BLOCKED, "the binding names no lock")
            current = locks.pinned(workspace, binding.lock, binding.package)
            if _satisfied(current, identity):
                return Verdict(
                    binding,
                    CONVERGED,
                    f"{binding.lock} pins {binding.package} {current}",
                    current=current,
                    target=identity.version,
                    head=head,
                    identity=identity,
                )
            return Verdict(
                binding,
                DRIFTED,
                f"{binding.lock} pins {current or 'nothing'}, "
                f"{head.tag} is {identity.version}",
                current=current,
                target=identity.version,
                head=head,
                identity=identity,
            )
        if binding.file is None:
            return Verdict(binding, BLOCKED, "the binding names no file")
        document = writer.read_document(workspace / binding.file)
        version_field = binding.fields.get("version")
        revision_field = binding.fields.get("revision")
        current_version: str | None = None
        if version_field is not None:
            for path in version_field.paths:
                pinned = str(writer.get_dotted(document, path))
                current_version = current_version or pinned
                if pinned != identity.version:
                    return Verdict(
                        binding,
                        DRIFTED,
                        f"{binding.file} {path} pins {pinned}, "
                        f"{head.tag} is {identity.version}",
                        current=pinned,
                        target=identity.version,
                        head=head,
                        identity=identity,
                    )
        current_revision: str | None = None
        if revision_field is not None:
            for path in revision_field.paths:
                pinned_revision = str(writer.get_dotted(document, path))
                current_revision = current_revision or pinned_revision
                if pinned_revision != identity.commit:
                    return Verdict(
                        binding,
                        DRIFTED,
                        f"{binding.file} {path} pins {pinned_revision[:8]}, "
                        f"but {head.tag} peels to {identity.commit[:8]}",
                        current=current_version,
                        target=identity.version,
                        head=head,
                        identity=identity,
                    )
        return Verdict(
            binding,
            CONVERGED,
            f"{binding.file} pins {current_version} at {current_revision}",
            current=current_version,
            target=identity.version,
            head=head,
            identity=identity,
        )
    except (
        RemoteError,
        broker.DispatchError,
        locks.LockUnreadable,
        writer.ApplyError,
    ) as error:
        return Verdict(binding, BLOCKED, str(error))


def _values(verdict: Verdict) -> dict[str, str]:
    assert verdict.head is not None and verdict.identity is not None
    head, identity = verdict.head, verdict.identity
    values = {
        "version": identity.version,
        "revision": identity.commit,
        "commit": identity.commit,
        "tag": head.tag,
        "channel": head.channel,
        "object": head.object_sha or identity.commit,
    }
    # The digest is resolved only where the document pins one. A library
    # binding names no digest, and a producer whose release publishes no
    # artifact (a tag with no release object) has none to resolve; requiring
    # one would block a lock-only converge on a fact it never reads. A
    # declared digest field still fails closed: ``rewrite`` refuses a field
    # with no resolved value.
    if "digest" in verdict.binding.fields:
        values["digest"] = writer.digest_for(verdict.binding, identity.files)
    return values


def _git(workspace: pathlib.Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=workspace, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise writer.ApplyError(
            f"git {' '.join(arguments)} failed with {result.returncode}: "
            f"{(result.stderr or result.stdout).strip()[:800]}"
        )
    return result.stdout.strip()


def _request(
    method: str, url: str, token: str, payload: dict | None = None
) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    request.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            return response.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")
        raise writer.ApplyError(
            f"{method} {url} failed with HTTP {error.code}: {body[:400]}"
        ) from error
    except urllib.error.URLError as error:
        raise writer.ApplyError(f"{method} {url} failed: {error.reason}") from error


def _body(verdict: Verdict, before: dict[str, str], after: dict[str, str]) -> str:
    binding = verdict.binding
    identity = verdict.identity
    assert identity is not None and verdict.head is not None
    lines = [
        f"`{binding.id}` is declared in `.l9/bindings.yaml` as a "
        f"**{binding.klass}** binding on `{binding.producer}` channel "
        f"`{binding.channel}`.",
        "",
        "| | before | after |",
        "|---|---|---|",
    ]
    for name in sorted(after):
        lines.append(f"| `{name}` | `{before.get(name, '')}` | `{after[name]}` |")
    lines += [
        "",
        f"- Release: `{verdict.head.tag}` at `{identity.commit}` "
        f"({identity.source} identity)",
        f"- Release URL: {identity.release_url or 'not published as a release'}",
        f"- Policy: auto_merge={str(binding.policy.auto_merge).lower()}, "
        f"requires_proof={str(binding.policy.requires_proof).lower()}",
    ]
    if binding.proof:
        lines.append(f"- Proof: `{binding.proof}` must be green before merge")
    if identity.files:
        lines += ["", "```text"]
        lines += [
            f"{digest}  {name}" for name, digest in sorted(identity.files.items())
        ]
        lines.append("```")
    lines += [
        "",
        "_Opened by `tools/l9_bindings` (binding-converge). "
        "Every field above is derived from the release, not typed._",
    ]
    return "\n".join(lines)


def _pr(
    repository: str, token: str, branch: str, base: str, title: str, body: str
) -> dict:
    owner = repository.split("/")[0]
    status, existing = _request(
        "GET",
        f"{API}/repos/{repository}/pulls?state=open&head={owner}:{branch}",
        token,
    )
    if status == 200 and isinstance(existing, list) and existing:
        return existing[0]
    _, created = _request(
        "POST",
        f"{API}/repos/{repository}/pulls",
        token,
        {"title": title, "head": branch, "base": base, "body": body},
    )
    return created


def _auto_merge(repository: str, token: str, node_id: str) -> None:
    query = (
        "mutation($id:ID!){enablePullRequestAutoMerge("
        "input:{pullRequestId:$id,mergeMethod:SQUASH})"
        "{pullRequest{number}}}"
    )
    _request(
        "POST", f"{API}/graphql", token, {"query": query, "variables": {"id": node_id}}
    )


def _optional(url: str, token: str) -> tuple[int, dict | list]:
    """GET that treats 404 as an answer rather than a failure."""
    request = urllib.request.Request(url, method="GET")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            return response.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return 404, {}
        raise writer.ApplyError(f"GET {url} failed with HTTP {error.code}") from error
    except urllib.error.URLError as error:
        raise writer.ApplyError(f"GET {url} failed: {error.reason}") from error


def discover(org: str, token: str) -> list[str]:
    """Every repository in the organization that declares a binding."""
    repositories: list[str] = []
    page = 1
    while True:
        status, batch = _optional(
            f"{API}/orgs/{org}/repos?per_page=100&page={page}", token
        )
        if status != 200 or not isinstance(batch, list) or not batch:
            break
        for entry in batch:
            if isinstance(entry, dict) and isinstance(entry.get("full_name"), str):
                repositories.append(entry["full_name"])
        if len(batch) < 100:
            break
        page += 1
    found: list[str] = []
    for repository in repositories:
        status, _ = _optional(
            f"{API}/repos/{repository}/contents/{model.PATH.as_posix()}", token
        )
        if status == 200:
            found.append(repository)
    return found


def converge(
    bindings: Bindings,
    workspace: pathlib.Path,
    token: str,
    *,
    dry_run: bool,
    dispatch: bool = False,
    dispatch_token: str | None = None,
    deploy_repository: str = broker.DEPLOY_REPOSITORY,
) -> list[Verdict]:
    verdicts: list[Verdict] = []
    for binding in bindings.bindings:
        verdicts.append(evaluate(binding, workspace, token))
    if dry_run:
        return verdicts
    if dispatch:
        for position, verdict in enumerate(list(verdicts)):
            binding = verdict.binding
            if binding.klass != "deployed" or verdict.request is None:
                continue
            assert verdict.identity is not None
            try:
                payload = broker.emit(
                    verdict.request,
                    consumer=bindings.consumer,
                    binding=binding,
                    producer=binding.producer,
                    commit=verdict.identity.commit,
                    token=dispatch_token or token,
                    repository=deploy_repository,
                )
            except broker.DispatchError as error:
                verdicts[position] = Verdict(
                    binding,
                    BLOCKED,
                    str(error),
                    current=verdict.current,
                    target=verdict.target,
                    head=verdict.head,
                    identity=verdict.identity,
                    request=verdict.request,
                )
                continue
            environment = str(payload["target"]["environment"])
            verdicts[position] = Verdict(
                binding,
                REQUESTED,
                f"dispatched {environment} for {verdict.identity.version} "
                f"({payload['idempotency_key']})",
                current=verdict.current,
                target=verdict.target,
                head=verdict.head,
                identity=verdict.identity,
                request=payload,
            )
    for position, verdict in enumerate(list(verdicts)):
        if verdict.state != DRIFTED or not verdict.binding.policy.auto_pr:
            continue
        binding = verdict.binding
        assert verdict.identity is not None
        branch = f"l9/binding/{binding.id}/{verdict.identity.version}"
        values = _values(verdict)
        try:
            _git(workspace, "switch", "-C", branch)
            if binding.klass == "library":
                command = writer.refresh(workspace, binding, verdict.identity.version)
                # The refresh is a declared command, not a guaranteed one. Read
                # the lock back: a command that moved the wrong package, or
                # followed a tag that has not moved, must not produce a pull
                # request claiming a version the lock does not pin.
                assert binding.lock is not None and binding.package is not None
                pinned = locks.pinned(workspace, binding.lock, binding.package)
                if not _satisfied(pinned, verdict.identity):
                    raise writer.ApplyError(
                        f"binding {binding.id}: {command!r} left {binding.lock} "
                        f"pinning {pinned or 'nothing'}, not "
                        f"{verdict.identity.version}"
                    )
                before = {"lock": verdict.current or ""}
                after = {"lock": f"{binding.package}=={verdict.identity.version}"}
                detail = f"`{command}`"
            else:
                before, after = writer.rewrite(workspace, binding, values)
                detail = f"rewrote {binding.file}"
            if not _git(workspace, "status", "--porcelain"):
                verdicts[position] = Verdict(
                    binding,
                    BLOCKED,
                    "the refresh produced no change, so the lock still drifts",
                    current=verdict.current,
                    target=verdict.target,
                    head=verdict.head,
                    identity=verdict.identity,
                )
                continue
            _git(workspace, "add", "--all")
            _git(
                workspace,
                *IDENTITY,
                "commit",
                "-m",
                f"chore(binding): {binding.id} -> {verdict.identity.version} "
                f"({verdict.head.tag if verdict.head else ''})",
                "-m",
                detail,
            )
            _git(workspace, "push", "-u", "origin", branch)
            pull = _pr(
                bindings.consumer,
                token,
                branch,
                "main",
                f"chore(binding): {binding.id} -> {verdict.identity.version}",
                _body(verdict, before, after),
            )
            if binding.policy.auto_merge and isinstance(pull.get("node_id"), str):
                _auto_merge(bindings.consumer, token, pull["node_id"])
            verdicts[position] = Verdict(
                binding,
                PROPOSED,
                f"opened #{pull.get('number')} for {verdict.identity.version}"
                + (" (auto-merge enabled)" if binding.policy.auto_merge else ""),
                current=verdict.current,
                target=verdict.target,
                head=verdict.head,
                identity=verdict.identity,
            )
        except (writer.ApplyError, locks.LockUnreadable) as error:
            verdicts[position] = Verdict(
                binding,
                BLOCKED,
                str(error),
                current=verdict.current,
                target=verdict.target,
                head=verdict.head,
                identity=verdict.identity,
            )
    return verdicts


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="l9-bindings")
    parser.add_argument("--workspace", type=pathlib.Path, default=pathlib.Path.cwd())
    parser.add_argument(
        "--token", default=None, help="GitHub token (default: $GITHUB_TOKEN)"
    )
    stages = parser.add_subparsers(dest="stage", required=True)
    plan = stages.add_parser("plan", help="report drift; write nothing")
    plan.add_argument("--json", action="store_true")
    run = stages.add_parser("converge", help="open the pull requests drift requires")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument(
        "--dispatch",
        action="store_true",
        help=(
            "also emit l9.release.requested.v2 for deployed bindings whose "
            "release publishes a valid deployment-request.json"
        ),
    )
    run.add_argument(
        "--dispatch-token",
        default=None,
        help=(
            "token authorized to dispatch to the deployment repository "
            "(default: --token; it needs contents: write on that repository)"
        ),
    )
    run.add_argument(
        "--deploy-repository",
        default=broker.DEPLOY_REPOSITORY,
        help=f"the repository that executes requests (default: {broker.DEPLOY_REPOSITORY})",
    )
    run.add_argument("--json", action="store_true")
    found = stages.add_parser(
        "discover", help="list the repositories that declare a binding"
    )
    found.add_argument("--org", required=True)
    stages.add_parser("validate", help="parse and validate the declared bindings")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    token = arguments.token or os.environ.get("GITHUB_TOKEN") or ""
    if arguments.stage == "discover":
        if not token:
            print("bindings: discover needs a token", file=sys.stderr)
            return 2
        try:
            print(json.dumps(discover(arguments.org, token)))
        except writer.ApplyError as error:
            print(f"bindings: {error}", file=sys.stderr)
            return 2
        return 0
    try:
        bindings = model.load(arguments.workspace)
    except model.BindingsInvalid as error:
        print(f"bindings: {error}", file=sys.stderr)
        return 1
    if bindings is None:
        print("bindings: none declared (.l9/bindings.yaml is absent)")
        return 0
    if arguments.stage == "validate":
        print(f"bindings: {len(bindings.bindings)} declared for {bindings.consumer}")
        return 0
    if arguments.stage == "plan":
        verdicts = [evaluate(b, arguments.workspace, token) for b in bindings.bindings]
    else:
        if not token:
            print("bindings: converge needs a token", file=sys.stderr)
            return 2
        verdicts = converge(
            bindings,
            arguments.workspace,
            token,
            dry_run=arguments.dry_run,
            dispatch=arguments.dispatch,
            dispatch_token=arguments.dispatch_token,
            deploy_repository=arguments.deploy_repository,
        )
    if arguments.json:
        print(json.dumps([verdict.as_dict() for verdict in verdicts], indent=2))
    else:
        for verdict in verdicts:
            print(
                f"{verdict.binding.id:<12} {verdict.state:<10} "
                f"{verdict.current or '-':<12} -> {verdict.target or '-':<12} "
                f"{verdict.detail}"
            )
    states = {verdict.state for verdict in verdicts}
    if BLOCKED in states:
        return 2
    if DRIFTED in states:
        return 3
    return 0
