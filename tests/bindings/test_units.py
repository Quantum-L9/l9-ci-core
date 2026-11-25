"""Unit suite for the consumer-convergence engine.

Every test stubs the two boundaries that reach outside the process: ``_git``
(one ``ls-remote``) and the REST helpers. Everything else — schema
validation, lock reading, field rewriting, verdicts — runs for real against
temporary files.
"""

from __future__ import annotations

import datetime
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
from l9_bindings import converge, dispatch, locks, model, remote  # noqa: E402

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

    def test_an_archive_pin_is_read_as_the_commit_it_resolved_to(self) -> None:
        # A hash-locked requirements file records the object a git channel
        # resolved to, because pip cannot hash-verify a ref.
        (self.directory / "requirements.lock").write_text(
            "l9-ci-sdk @ https://github.com/o/r/archive/"
            + "a" * 40
            + ".tar.gz \\\n    --hash=sha256:"
            + "0" * 64
            + "\n",
            encoding="utf-8",
        )
        self.assertEqual(
            locks.pinned(self.directory, "requirements.lock", "l9-ci-sdk"),
            "a" * 40,
        )


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

    def test_a_lock_that_pins_the_release_commit_is_converged(self) -> None:
        document = json.loads(json.dumps(LIBRARY))
        document["bindings"][0]["lock"] = "requirements.lock"
        (self.directory / "requirements.lock").write_text(
            "l9-ci-sdk @ https://github.com/o/r/archive/" + "d" * 40 + ".tar.gz\n",
            encoding="utf-8",
        )
        binding = model.parse(document).bindings[0]
        with (
            mock.patch.object(converge, "channel_head", return_value=self.head),
            mock.patch.object(converge, "fetch_identity", return_value=self.identity),
        ):
            verdict = converge.evaluate(binding, self.directory, None)
        self.assertEqual(verdict.state, converge.CONVERGED)
        self.assertEqual(verdict.current, "d" * 40)

    def test_a_lock_pinned_to_another_commit_drifts(self) -> None:
        document = json.loads(json.dumps(LIBRARY))
        document["bindings"][0]["lock"] = "requirements.lock"
        (self.directory / "requirements.lock").write_text(
            "l9-ci-sdk @ https://github.com/o/r/archive/" + "e" * 40 + ".tar.gz\n",
            encoding="utf-8",
        )
        binding = model.parse(document).bindings[0]
        with (
            mock.patch.object(converge, "channel_head", return_value=self.head),
            mock.patch.object(converge, "fetch_identity", return_value=self.identity),
        ):
            verdict = converge.evaluate(binding, self.directory, None)
        self.assertEqual(verdict.state, converge.DRIFTED)


DEPLOYED = {
    "schema": "l9.bindings/v1",
    "consumer": "Quantum-L9/Cursor-Governance",
    "bindings": [
        {
            "id": "c1",
            "producer": "Quantum-L9/l9-graphiti-memory",
            "channel": "v3",
            "class": "deployed",
            "environment": "c1",
        }
    ],
}
COMMIT = "d" * 40
IMAGE = "ghcr.io/quantum-l9/l9-graphiti-memory"
IMAGE_DIGEST = "sha256:" + "a" * 64


def deployment_request(**overrides: object) -> dict[str, object]:
    """A request shaped exactly like ``container-release`` publishes one."""

    document: dict[str, object] = {
        "schema": "l9.deployment-request/v2",
        "request_id": "6f1e2d3c-4b5a-4978-8796-a5b4c3d2e1f0",
        "idempotency_key": f"release:Quantum-L9/l9-graphiti-memory:{COMMIT}:c1-staging",
        "source": {
            "repository": "Quantum-L9/l9-graphiti-memory",
            "commit_sha": COMMIT,
            "ref": "refs/tags/v3.0.2",
            "run_id": 44388642735,
        },
        "artifact": {
            "image": IMAGE,
            "digest": IMAGE_DIGEST,
            "image_ref": f"{IMAGE}@{IMAGE_DIGEST}",
            "architecture": "linux/amd64",
        },
        "profile": {"path": ".l9/deployment.yaml", "digest": "sha256:" + "b" * 64},
        "evidence": {
            "schema": "l9.release-evidence-reference/v1",
            "schema_version": "2.0.0",
            "sdk_version": "1.4.0",
            "bundle_digest": "sha256:" + "c" * 64,
            "workflow_run_id": 44388642735,
            "artifact_name": "release-evidence",
            "bundle_path": "finding-bundle.json",
            "provenance_reference": f"oci://{IMAGE}@{IMAGE_DIGEST}#provenance",
            "sbom_reference": f"oci://{IMAGE}@{IMAGE_DIGEST}#sbom",
            "gate_binding_digest": "sha256:" + "e" * 64,
            "gate_binding_path": "ci-gate-binding.json",
            "artifact_binding_digest": "sha256:" + "f" * 64,
            "artifact_binding_path": "release-artifact-binding.json",
        },
        "target": {"environment": "c1-staging"},
        "requested_by": "release-workflow",
        "requested_at": "2026-11-24T00:00:00Z",
    }
    document.update(overrides)
    return document


