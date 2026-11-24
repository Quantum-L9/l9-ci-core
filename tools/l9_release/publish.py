"""``publish`` and ``verify``.

``publish`` re-observes remote state (it never trusts an earlier plan), then
performs only the side effects still pending: the annotated exact tag at the
commit (create-only), the GitHub Release with notes from the dated CHANGELOG
section plus a provenance block, the missing assets and ``SHA256SUMS``, and —
only when declared and only forward — the maintained major tag. It ends by
running ``verify``.

``verify`` re-downloads every asset, checks each against ``SHA256SUMS`` (the
equivalent of ``sha256sum -c``, plus "no asset is missing and none is
extra"), and proves the exact tag peels to the commit.
"""

from __future__ import annotations

import json
import os
import pathlib
from dataclasses import dataclass

from . import gitops
from .build import BUILD_RECORD, BUILD_RECORD_SCHEMA, SUMS, sha256
from .config import ReleaseConfig
from .errors import ReleaseRefused
from .github import GitHubReleases
from .identity import changelog_section, parse_version, read_version, release_identity
from .identity_document import NAME as IDENTITY_NAME
from .identity_document import encode as encode_identity
from .identity_document import identity_document
from .identity_document import validate as validate_identity
from .plan import Plan
from .plan import plan as make_plan


