"""``plan``: decide what a release push still has to publish.

Publication has four independent side effects — the immutable exact tag, the
GitHub Release, the release assets, and (when declared) the maintained major
tag. They are decided separately so a rerun after a partial publication
finishes exactly what is missing instead of concluding it is done. This
generalises l9-meta-injector's ``release-state.js`` (its ADR-050), adding the
asset side effect.

:func:`decide` is pure: every input is an observation, so each partial
boundary is testable without a remote. :func:`observe` gathers those
observations and performs no write.
"""

from __future__ import annotations

import pathlib
from dataclasses import asdict, dataclass

from . import gitops
from .config import ReleaseConfig
from .errors import ReleaseRefused
from .github import GitHubReleases
from .identity import (
    ReleaseIdentity,
    changelog_section,
    exact_tag_version,
    parse_version,
    read_version,
    release_identity,
)

SUMS = "SHA256SUMS"


@dataclass(frozen=True)
class Observed:
    head: str
    version: str
    tags: dict[str, str]
    major_tag_version: str | None
    release_exists: bool
    release_assets: tuple[str, ...]
    major_declared: bool


@dataclass(frozen=True)
class Plan:
    version: str
    exact_tag: str
    major_tag: str | None
    is_release_commit: bool
    create_tag: bool
    create_release: bool
    upload_assets: bool
    advance_major: bool
    major_verdict: str
    previous_major_sha: str | None
    work_pending: bool
    reason: str

    def outputs(self) -> dict[str, str]:
        fields = asdict(self)
        return {
            key: (
                ""
                if value is None
                else str(value).lower()
                if isinstance(value, bool)
                else str(value)
            )
            for key, value in fields.items()
        }


def decide(observed: Observed) -> Plan:
    identity: ReleaseIdentity = release_identity(observed.version)
    exact = observed.tags.get(identity.exact_tag)
    major_name = identity.major_tag if observed.major_declared else None
    nothing = Plan(
        version=identity.version,
        exact_tag=identity.exact_tag,
        major_tag=major_name,
        is_release_commit=False,
        create_tag=False,
        create_release=False,
        upload_assets=False,
        advance_major=False,
        major_verdict="hold",
        previous_major_sha=None,
        work_pending=False,
        reason="",
    )

    # An exact tag elsewhere: this version was released from another commit.
    # Exact tags are immutable provenance, never moved; this push is simply
    # not a version bump.
    if exact is not None and exact != observed.head:
        return _with(
            nothing,
            reason=(
                f"{identity.exact_tag} already exists at {exact}; this push is not a "
                "new release"
            ),
        )

    if exact is None:
        released = [
            key
            for name, sha in observed.tags.items()
            if (key := exact_tag_version(name)) is not None
        ]
        latest = max(released, default=None)
        if latest is not None and not identity.key > latest:
            raise ReleaseRefused(
                f"version {identity.version} is not ahead of the latest release "
                f"v{'.'.join(map(str, latest))}"
            )

    create_tag = exact is None
    create_release = not observed.release_exists
    upload_assets = not observed.release_exists or SUMS not in observed.release_assets

    verdict = "hold"
    previous: str | None = None
    if observed.major_declared:
        current = observed.tags.get(identity.major_tag)
        previous = current
        if current is None:
            verdict = "create"
        elif current == observed.head:
            verdict = "hold"
        else:
            if observed.major_tag_version is None:
                raise ReleaseRefused(
                    f"cannot read the version at {identity.major_tag} ({current})"
                )
            prior = parse_version(observed.major_tag_version)
            if prior < identity.key:
                verdict = "advance"
            elif prior == identity.key:
                verdict = "hold"
            else:
                raise ReleaseRefused(
                    f"{identity.major_tag} is at {observed.major_tag_version}, ahead "
                    f"of {identity.version}; refusing to move it back"
                )
    advance_major = verdict in {"create", "advance"}
    outstanding = [
        label
        for label, flag in (
            ("exact tag", create_tag),
            ("GitHub Release", create_release),
            ("assets", upload_assets),
            ("major tag", advance_major),
        )
        if flag
    ]
    return Plan(
        version=identity.version,
        exact_tag=identity.exact_tag,
        major_tag=major_name,
        is_release_commit=True,
        create_tag=create_tag,
        create_release=create_release,
        upload_assets=upload_assets,
        advance_major=advance_major,
        major_verdict=verdict,
        previous_major_sha=previous if advance_major else None,
        work_pending=bool(outstanding),
        reason=("outstanding: " + ", ".join(outstanding))
        if outstanding
        else "the release is already complete at this commit",
    )


def _with(plan: Plan, *, reason: str) -> Plan:
    data = asdict(plan)
    data["reason"] = reason
    return Plan(**data)


def observe(
    workspace: pathlib.Path,
    config: ReleaseConfig,
    commit: str,
    remote: str,
    releases: GitHubReleases,
) -> Observed:
    version = read_version(
        config.version_authority,
        gitops.file_at(workspace, commit, config.version_authority),
    )
    identity = release_identity(version)
    tags = gitops.remote_tags(workspace, remote)
    major_version: str | None = None
    major_sha = tags.get(identity.major_tag) if config.major_tag else None
    if major_sha is not None and major_sha != commit:
        gitops.fetch_commit(workspace, remote, major_sha)
        major_version = read_version(
            config.version_authority,
            gitops.file_at(workspace, major_sha, config.version_authority),
        )
    exact = tags.get(identity.exact_tag)
    release = None
    if exact is None or exact == commit:
        release = releases.get_by_tag(identity.exact_tag)
    return Observed(
        head=commit,
        version=version,
        tags=tags,
        major_tag_version=major_version,
        release_exists=release is not None,
        release_assets=tuple(asset.name for asset in release.assets) if release else (),
        major_declared=config.major_tag,
    )


def plan(
    workspace: pathlib.Path,
    config: ReleaseConfig,
    commit: str,
    remote: str,
    releases: GitHubReleases,
) -> Plan:
    decided = decide(observe(workspace, config, commit, remote, releases))
    if decided.work_pending:
        # Release notes come from the dated CHANGELOG section at this commit.
        changelog_section(
            gitops.file_at(workspace, commit, config.changelog), decided.version
        )
    return decided