class DeploymentRequestTests(unittest.TestCase):
    """``l9.deployment-request/v2``: proved against the release, then re-bound."""

    def test_a_request_for_the_release_validates(self) -> None:
        document = deployment_request()
        self.assertEqual(
            document,
            dispatch.validate(
                document,
                producer="Quantum-L9/l9-graphiti-memory",
                commit=COMMIT,
            ),
        )

    def test_a_request_about_another_commit_is_refused(self) -> None:
        with self.assertRaisesRegex(dispatch.DispatchError, "commit_sha"):
            dispatch.validate(
                deployment_request(),
                producer="Quantum-L9/l9-graphiti-memory",
                commit="e" * 40,
            )

    def test_a_request_from_another_repository_is_refused(self) -> None:
        with self.assertRaisesRegex(dispatch.DispatchError, "source.repository"):
            dispatch.validate(
                deployment_request(),
                producer="Quantum-L9/Constellation.Gate",
                commit=COMMIT,
            )

    def test_an_unknown_field_is_refused(self) -> None:
        document = deployment_request(extra="not in the contract")
        with self.assertRaisesRegex(dispatch.DispatchError, "unknown field"):
            dispatch.validate(
                document,
                producer="Quantum-L9/l9-graphiti-memory",
                commit=COMMIT,
            )

    def test_an_image_ref_that_disagrees_with_image_and_digest_is_refused(self) -> None:
        document = json.loads(json.dumps(deployment_request()))
        document["artifact"]["image_ref"] = f"{IMAGE}@sha256:" + "0" * 64
        with self.assertRaisesRegex(dispatch.DispatchError, "image_ref"):
            dispatch.validate(
                document,
                producer="Quantum-L9/l9-graphiti-memory",
                commit=COMMIT,
            )

    def test_a_request_without_evidence_is_refused(self) -> None:
        document = json.loads(json.dumps(deployment_request()))
        del document["evidence"]
        with self.assertRaisesRegex(dispatch.DispatchError, "missing field"):
            dispatch.validate(
                document,
                producer="Quantum-L9/l9-graphiti-memory",
                commit=COMMIT,
            )

    def test_bind_rebinds_identity_and_environment(self) -> None:
        binding = model.parse(DEPLOYED).bindings[0]
        payload = dispatch.bind(
            deployment_request(),
            consumer="Quantum-L9/Cursor-Governance",
            binding=binding,
            producer=binding.producer,
            commit=COMMIT,
            now=datetime.datetime(2026, 11, 24, 12, 0, tzinfo=datetime.UTC),
        )
        self.assertEqual({"environment": "c1"}, payload["target"])
        self.assertEqual(
            f"release:{binding.producer}:{COMMIT}:c1", payload["idempotency_key"]
        )
        self.assertEqual("Quantum-L9/Cursor-Governance (c1)", payload["requested_by"])
        self.assertEqual("2026-11-24T12:00:00Z", payload["requested_at"])
        # Everything the producer proved is copied, not recomputed.
        self.assertEqual(deployment_request()["artifact"], payload["artifact"])
        self.assertEqual(deployment_request()["evidence"], payload["evidence"])

    def test_two_environments_are_two_distinct_requests(self) -> None:
        document = json.loads(json.dumps(DEPLOYED))
        document["bindings"].append(
            {
                "id": "c1-staging",
                "producer": "Quantum-L9/l9-graphiti-memory",
                "channel": "v3",
                "class": "deployed",
                "environment": "c1-staging",
            }
        )
        bindings = model.parse(document)
        first, second = (
            dispatch.bind(
                deployment_request(),
                consumer=bindings.consumer,
                binding=binding,
                producer=binding.producer,
                commit=COMMIT,
            )
            for binding in bindings.bindings
        )
        self.assertNotEqual(first["request_id"], second["request_id"])
        self.assertNotEqual(first["idempotency_key"], second["idempotency_key"])

    def test_emit_dispatches_the_rebound_payload(self) -> None:
        binding = model.parse(DEPLOYED).bindings[0]
        with mock.patch.object(dispatch, "_post") as post:
            payload = dispatch.emit(
                deployment_request(),
                consumer="Quantum-L9/Cursor-Governance",
                binding=binding,
                producer=binding.producer,
                commit=COMMIT,
                token="t",
            )
        url, token, body = post.call_args.args
        self.assertEqual(
            "https://api.github.com/repos/Quantum-L9/l9-deploy/dispatches", url
        )
        self.assertEqual("t", token)
        self.assertEqual("l9.release.requested.v2", body["event_type"])
        self.assertEqual(payload, body["client_payload"])
        self.assertEqual("c1", body["client_payload"]["target"]["environment"])

    def test_emit_refuses_a_request_it_cannot_prove(self) -> None:
        binding = model.parse(DEPLOYED).bindings[0]
        with mock.patch.object(dispatch, "_post") as post:
            with self.assertRaises(dispatch.DispatchError):
                dispatch.emit(
                    deployment_request(),
                    consumer="Quantum-L9/Cursor-Governance",
                    binding=binding,
                    producer=binding.producer,
                    commit="e" * 40,
                    token="t",
                )
        post.assert_not_called()