def parse_sums(text: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        digest, separator, name = line.partition("  ")
        if (
            not separator
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
            or not name
            or "/" in name
            or name in entries
            or name == SUMS
        ):
            raise ReleaseRefused(f"{SUMS} line {number} is malformed: {line!r}")
        entries[name] = digest
    if not entries:
        raise ReleaseRefused(f"{SUMS} names no artifact")
    return entries


@dataclass(frozen=True)
class LocalBuild:
    sums_text: str
    sums: dict[str, str]
    record: dict[str, object]
    files: dict[str, bytes]


def load_build(dist: pathlib.Path, commit: str, version: str) -> LocalBuild:
    try:
        sums_text = (dist / SUMS).read_text(encoding="utf-8")
        record = json.loads((dist / BUILD_RECORD).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleaseRefused(f"{dist} is not a release build: {error}") from error
    sums = parse_sums(sums_text)
    if (
        not isinstance(record, dict)
        or record.get("schema") != BUILD_RECORD_SCHEMA
        or record.get("commit") != commit
        or record.get("version") != version
    ):
        raise ReleaseRefused(
            f"{dist / BUILD_RECORD} does not record a build of {version} at {commit}"
        )
    files: dict[str, bytes] = {}
    for name, digest in sums.items():
        try:
            data = (dist / name).read_bytes()
        except OSError as error:
            raise ReleaseRefused(f"{name} named by {SUMS} is missing: {error}")
        if sha256(data) != digest:
            raise ReleaseRefused(f"{name} does not match {SUMS}")
        files[name] = data
    extra = sorted(
        path.name
        for path in dist.iterdir()
        if path.is_file() and path.name not in {*sums, SUMS, BUILD_RECORD}
    )
    if extra:
        raise ReleaseRefused(f"{dist} holds files {SUMS} does not name: {extra}")
    return LocalBuild(sums_text=sums_text, sums=sums, record=record, files=files)


def release_notes(
    repository: str,
    commit: str,
    version: str,
    date: str,
    changelog_body: str,
    build: LocalBuild,
    config: ReleaseConfig,
) -> str:
    engine = os.environ.get("L9_RELEASE_ENGINE_REVISION", "") or "unrecorded"
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    run_id = os.environ.get("GITHUB_RUN_ID")
    run = f"{server}/{repository}/actions/runs/{run_id}" if run_id else "not a CI run"
    body = changelog_body.strip() or "_No release notes._"
    return (
        f"{body}\n\n---\n\n### Provenance\n\n"
        f"- Repository: `{repository}`\n"
        f"- Commit: `{commit}`\n"
        f"- Version: `{version}` from `{config.version_authority}` "
        f"(CHANGELOG dated {date})\n"
        f"- Build: `{config.adapter}`, two clean rounds byte-identical, "
        f"`SOURCE_DATE_EPOCH={build.record.get('source_date_epoch')}`\n"
        f"- Release engine: `Quantum-L9/l9-ci-core@{engine}`\n"
        f"- Workflow run: {run}\n\n"
        f"```text\n{build.sums_text}```\n"
    )


def publish(
    workspace: pathlib.Path,
    config: ReleaseConfig,
    commit: str,
    dist: pathlib.Path,
    remote: str,
    releases: GitHubReleases,
) -> Plan:
    commit = gitops.resolve_commit(workspace, commit)
    decided = make_plan(workspace, config, commit, remote, releases)
    if not decided.is_release_commit or not decided.work_pending:
        print(f"publish: nothing to do — {decided.reason}")
        if decided.is_release_commit:
            verify(workspace, config, commit, remote, releases, dist)
        return decided

    identity = release_identity(decided.version)
    section = changelog_section(
        gitops.file_at(workspace, commit, config.changelog), decided.version
    )
    local = load_build(dist, commit, decided.version)
    epoch = gitops.commit_epoch(workspace, commit)
    document = encode_identity(
        identity_document(
            releases.repository,
            decided.version,
            identity.exact_tag,
            identity.major_tag,
            commit,
            config.adapter,
            local.sums,
            epoch,
        )
    )

    if decided.create_tag:
        gitops.create_exact_tag(
            workspace,
            remote,
            identity.exact_tag,
            commit,
            f"{releases.repository} {identity.exact_tag}\n\n"
            f"Release {decided.version} ({section.date}) at {commit}.",
            epoch=epoch,
        )
        print(f"publish: created {identity.exact_tag} at {commit}")

    release = releases.get_by_tag(identity.exact_tag)
    if release is None:
        release = releases.create(
            identity.exact_tag,
            identity.exact_tag,
            release_notes(
                releases.repository,
                commit,
                decided.version,
                section.date,
                section.body,
                local,
                config,
            ),
        )
        print(f"publish: created GitHub Release {identity.exact_tag}")

    present = {asset.name for asset in release.assets}
    uploads = [*sorted(local.files), IDENTITY_NAME, SUMS]
    for name in uploads:
        if name in present:
            continue  # verify proves its bytes; an existing asset is never replaced
        if name == SUMS:
            data = local.sums_text.encode("utf-8")
        elif name == IDENTITY_NAME:
            data = document
        else:
            data = local.files[name]
        releases.upload_asset(release, name, data)
        print(f"publish: uploaded {name}")

    if decided.advance_major and decided.major_tag:
        gitops.write_major_tag(
            workspace, remote, decided.major_tag, commit, decided.previous_major_sha
        )
        print(f"publish: {decided.major_tag} -> {commit} ({decided.major_verdict})")

    verify(workspace, config, commit, remote, releases, dist, require_identity=True)
    return decided


def verify(
    workspace: pathlib.Path,
    config: ReleaseConfig,
    commit: str,
    remote: str,
    releases: GitHubReleases,
    dist: pathlib.Path | None = None,
    require_identity: bool = False,
) -> None:
    commit = gitops.resolve_commit(workspace, commit)
    version = read_version(
        config.version_authority,
        gitops.file_at(workspace, commit, config.version_authority),
    )
    identity = release_identity(version)
    tags = gitops.remote_tags(workspace, remote)
    target = tags.get(identity.exact_tag)
    if target != commit:
        raise ReleaseRefused(
            f"{identity.exact_tag} points at {target or 'nothing'}, not {commit}"
        )
    release = releases.get_by_tag(identity.exact_tag)
    if release is None:
        raise ReleaseRefused(f"no GitHub Release for {identity.exact_tag}")
    assets = {asset.name: asset for asset in release.assets}
    if SUMS not in assets:
        raise ReleaseRefused(f"release {identity.exact_tag} has no {SUMS} asset")
    sums_bytes = releases.download_asset(assets[SUMS])
    try:
        sums_text = sums_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ReleaseRefused(f"published {SUMS} is not UTF-8") from error
    sums = parse_sums(sums_text)
    if dist is not None and (dist / SUMS).is_file():
        if (dist / SUMS).read_text(encoding="utf-8") != sums_text:
            raise ReleaseRefused(f"published {SUMS} differs from the local build")
    extra = sorted(set(assets) - set(sums) - {SUMS, IDENTITY_NAME})
    missing = sorted(set(sums) - set(assets))
    if missing or extra:
        raise ReleaseRefused(
            f"release assets disagree with {SUMS}: missing {missing}, extra {extra}"
        )
    if IDENTITY_NAME in assets:
        try:
            document = json.loads(
                releases.download_asset(assets[IDENTITY_NAME]).decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ReleaseRefused(f"published {IDENTITY_NAME} is not JSON: {error}")
        validate_identity(
            document,
            repository=releases.repository,
            tag=identity.exact_tag,
            version=version,
            commit=commit,
            channel=identity.major_tag,
            sums=sums,
        )
        print(f"{IDENTITY_NAME}: OK")
    elif require_identity:
        raise ReleaseRefused(
            f"release {identity.exact_tag} has no {IDENTITY_NAME}; a release "
            "published by this engine always carries its identity document"
        )
    for name, digest in sorted(sums.items()):
        actual = sha256(releases.download_asset(assets[name]))
        if actual != digest:
            raise ReleaseRefused(
                f"{name}: FAILED (published {actual}, expected {digest})"
            )
        print(f"{name}: OK")
    if config.major_tag:
        major = tags.get(identity.major_tag)
        if major is None:
            raise ReleaseRefused(f"declared major tag {identity.major_tag} is absent")
        if major != commit:
            gitops.fetch_commit(workspace, remote, major)
            at_major = read_version(
                config.version_authority,
                gitops.file_at(workspace, major, config.version_authority),
            )
            if parse_version(at_major) < identity.key:
                raise ReleaseRefused(
                    f"{identity.major_tag} is at {at_major}, behind {version}"
                )
    print(f"verify: {identity.exact_tag} -> {commit}; {len(sums)} asset(s) verified")
