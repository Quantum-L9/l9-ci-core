"""Unit tests: release declaration, identity, the pure plan, sdist normalisation."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import pathlib
import sys
import tarfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from support import ROOT  # noqa: E402

from l9_release.build import normalise_sdist, sums_text
from l9_release.config import (
    ADAPTER_VERSION_AUTHORITY,
    FIELDS,
    SCHEMA_PATH,
    parse_config,
    refuse_core_repository,
)
from l9_release.errors import ReleaseRefused
from l9_release.identity import changelog_section, read_version, release_identity
from l9_release.plan import Observed, decide
from l9_release.publish import parse_sums

HEAD = "a" * 40
OTHER = "b" * 40
VALID = {
    "schema": "l9.release/v1",
    "adapter": "python-uv",
    "version_authority": "pyproject.toml",
    "changelog": "CHANGELOG.md",
    "major_tag": False,
}


class ConfigTests(unittest.TestCase):
    def test_valid_declaration(self) -> None:
        config = parse_config(dict(VALID))
        self.assertEqual(
            ("python-uv", "pyproject.toml", "CHANGELOG.md", False),
            (
                config.adapter,
                config.version_authority,
                config.changelog,
                config.major_tag,
            ),
        )

    def test_unknown_field_fails_closed(self) -> None:
        with self.assertRaisesRegex(ReleaseRefused, "unknown field"):
            parse_config({**VALID, "token": "x"})

    def test_missing_field_fails_closed(self) -> None:
        document = dict(VALID)
        del document["major_tag"]
        with self.assertRaisesRegex(ReleaseRefused, "missing field"):
            parse_config(document)

    def test_version_authority_must_match_adapter(self) -> None:
        with self.assertRaisesRegex(ReleaseRefused, "reads its version from"):
            parse_config({**VALID, "version_authority": "package.json"})

    def test_rejects_other_schema_and_bad_types(self) -> None:
        for change in (
            {"schema": "l9.release/v2"},
            {"adapter": "cargo"},
            {"major_tag": "yes"},
            {"changelog": "../CHANGELOG.md"},
            {"changelog": "/etc/passwd"},
        ):
            with self.subTest(change=change), self.assertRaises(ReleaseRefused):
                parse_config({**VALID, **change})

    def test_published_schema_agrees_with_validator(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual("l9.release/v1", schema["title"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(FIELDS), set(schema["required"]))
        self.assertEqual(set(FIELDS), set(schema["properties"]))
        self.assertEqual(
            set(ADAPTER_VERSION_AUTHORITY), set(schema["properties"]["adapter"]["enum"])
        )
        pairs = {
            rule["if"]["properties"]["adapter"]["const"]: rule["then"]["properties"][
                "version_authority"
            ]["const"]
            for rule in schema["allOf"]
        }
        self.assertEqual(ADAPTER_VERSION_AUTHORITY, pairs)

    def test_core_itself_is_refused(self) -> None:
        with self.assertRaisesRegex(ReleaseRefused, "tag-and-release.sh"):
            refuse_core_repository("Quantum-L9/l9-ci-core")
        refuse_core_repository("Quantum-L9/l9-meta-injector")

    def test_examples_are_valid(self) -> None:
        examples = sorted(
            (ROOT / "docs" / "release" / "examples").glob("*.release.json")
        )
        self.assertGreaterEqual(len(examples), 2)
        for path in examples:
            with self.subTest(example=path.name):
                parse_config(json.loads(path.read_text(encoding="utf-8")))


class IdentityTests(unittest.TestCase):
    def test_strict_semver_core(self) -> None:
        identity = release_identity("4.4.0")
        self.assertEqual(
            ("v4.4.0", "v4", 4),
            (identity.exact_tag, identity.major_tag, identity.major),
        )
        for bad in ("4.02.0", "04.2.0", "4.2", "4.2.0-rc.1", "4.2.0+b", "v4.2.0"):
            with self.subTest(version=bad), self.assertRaises(ReleaseRefused):
                release_identity(bad)

    def test_version_authorities(self) -> None:
        self.assertEqual(
            "3.0.0",
            read_version(
                "pyproject.toml", '[project]\nname = "x"\nversion = "3.0.0"\n'
            ),
        )
        self.assertEqual("4.4.0", read_version("package.json", '{"version": "4.4.0"}'))
        with self.assertRaisesRegex(ReleaseRefused, "dynamic"):
            read_version(
                "pyproject.toml", '[project]\nname = "x"\ndynamic = ["version"]\n'
            )
        with self.assertRaises(ReleaseRefused):
            read_version("package.json", "{}")

    def test_changelog_requires_dated_section(self) -> None:
        text = (
            "# Changelog\n\n## Unreleased\n\n- next\n\n## 4.4.0 - 2026-11-14\n\n"
            "Body line.\n\n## 4.3.0 - 2026-11-03\n\n- old\n"
        )
        section = changelog_section(text, "4.4.0")
        self.assertEqual(("2026-11-14", "Body line."), (section.date, section.body))
        self.assertEqual(
            "2026-11-03", changelog_section("## [4.3.0] - 2026-11-03\n", "4.3.0").date
        )
        for bad in ("## 4.4.0\n", "## 4.4.0 - soon\n", "## 4.4.0 - 2026-13-40\n", ""):
            with self.subTest(text=bad), self.assertRaises(ReleaseRefused):
                changelog_section(bad, "4.4.0")


def observed(**changes: object) -> Observed:
    base: dict[str, object] = {
        "head": HEAD,
        "version": "1.2.0",
        "tags": {"v1.1.0": OTHER},
        "major_tag_version": None,
        "release_exists": False,
        "release_assets": (),
        "major_declared": False,
    }
    base.update(changes)
    return Observed(**base)  # type: ignore[arg-type]


class PlanDecisionTests(unittest.TestCase):
    def test_new_release_has_every_side_effect_pending(self) -> None:
        plan = decide(observed())
        self.assertTrue(plan.is_release_commit)
        self.assertEqual(
            (True, True, True, False),
            (
                plan.create_tag,
                plan.create_release,
                plan.upload_assets,
                plan.advance_major,
            ),
        )
        self.assertTrue(plan.work_pending)

    def test_non_bump_push_is_a_no_op(self) -> None:
        plan = decide(observed(version="1.1.0"))
        self.assertFalse(plan.is_release_commit)
        self.assertFalse(plan.work_pending)

    def test_version_not_ahead_is_refused(self) -> None:
        for version in ("1.0.9", "0.9.0"):
            with (
                self.subTest(version=version),
                self.assertRaisesRegex(ReleaseRefused, "not ahead"),
            ):
                decide(observed(version=version))

    def test_partial_publication_finishes_only_what_is_missing(self) -> None:
        tag_only = decide(observed(tags={"v1.1.0": OTHER, "v1.2.0": HEAD}))
        self.assertEqual(
            (False, True, True),
            (tag_only.create_tag, tag_only.create_release, tag_only.upload_assets),
        )
        sums_missing = decide(
            observed(
                tags={"v1.2.0": HEAD}, release_exists=True, release_assets=("x.whl",)
            )
        )
        self.assertEqual(
            (False, False, True),
            (
                sums_missing.create_tag,
                sums_missing.create_release,
                sums_missing.upload_assets,
            ),
        )
        complete = decide(
            observed(
                tags={"v1.2.0": HEAD},
                release_exists=True,
                release_assets=("x.whl", "SHA256SUMS"),
            )
        )
        self.assertTrue(complete.is_release_commit)
        self.assertFalse(complete.work_pending)

    def test_major_tag_create_advance_hold_regress(self) -> None:
        created = decide(observed(major_declared=True))
        self.assertEqual(
            ("create", True, None),
            (created.major_verdict, created.advance_major, created.previous_major_sha),
        )
        advanced = decide(
            observed(
                major_declared=True,
                tags={"v1.1.0": OTHER, "v1": OTHER},
                major_tag_version="1.1.0",
            )
        )
        self.assertEqual(
            ("advance", OTHER), (advanced.major_verdict, advanced.previous_major_sha)
        )
        held = decide(
            observed(
                major_declared=True,
                tags={"v1.2.0": HEAD, "v1": HEAD},
                release_exists=True,
                release_assets=("SHA256SUMS",),
            )
        )
        self.assertEqual(("hold", False), (held.major_verdict, held.work_pending))
        with self.assertRaisesRegex(ReleaseRefused, "refusing to move it back"):
            decide(
                observed(
                    major_declared=True,
                    tags={"v1.1.0": OTHER, "v1": OTHER},
                    major_tag_version="1.3.0",
                )
            )

    def test_undeclared_major_is_never_touched(self) -> None:
        plan = decide(observed(tags={"v1.1.0": OTHER, "v1": OTHER}))
        self.assertEqual((None, False), (plan.major_tag, plan.advance_major))


def sdist(order: list[str], mtime: int, uid: int, gzip_mtime: int) -> bytes:
    contents = {
        "pkg-1.0/PKG-INFO": b"Name: pkg\n",
        "pkg-1.0/a.py": b"A = 1\n",
        "pkg-1.0/sub/b.py": b"B = 2\n",
    }
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for name in order:
            info = tarfile.TarInfo(name)
            info.size = len(contents[name])
            info.mtime, info.uid, info.uname, info.mode = mtime, uid, "someone", 0o644
            tar.addfile(info, io.BytesIO(contents[name]))
    out = io.BytesIO()
    with gzip.GzipFile(
        filename="pkg-1.0.tar", mode="wb", fileobj=out, mtime=gzip_mtime
    ) as stream:
        stream.write(raw.getvalue())
    return out.getvalue()


class NormalisationTests(unittest.TestCase):
    NAMES = ["pkg-1.0/PKG-INFO", "pkg-1.0/a.py", "pkg-1.0/sub/b.py"]

    def test_two_divergent_envelopes_normalise_to_identical_bytes(self) -> None:
        first = sdist(self.NAMES, 1_700_000_000, 1000, 1_700_000_001)
        second = sdist(list(reversed(self.NAMES)), 1_800_000_000, 2000, 1_800_000_002)
        self.assertNotEqual(first, second)
        epoch = 1_794_678_803
        self.assertEqual(normalise_sdist(first, epoch), normalise_sdist(second, epoch))

    def test_envelope_fields_and_member_content(self) -> None:
        epoch = 1_794_678_803
        result = normalise_sdist(sdist(list(reversed(self.NAMES)), 5, 7, 9), epoch)
        self.assertEqual(b"\x1f\x8b\x08\x00", result[:4])  # no FNAME flag
        self.assertEqual(b"\x00\x00\x00\x00", result[4:8])  # gzip mtime 0
        with tarfile.open(fileobj=io.BytesIO(result), mode="r:gz") as tar:
            members = tar.getmembers()
            self.assertEqual(self.NAMES, [member.name for member in members])
            for member in members:
                self.assertEqual(
                    (epoch, 0, 0, "", ""),
                    (member.mtime, member.uid, member.gid, member.uname, member.gname),
                )
                self.assertEqual({}, member.pax_headers)
            stream = tar.extractfile("pkg-1.0/sub/b.py")
            assert stream is not None
            self.assertEqual(b"B = 2\n", stream.read())

    def test_unreadable_sdist_is_refused(self) -> None:
        with self.assertRaises(ReleaseRefused):
            normalise_sdist(b"not a tarball", 0)


class SumsTests(unittest.TestCase):
    def test_round_trip_and_rejections(self) -> None:
        files = {"b.whl": b"b", "a.tar.gz": b"a"}
        text = sums_text(files)
        self.assertEqual(text.splitlines()[0].split("  ")[1], "a.tar.gz")
        self.assertEqual(
            {n: hashlib.sha256(d).hexdigest() for n, d in files.items()},
            parse_sums(text),
        )
        for bad in (
            "",
            "abc  x\n",
            f"{'0' * 64} x\n",
            f"{'0' * 64}  dir/x\n",
            f"{'0' * 64}  SHA256SUMS\n",
        ):
            with self.subTest(text=bad), self.assertRaises(ReleaseRefused):
                parse_sums(bad)


if __name__ == "__main__":
    unittest.main()
