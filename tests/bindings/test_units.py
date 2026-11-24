"""Unit suite for the consumer-convergence engine.

Every test stubs the two boundaries that reach outside the process: ``_git``
(one ``ls-remote``) and the REST helpers. Everything else — schema
validation, lock reading, field rewriting, verdicts — runs for real against
temporary files.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from l9_bindings import apply as writer  # noqa: E402
from l9_bindings import converge, locks, model, remote  # noqa: E402

CONTRACT = {
    "schema": "l9.bindings/v1",
    "consumer": "Quantum-L9/Cursor-Governance",
    "bindings": [
        {
            "id": "memory",
            "producer": "Quantum-L9/l9-graphiti-memory",
            "channel": "v3",
            "class": "contract",
            "file": "ops/config/memory-binding.json",
            "artifact": "*.whl",
            "fields": {
                "version": [
                    "expected_package_version",
                    "release_evidence.package_version",
                ],
                "revision": "source.ref",
                "digest": "release_evidence.artifact_sha256",
                "revision_note": {
                    "path": "source.ref_note",
                    "template": "Annotated tag {tag} (object {object}) peels to {commit}.",
                },
            },
            "proof": ".github/workflows/351745802",
        }
    ],
}

LIBRARY = {
    "schema": "l9.bindings/v1",
    "consumer": "Quantum-L9/Cognitive.Engine.Graphs",
    "bindings": [
        {
            "id": "sdk",
            "producer": "Quantum-L9/Gate_SDK",
            "channel": "v1",
            "class": "library",
            "lock": "uv.lock",
            "package": "l9-ci-sdk",
            "refresh": "uv lock --upgrade-package {package}=={version}",
            "policy": {"auto_merge": True},
        }
    ],
}


class ModelTests(unittest.TestCase):
    def test_contract_binding_parses_with_its_defaults(self) -> None:
        bindings = model.parse(CONTRACT)
        binding = bindings.bindings[0]
        self.assertEqual(binding.klass, "contract")
        self.assertTrue(binding.policy.auto_pr)
        self.assertFalse(binding.policy.auto_merge)
        self.assertTrue(binding.policy.requires_proof)
        self.assertEqual(binding.fields["revision"].paths, ("source.ref",))
        self.assertEqual(
            binding.fields["digest"].paths,
            ("release_evidence.artifact_sha256",),
        )
        self.assertEqual(
            binding.fields["version"].paths,
            ("expected_package_version", "release_evidence.package_version"),
        )

    def test_unknown_key_fails_closed(self) -> None:
        document = json.loads(json.dumps(CONTRACT))
        document["bindings"][0]["auto_mrege"] = True
        with self.assertRaises(model.BindingsInvalid):
            model.parse(document)

    def test_contract_may_not_auto_merge(self) -> None:
        document = json.loads(json.dumps(CONTRACT))
        document["bindings"][0]["policy"] = {"auto_merge": True}
        with self.assertRaises(model.BindingsInvalid) as caught:
            model.parse(document)
        self.assertIn("never auto-merged", str(caught.exception))

    def test_digest_field_requires_an_artifact(self) -> None:
        document = json.loads(json.dumps(CONTRACT))
        del document["bindings"][0]["artifact"]
        with self.assertRaises(model.BindingsInvalid) as caught:
            model.parse(document)
        self.assertIn("artifact", str(caught.exception))

    def test_library_refresh_must_name_the_package_it_moves(self) -> None:
        document = json.loads(json.dumps(LIBRARY))
        document["bindings"][0]["refresh"] = "uv lock --upgrade"
        with self.assertRaises(model.BindingsInvalid) as caught:
            model.parse(document)
        self.assertIn("must name {package}", str(caught.exception))

    def test_a_refresh_that_follows_a_tag_need_not_name_the_version(self) -> None:
        # A consumer that follows a moving tag resolves the version *from* the
        # tag; converge verifies the lock afterwards instead of passing a
        # version the command could not accept.
        document = json.loads(json.dumps(LIBRARY))
        document["bindings"][0]["refresh"] = "uv lock --upgrade-package {package}"
        binding = model.parse(document).bindings[0]
        self.assertEqual(binding.refresh, "uv lock --upgrade-package {package}")

    def test_library_parses_and_keeps_auto_merge(self) -> None:
        binding = model.parse(LIBRARY).bindings[0]
        self.assertEqual(binding.klass, "library")
        self.assertTrue(binding.policy.auto_merge)
        self.assertFalse(binding.policy.requires_proof)

    def test_deployed_binding_may_not_open_a_pull_request(self) -> None:
        document = {
            "schema": "l9.bindings/v1",
            "consumer": "Quantum-L9/Cursor-Governance",
            "bindings": [
                {
                    "id": "c1",
                    "producer": "Quantum-L9/l9-graphiti-memory",
                    "channel": "v3",
                    "class": "deployed",
                    "environment": "c1",
                    "policy": {"auto_pr": True},
                }
            ],
        }
        with self.assertRaises(model.BindingsInvalid):
            model.parse(document)


class LockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = pathlib.Path(tempfile.mkdtemp())

    def test_uv_lock_reads_the_normalised_name(self) -> None:
        (self.directory / "uv.lock").write_text(
            '[[package]]\nname = "l9_ci_sdk"\nversion = "1.3.4"\n', encoding="utf-8"
        )
        self.assertEqual(locks.pinned(self.directory, "uv.lock", "l9-ci-sdk"), "1.3.4")

    def test_requirements_lock_reads_a_pin(self) -> None:
        (self.directory / "requirements.lock").write_text(
            "# generated\nl9-ci-sdk==1.3.4 \\\n    --hash=sha256:00\n", encoding="utf-8"
        )
        self.assertEqual(
            locks.pinned(self.directory, "requirements.lock", "L9_CI_SDK"), "1.3.4"
        )

    def test_unreadable_lock_raises_rather_than_reporting_converged(self) -> None:
        (self.directory / "uv.lock").write_text("not toml [", encoding="utf-8")
        with self.assertRaises(locks.LockUnreadable):
            locks.pinned(self.directory, "uv.lock", "l9-ci-sdk")


class ApplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = pathlib.Path(tempfile.mkdtemp())
        self.path = self.directory / "ops/config/memory-binding.json"
        self.path.parent.mkdir(parents=True)
        self.path.write_text(
            json.dumps(
                {
                    "expected_package_version": "3.0.1",
                    "source": {"ref": "96ed99eb", "ref_note": "old"},
                    "release_evidence": {
                        "artifact_sha256": "4822",
                        "package_version": "3.0.1",
                    },
                    "invariant": "must survive",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def test_rewrite_touches_only_the_declared_fields(self) -> None:
        binding = model.parse(CONTRACT).bindings[0]
        before, after = writer.rewrite(
            self.directory,
            binding,
            {
                "version": "3.0.2",
                "revision": "a" * 40,
                "digest": "b" * 64,
                "tag": "v3.0.2",
                "object": "c" * 40,
                "commit": "a" * 40,
                "channel": "v3",
            },
        )
        document = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(before["version"], "3.0.1")
        self.assertEqual(after["version"], "3.0.2")
        self.assertEqual(document["expected_package_version"], "3.0.2")
        self.assertEqual(document["release_evidence"]["package_version"], "3.0.2")
        self.assertEqual(document["source"]["ref"], "a" * 40)
        self.assertEqual(document["invariant"], "must survive")
        self.assertEqual(
            document["source"]["ref_note"],
            f"Annotated tag v3.0.2 (object {'c' * 40}) peels to {'a' * 40}.",
        )

    def test_missing_path_is_a_hard_error_not_a_new_field(self) -> None:
        binding = model.parse(CONTRACT).bindings[0]
        document = json.loads(self.path.read_text(encoding="utf-8"))
        del document["source"]["ref"]
        self.path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaises(writer.ApplyError):
            writer.rewrite(
                self.directory,
                binding,
                {"version": "3.0.2", "revision": "a", "digest": "b"},
            )

    def test_digest_for_requires_one_match(self) -> None:
        binding = model.parse(CONTRACT).bindings[0]
        self.assertEqual(
            writer.digest_for(binding, {"x.whl": "1", "x.tar.gz": "2"}), "1"
        )
        with self.assertRaises(writer.ApplyError):
            writer.digest_for(binding, {"x.tar.gz": "2"})


class RemoteTests(unittest.TestCase):
    def _fake(self, returncode: int, stdout: str = ""):
        return subprocess.CompletedProcess(
            args=["git"], returncode=returncode, stdout=stdout, stderr=""
        )

    def test_tag_state_is_tri_state(self) -> None:
        with mock.patch.object(remote, "_git", return_value=self._fake(0)):
            self.assertIs(remote.tag_state("o/r", "v1"), remote.RemoteState.PRESENT)
        with mock.patch.object(remote, "_git", return_value=self._fake(2)):
            self.assertIs(remote.tag_state("o/r", "v1"), remote.RemoteState.ABSENT)
        with mock.patch.object(remote, "_git", return_value=self._fake(128)):
            with self.assertRaises(remote.RemoteError):
                remote.tag_state("o/r", "v1")

    def test_channel_head_peels_an_annotated_tag(self) -> None:
        output = (
            "a" * 40
            + "\trefs/tags/v3.0.1\n"
            + "b" * 40
            + "\trefs/tags/v3.0.1^{}\n"
            + "c" * 40
            + "\trefs/tags/v3.0.2\n"
            + "d" * 40
            + "\trefs/tags/v3.0.2^{}\n"
            + "e" * 40
            + "\trefs/tags/v3.9.0-rc1\n"
        )
        with mock.patch.object(remote, "_git", return_value=self._fake(0, output)):
            head = remote.channel_head("o/r", "v3")
        assert head is not None
        self.assertEqual(head.tag, "v3.0.2")
        self.assertEqual(head.commit, "d" * 40)
        self.assertEqual(head.object_sha, "c" * 40)

    def test_empty_channel_is_none(self) -> None:
        with mock.patch.object(remote, "_git", return_value=self._fake(0, "")):
            self.assertIsNone(remote.channel_head("o/r", "v3"))

    def test_identity_document_disagreeing_with_the_tag_is_refused(self) -> None:
        head = remote.ChannelHead("v3", "v3.0.2", "3.0.2", "d" * 40, None)
        release = {"html_url": "u", "assets": []}
        document = json.dumps(
            {
                "schema": "l9.release-identity/v1",
                "repository": "o/r",
                "tag": "v3.0.2",
                "version": "3.0.2",
                "commit": "f" * 40,
                "channel": "v3",
                "files": {"x.whl": "1"},
            }
        ).encode()
        with (
            mock.patch.object(remote, "_release", return_value=release),
            mock.patch.object(remote, "_asset", return_value=document),
        ):
            with self.assertRaises(remote.RemoteError):
                remote.fetch_identity("o/r", head)

    def test_missing_release_derives_a_tag_only_identity(self) -> None:
        head = remote.ChannelHead("v3", "v3.0.2", "3.0.2", "d" * 40, None)
        with mock.patch.object(remote, "_release", return_value=None):
            identity = remote.fetch_identity("o/r", head)
        self.assertEqual(identity.source, "tag-only")
        self.assertEqual(identity.files, {})


class VerdictTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = pathlib.Path(tempfile.mkdtemp())
        self.head = remote.ChannelHead("v3", "v3.0.2", "3.0.2", "d" * 40, None)
        self.identity = remote.ReleaseIdentity(
            "o/r", "v3", "v3.0.2", "3.0.2", "d" * 40, {"x.whl": "1"}, "document", None
        )

    def test_library_drift_is_reported_against_the_channel_head(self) -> None:
        (self.directory / "uv.lock").write_text(
            '[[package]]\nname = "l9-ci-sdk"\nversion = "1.3.3"\n', encoding="utf-8"
        )
        binding = model.parse(LIBRARY).bindings[0]
        with (
            mock.patch.object(converge, "channel_head", return_value=self.head),
            mock.patch.object(converge, "fetch_identity", return_value=self.identity),
        ):
            verdict = converge.evaluate(binding, self.directory, None)
        self.assertEqual(verdict.state, converge.DRIFTED)
        self.assertEqual(verdict.current, "1.3.3")
        self.assertEqual(verdict.target, "3.0.2")

    def test_unreachable_producer_blocks_and_says_why(self) -> None:
        binding = model.parse(LIBRARY).bindings[0]
        with mock.patch.object(
            converge, "channel_head", side_effect=remote.RemoteError("boom")
        ):
            verdict = converge.evaluate(binding, self.directory, None)
        self.assertEqual(verdict.state, converge.BLOCKED)
        self.assertIn("boom", verdict.detail)

    def test_contract_converges_on_version_and_revision(self) -> None:
        path = self.directory / "ops/config/memory-binding.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "expected_package_version": "3.0.2",
                    "source": {"ref": "d" * 40},
                    "release_evidence": {
                        "artifact_sha256": "1",
                        "package_version": "3.0.2",
                    },
                }
            ),
            encoding="utf-8",
        )
        binding = model.parse(CONTRACT).bindings[0]
        with (
            mock.patch.object(converge, "channel_head", return_value=self.head),
            mock.patch.object(converge, "fetch_identity", return_value=self.identity),
        ):
            verdict = converge.evaluate(binding, self.directory, None)
        self.assertEqual(verdict.state, converge.CONVERGED)


if __name__ == "__main__":
    unittest.main()