class DeployedConvergeTests(unittest.TestCase):
    """A deployed binding converges by dispatching, and only when asked."""

    def setUp(self) -> None:
        self.directory = pathlib.Path(tempfile.mkdtemp())
        self.head = remote.ChannelHead("v3", "v3.0.2", "3.0.2", COMMIT, None)
        self.identity = remote.ReleaseIdentity(
            "Quantum-L9/l9-graphiti-memory",
            "v3",
            "v3.0.2",
            "3.0.2",
            COMMIT,
            {"x.whl": "1"},
            "document",
            None,
        )
        self.bindings = model.parse(DEPLOYED)

    def evaluate(self) -> converge.Verdict:
        with (
            mock.patch.object(converge, "channel_head", return_value=self.head),
            mock.patch.object(converge, "fetch_identity", return_value=self.identity),
        ):
            return converge.evaluate(self.bindings.bindings[0], self.directory, "t")

    def test_a_release_without_a_published_request_stays_delegated(self) -> None:
        with mock.patch.object(remote, "release_asset", return_value=None):
            verdict = self.evaluate()
        self.assertEqual(converge.DELEGATED, verdict.state)
        self.assertIsNone(verdict.request)
        self.assertIn("publishes no deployment-request.json", verdict.detail)

    def test_a_published_request_is_proved_before_it_can_be_dispatched(self) -> None:
        body = json.dumps(deployment_request()).encode()
        with mock.patch.object(remote, "release_asset", return_value=body):
            verdict = self.evaluate()
        self.assertEqual(converge.DELEGATED, verdict.state)
        assert verdict.request is not None
        self.assertEqual("c1-staging", verdict.request["target"]["environment"])

    def test_an_invalid_published_request_blocks(self) -> None:
        body = json.dumps(deployment_request(source={"repository": "o/r"})).encode()
        with mock.patch.object(remote, "release_asset", return_value=body):
            verdict = self.evaluate()
        self.assertEqual(converge.BLOCKED, verdict.state)
        self.assertIn("source", verdict.detail)

    def test_plan_never_dispatches(self) -> None:
        body = json.dumps(deployment_request()).encode()
        with (
            mock.patch.object(converge, "channel_head", return_value=self.head),
            mock.patch.object(converge, "fetch_identity", return_value=self.identity),
            mock.patch.object(remote, "release_asset", return_value=body),
            mock.patch.object(dispatch, "_post") as post,
        ):
            verdicts = converge.converge(
                self.bindings, self.directory, "t", dry_run=True, dispatch=True
            )
        post.assert_not_called()
        self.assertEqual(converge.DELEGATED, verdicts[0].state)

    def test_converge_dispatches_the_declared_environment(self) -> None:
        body = json.dumps(deployment_request()).encode()
        with (
            mock.patch.object(converge, "channel_head", return_value=self.head),
            mock.patch.object(converge, "fetch_identity", return_value=self.identity),
            mock.patch.object(remote, "release_asset", return_value=body),
            mock.patch.object(dispatch, "_post") as post,
        ):
            verdicts = converge.converge(
                self.bindings, self.directory, "t", dry_run=False, dispatch=True
            )
        self.assertEqual(converge.REQUESTED, verdicts[0].state)
        payload = post.call_args.args[2]["client_payload"]
        self.assertEqual({"environment": "c1"}, payload["target"])
        self.assertEqual(COMMIT, payload["source"]["commit_sha"])

    def test_converge_without_dispatch_emits_nothing(self) -> None:
        body = json.dumps(deployment_request()).encode()
        with (
            mock.patch.object(converge, "channel_head", return_value=self.head),
            mock.patch.object(converge, "fetch_identity", return_value=self.identity),
            mock.patch.object(remote, "release_asset", return_value=body),
            mock.patch.object(dispatch, "_post") as post,
        ):
            verdicts = converge.converge(
                self.bindings, self.directory, "t", dry_run=False
            )
        post.assert_not_called()
        self.assertEqual(converge.DELEGATED, verdicts[0].state)

    def test_a_failed_dispatch_is_blocked_not_silent(self) -> None:
        body = json.dumps(deployment_request()).encode()
        with (
            mock.patch.object(converge, "channel_head", return_value=self.head),
            mock.patch.object(converge, "fetch_identity", return_value=self.identity),
            mock.patch.object(remote, "release_asset", return_value=body),
            mock.patch.object(
                dispatch, "_post", side_effect=dispatch.DispatchError("HTTP 403")
            ),
        ):
            verdicts = converge.converge(
                self.bindings, self.directory, "t", dry_run=False, dispatch=True
            )
        self.assertEqual(converge.BLOCKED, verdicts[0].state)
        self.assertIn("HTTP 403", verdicts[0].detail)


if __name__ == "__main__":
    unittest.main()
