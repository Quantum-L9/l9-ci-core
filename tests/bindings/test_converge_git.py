"""Integration suite for the converge write path.

``test_units.py`` stops at a verdict. This suite runs ``converge`` end to end
against a real repository with a real bare ``origin``, so the branch, the
commit, the push and the pull-request call are exercised the way
``binding-converge.yml`` exercises them. Only the two remote reads
(``channel_head``, ``fetch_identity``) and the REST calls (``_pr``,
``_auto_merge``, ``_optional``) are stubbed: the network is not under test,
the write path is.

What that buys, in the order the engine does it:

* a drift verdict becomes a branch named for the binding and the version;
* the commit carries the declared fields and nothing else — an unrelated key
  in the same JSON document must survive byte for byte;
* the push lands on the remote, so a consumer can see the branch;
* ``_pr`` is called exactly once, with the title and base the workflow
  expects, and auto-merge is requested only where the binding allows it;
* every failure between branch and pull request is a ``blocked`` verdict
  naming the command that failed, never a silent pass.

The rest of the file covers the parts of the engine that only exist on the
CLI surface (``main``, ``discover``) and the REST layer beneath
``fetch_identity``, because those are the lines the workflow runs and no
other suite reaches.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
import unittest.mock as mock
import urllib.error

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from l9_bindings import apply as writer  # noqa: E402
from l9_bindings import converge, locks, model, remote  # noqa: E402

CONSUMER = "Quantum-L9/Cursor-Governance"
PRODUCER = "Quantum-L9/l9-graphiti-memory"
WHEEL = "l9_graphiti_memory-3.0.2-py3-none-any.whl"
DIGEST = "4822" + "0" * 60

CONTRACT_BINDING = {
    "id": "memory",
    "producer": PRODUCER,
    "channel": "v3",
    "class": "contract",
    "file": "ops/config/memory-binding.json",
    "artifact": "*.whl",
    "fields": {
        "version": ["expected_package_version", "release_evidence.package_version"],
        "revision": "source.ref",
        "digest": "release_evidence.artifact_sha256",
        "revision_note": {
            "path": "source.ref_note",
            "template": "Annotated tag {tag} (object {object}) peels to {commit}.",
        },
    },
    "proof": ".github/workflows/memory-cross-repo.yml",
}

LIBRARY_BINDING = {
    "id": "sdk",
    "producer": "Quantum-L9/Gate_SDK",
    "channel": "v1",
    "class": "library",
    "lock": "uv.lock",
    "package": "l9-ci-sdk",
    "refresh": "python3 relock.py {package} {version}",
    "policy": {"auto_merge": True},
}

MEMORY_DOCUMENT = {
    "expected_package_version": "3.0.1",
    "source": {"ref": "96ed99eb" + "1" * 32, "ref_note": "pinned by hand"},
    "release_evidence": {"artifact_sha256": "0" * 64, "package_version": "3.0.1"},
    "invariant": "prose no release document can derive",
}

RELOCK = """\
import pathlib, sys

package, version = sys.argv[1], sys.argv[2]
path = pathlib.Path("uv.lock")
out, replace_next = [], False
for line in path.read_text().splitlines():
    if replace_next and line.startswith("version = "):
        line, replace_next = f'version = "{version}"', False
    elif line == f'name = "{package}"':
        replace_next = True
    out.append(line)
path.write_text("\\n".join(out) + "\\n")
"""

STALE = """\
import pathlib

# A refresh that runs, exits 0, and leaves the lock on the wrong release.
path = pathlib.Path("uv.lock")
path.write_text(
    'version = 1\\n\\n[[package]]\\nname = "l9-ci-sdk"\\nversion = "1.0.0"\\n',
    encoding="utf-8",
)
"""


def run(cwd: pathlib.Path, *command: str) -> str:
    completed = subprocess.run(
        list(command), cwd=cwd, text=True, capture_output=True, check=False
    )
    if completed.returncode != 0:
        raise AssertionError(f"{command} failed: {completed.stderr}{completed.stdout}")
    return completed.stdout


def git(cwd: pathlib.Path, *arguments: str) -> str:
    return run(
        cwd,
        "git",
        "-c",
        "user.name=Binding Fixture",
        "-c",
        "user.email=binding@l9.invalid",
        "-c",
        "commit.gpgsign=false",
        *arguments,
    )


def identity(
    version: str, commit: str, files: dict[str, str]
) -> remote.ReleaseIdentity:
    return remote.ReleaseIdentity(
        PRODUCER,
        "v3",
        f"v{version}",
        version,
        commit,
        files,
        "document",
        f"https://github.com/{PRODUCER}/releases/tag/v{version}",
    )


class Fixture(unittest.TestCase):
    """A consumer repository with a bare ``origin`` and one declared binding."""

    binding: dict = CONTRACT_BINDING
    files: dict[str, str] = {}

    def setUp(self) -> None:
        self.base = pathlib.Path(tempfile.mkdtemp(prefix="l9-bindings-"))
        self.origin = self.base / "origin.git"
        self.work = self.base / "consumer"
        run(
            self.base,
            "git",
            "init",
            "--quiet",
            "--bare",
            "--initial-branch=main",
            str(self.origin),
        )
        run(
            self.base, "git", "init", "--quiet", "--initial-branch=main", str(self.work)
        )
        git(self.work, "remote", "add", "origin", str(self.origin))
        self.write(
            ".l9/bindings.yaml",
            yaml.safe_dump(
                {
                    "schema": "l9.bindings/v1",
                    "consumer": CONSUMER,
                    "bindings": [self.binding],
                },
                sort_keys=False,
            ),
        )
        for name, text in self.files.items():
            self.write(name, text)
        self.commit("chore: declare the binding")
        self.bindings = model.load(self.work)
        assert self.bindings is not None

    def write(self, relative: str, text: str) -> None:
        path = self.work / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit(self, message: str) -> str:
        git(self.work, "add", "--all")
        git(self.work, "commit", "--quiet", "-m", message)
        git(self.work, "push", "--quiet", "origin", "HEAD:refs/heads/main")
        return git(self.work, "rev-parse", "HEAD").strip()

    def origin_ref(self, ref: str) -> str | None:
        completed = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", ref],
            cwd=self.origin,
            text=True,
            capture_output=True,
            check=False,
        )
        return completed.stdout.strip() or None

    def origin_refs(self) -> set[str]:
        listing = git(self.origin, "for-each-ref", "--format=%(refname)")
        return {line for line in listing.splitlines() if line}

    def show(self, revision: str, path: str) -> str:
        return git(self.work, "show", f"{revision}:{path}")


class ContractConvergeTests(Fixture):
    binding = CONTRACT_BINDING
    files = {
        "ops/config/memory-binding.json": json.dumps(MEMORY_DOCUMENT, indent=2) + "\n"
    }

    def setUp(self) -> None:
        super().setUp()
        self.head = remote.ChannelHead("v3", "v3.0.2", "3.0.2", "d" * 40, "c" * 40)
        self.release = identity("3.0.2", "d" * 40, {WHEEL: DIGEST})

    def _converge(self, **kwargs) -> tuple[list[converge.Verdict], mock.Mock]:
        pull = mock.Mock(return_value={"number": 42, "node_id": "PR_NODE"})
        with (
            mock.patch.object(converge, "channel_head", return_value=self.head),
            mock.patch.object(converge, "fetch_identity", return_value=self.release),
            mock.patch.object(converge, "_pr", pull),
            mock.patch.object(converge, "_auto_merge") as merge,
        ):
            verdicts = converge.converge(
                self.bindings, self.work, "token", dry_run=kwargs.get("dry_run", False)
            )
        self.merge = merge
        return verdicts, pull

    def test_drift_pushes_a_branch_that_carries_only_the_declared_fields(self) -> None:
        verdicts, pull = self._converge()
        self.assertEqual([verdict.state for verdict in verdicts], [converge.PROPOSED])
        self.assertEqual(verdicts[0].detail, "opened #42 for 3.0.2")

        branch = "l9/binding/memory/3.0.2"
        self.assertEqual(
            self.origin_ref(f"refs/heads/{branch}"),
            git(self.work, "rev-parse", branch).strip(),
        )
        self.assertEqual(
            git(self.work, "diff", "--name-only", f"main..{branch}").split(),
            ["ops/config/memory-binding.json"],
        )

        document = json.loads(self.show(branch, "ops/config/memory-binding.json"))
        self.assertEqual(document["expected_package_version"], "3.0.2")
        self.assertEqual(document["release_evidence"]["package_version"], "3.0.2")
        self.assertEqual(document["release_evidence"]["artifact_sha256"], DIGEST)
        self.assertEqual(document["source"]["ref"], "d" * 40)
        self.assertEqual(
            document["source"]["ref_note"],
            f"Annotated tag v3.0.2 (object {'c' * 40}) peels to {'d' * 40}.",
        )
        self.assertEqual(
            document["invariant"], MEMORY_DOCUMENT["invariant"], "undeclared keys move"
        )
        self.assertEqual(list(document), list(MEMORY_DOCUMENT), "key order changed")

        subject = git(self.work, "log", "-1", "--format=%s", branch).strip()
        self.assertEqual(subject, "chore(binding): memory -> 3.0.2 (v3.0.2)")
        body = git(self.work, "log", "-1", "--format=%b", branch)
        self.assertIn("rewrote ops/config/memory-binding.json", body)

        pull.assert_called_once()
        arguments = pull.call_args.args
        self.assertEqual(arguments[0], CONSUMER)
        self.assertEqual(arguments[2], branch)
        self.assertEqual(arguments[3], "main")
        self.assertEqual(arguments[4], "chore(binding): memory -> 3.0.2")
        self.assertIn("| `version` | `3.0.1` | `3.0.2` |", arguments[5])
        self.assertIn(WHEEL, arguments[5])
        self.assertIn("auto_merge=false, requires_proof=true", arguments[5])
        self.merge.assert_not_called()

    def test_dry_run_writes_nothing_anywhere(self) -> None:
        verdicts, pull = self._converge(dry_run=True)
        self.assertEqual([verdict.state for verdict in verdicts], [converge.DRIFTED])
        self.assertEqual(self.origin_refs(), {"refs/heads/main"})
        self.assertIsNone(self.origin_ref("refs/heads/l9/binding/memory/3.0.2"))
        pull.assert_not_called()

    def test_a_push_that_cannot_reach_the_remote_blocks_and_names_the_command(
        self,
    ) -> None:
        git(self.work, "remote", "remove", "origin")
        verdicts, pull = self._converge()
        self.assertEqual([verdict.state for verdict in verdicts], [converge.BLOCKED])
        self.assertIn("git push", verdicts[0].detail)
        pull.assert_not_called()


class LibraryConvergeTests(Fixture):
    binding = LIBRARY_BINDING
    files = {
        "uv.lock": 'version = 1\n\n[[package]]\nname = "l9-ci-sdk"\nversion = "1.3.3"\n',
        "relock.py": RELOCK,
        "noop.py": "import sys\n\nassert sys.argv[1:]\n",
        "stale.py": STALE,
    }

    def setUp(self) -> None:
        super().setUp()
        self.head = remote.ChannelHead("v1", "v1.3.4", "1.3.4", "e" * 40, None)
        self.release = identity("1.3.4", "e" * 40, {})

    def _converge(self) -> tuple[list[converge.Verdict], mock.Mock, mock.Mock]:
        pull = mock.Mock(return_value={"number": 7, "node_id": "PR_NODE"})
        merge = mock.Mock()
        with (
            mock.patch.object(converge, "channel_head", return_value=self.head),
            mock.patch.object(converge, "fetch_identity", return_value=self.release),
            mock.patch.object(converge, "_pr", pull),
            mock.patch.object(converge, "_auto_merge", merge),
        ):
            verdicts = converge.converge(
                self.bindings, self.work, "token", dry_run=False
            )
        return verdicts, pull, merge

    def test_the_declared_refresh_moves_the_lock_and_auto_merge_is_requested(
        self,
    ) -> None:
        verdicts, pull, merge = self._converge()
        self.assertEqual([verdict.state for verdict in verdicts], [converge.PROPOSED])
        self.assertIn("auto-merge enabled", verdicts[0].detail)

        branch = "l9/binding/sdk/1.3.4"
        self.assertEqual(
            git(self.work, "diff", "--name-only", f"main..{branch}").split(),
            ["uv.lock"],
        )
        self.assertIn(
            'name = "l9-ci-sdk"\nversion = "1.3.4"', self.show(branch, "uv.lock")
        )
        self.assertEqual(
            git(self.work, "log", "-1", "--format=%b", branch).strip(),
            "`python3 relock.py l9-ci-sdk 1.3.4`",
        )
        self.assertEqual(
            self.origin_ref(f"refs/heads/{branch}"),
            git(self.work, "rev-parse", branch).strip(),
        )
        self.assertIn(
            "| `lock` | `1.3.3` | `l9-ci-sdk==1.3.4` |", pull.call_args.args[5]
        )
        merge.assert_called_once_with(CONSUMER, "token", "PR_NODE")

    def test_the_commit_carries_its_own_identity(self) -> None:
        # A hosted runner has no ``user.email``. The engine must not inherit
        # one: it authors the commit, so the identity travels with the command.
        # Without that, every converge on a clean runner dies at ``git commit``
        # with exit 128 and the binding blocks for a reason no reviewer can fix.
        environment = {
            "HOME": tempfile.mkdtemp(prefix="l9-no-identity-"),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
        }
        with mock.patch.dict(os.environ, environment):
            for name in (
                "GIT_AUTHOR_NAME",
                "GIT_AUTHOR_EMAIL",
                "GIT_COMMITTER_NAME",
                "GIT_COMMITTER_EMAIL",
            ):
                os.environ.pop(name, None)
            verdicts, _, _ = self._converge()
        self.assertEqual([verdict.state for verdict in verdicts], [converge.PROPOSED])
        author = git(
            self.work, "log", "-1", "--format=%an <%ae>", "l9/binding/sdk/1.3.4"
        )
        self.assertEqual(author.strip(), "core <core@invalid>")

    def test_a_refresh_that_changes_nothing_blocks_rather_than_converging(
        self,
    ) -> None:
        # The lock still drifts (so the engine takes the write path) but the
        # declared refresh writes nothing. Nothing is committed and the binding
        # is blocked: a no-op refresh has not converged anything, and calling
        # it converged is how a drifted consumer stays drifted in silence.
        self.write(
            ".l9/bindings.yaml",
            yaml.safe_dump(
                {
                    "schema": "l9.bindings/v1",
                    "consumer": CONSUMER,
                    "bindings": [
                        {
                            **LIBRARY_BINDING,
                            "refresh": "python3 noop.py {package} {version}",
                        }
                    ],
                },
                sort_keys=False,
            ),
        )
        self.commit("test: a refresh that writes nothing")
        self.bindings = model.load(self.work)
        assert self.bindings is not None
        verdicts, pull, merge = self._converge()
        self.assertEqual([verdict.state for verdict in verdicts], [converge.BLOCKED])
        self.assertIn("pinning 1.3.3, not 1.3.4", verdicts[0].detail)
        self.assertEqual(self.origin_refs(), {"refs/heads/main"})
        pull.assert_not_called()
        merge.assert_not_called()

    def test_an_unreadable_lock_blocks_before_any_write(self) -> None:
        self.write("uv.lock", "not toml [")
        self.commit("test: break the lock")
        verdicts, pull, _ = self._converge()
        self.assertEqual([verdict.state for verdict in verdicts], [converge.BLOCKED])
        self.assertIn("not readable TOML", verdicts[0].detail)
        pull.assert_not_called()
        self.assertEqual(self.origin_refs(), {"refs/heads/main"})

    def test_a_refresh_command_that_fails_blocks_and_quotes_the_command(self) -> None:
        self.write(
            ".l9/bindings.yaml",
            yaml.safe_dump(
                {
                    "schema": "l9.bindings/v1",
                    "consumer": CONSUMER,
                    "bindings": [
                        {
                            **LIBRARY_BINDING,
                            "refresh": "python3 missing.py {package} {version}",
                        }
                    ],
                },
                sort_keys=False,
            ),
        )
        self.commit("test: a refresh that cannot run")
        self.bindings = model.load(self.work)
        assert self.bindings is not None
        verdicts, pull, _ = self._converge()
        self.assertEqual([verdict.state for verdict in verdicts], [converge.BLOCKED])
        self.assertIn("missing.py", verdicts[0].detail)
        pull.assert_not_called()
        self.assertEqual(self.origin_refs(), {"refs/heads/main"})

    def test_a_refresh_that_leaves_the_wrong_version_blocks(self) -> None:
        # The command succeeds but the lock does not reach the release, so the
        # pull request would claim a version the consumer does not pin.
        self.write(
            ".l9/bindings.yaml",
            yaml.safe_dump(
                {
                    "schema": "l9.bindings/v1",
                    "consumer": CONSUMER,
                    "bindings": [
                        {
                            **LIBRARY_BINDING,
                            "refresh": "python3 stale.py {package} {version}",
                        }
                    ],
                },
                sort_keys=False,
            ),
        )
        self.commit("test: a refresh that leaves the wrong version")
        self.bindings = model.load(self.work)
        assert self.bindings is not None
        verdicts, pull, _ = self._converge()
        self.assertEqual([verdict.state for verdict in verdicts], [converge.BLOCKED])
        self.assertIn("pinning 1.0.0, not 1.3.4", verdicts[0].detail)
        pull.assert_not_called()
        self.assertEqual(self.origin_refs(), {"refs/heads/main"})


class DiscoveryTests(unittest.TestCase):
    def test_only_repositories_that_declare_a_binding_are_returned(self) -> None:
        def optional(url: str, token: str) -> tuple[int, object]:
            if "/orgs/Quantum-L9/repos" in url:
                return 200, [
                    {"full_name": "Quantum-L9/declares"},
                    {"full_name": "Quantum-L9/does-not"},
                    {"not_a_repository": True},
                ]
            if url.endswith("/repos/Quantum-L9/declares/contents/.l9/bindings.yaml"):
                return 200, {}
            return 404, {}

        with mock.patch.object(converge, "_optional", side_effect=optional):
            found = converge.discover("Quantum-L9", "token")
        self.assertEqual(found, ["Quantum-L9/declares"])

    def test_pagination_stops_on_a_short_page_and_an_empty_page(self) -> None:
        calls: list[str] = []

        def optional(url: str, token: str) -> tuple[int, object]:
            calls.append(url)
            # Read the page number off the end: ``per_page=100`` contains
            # "page=1", so a substring test would never advance past page one.
            page = url.rsplit("page=", 1)[-1]
            if page == "1":
                return 200, [
                    {"full_name": f"Quantum-L9/r{index}"} for index in range(100)
                ]
            if page == "2":
                return 200, []
            return 404, {}

        with mock.patch.object(converge, "_optional", side_effect=optional):
            found = converge.discover("Quantum-L9", "token")
        self.assertEqual(found, [])
        self.assertTrue(any("page=2" in url for url in calls))

    def test_an_unreachable_org_returns_nothing_rather_than_raising(self) -> None:
        with mock.patch.object(converge, "_optional", return_value=(500, {})):
            self.assertEqual(converge.discover("Quantum-L9", "token"), [])


class CliTests(Fixture):
    binding = CONTRACT_BINDING
    files = {
        "ops/config/memory-binding.json": json.dumps(MEMORY_DOCUMENT, indent=2) + "\n"
    }

    def setUp(self) -> None:
        super().setUp()
        self.head = remote.ChannelHead("v3", "v3.0.2", "3.0.2", "d" * 40, None)
        self.release = identity("3.0.2", "d" * 40, {WHEEL: DIGEST})

    def _main(self, *arguments: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(converge, "channel_head", return_value=self.head),
            mock.patch.object(converge, "fetch_identity", return_value=self.release),
            mock.patch.object(converge, "_pr", return_value={"number": 42}),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = converge.main(["--workspace", str(self.work), *arguments])
        return code, out.getvalue(), err.getvalue()

    def test_validate_parses_the_declaration_and_exits_zero(self) -> None:
        code, out, _ = self._main("validate")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), f"bindings: 1 declared for {CONSUMER}")

    def test_plan_reports_drift_and_exits_three(self) -> None:
        code, out, _ = self._main("--token", "token", "plan")
        self.assertEqual(code, 3)
        self.assertIn("memory", out)
        self.assertIn("drifted", out)
        self.assertIn("3.0.1", out)
        self.assertIn("-> 3.0.2", out)

    def test_plan_json_is_machine_readable(self) -> None:
        code, out, _ = self._main("--token", "token", "plan", "--json")
        self.assertEqual(code, 3)
        payload = json.loads(out)
        self.assertEqual(payload[0]["id"], "memory")
        self.assertEqual(payload[0]["state"], "drifted")
        self.assertEqual(payload[0]["tag"], "v3.0.2")
        self.assertEqual(payload[0]["commit"], "d" * 40)

    def test_converge_without_a_token_is_refused_before_it_writes(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            code, _, err = self._main("converge")
        self.assertEqual(code, 2)
        self.assertIn("needs a token", err)
        self.assertEqual(self.origin_refs(), {"refs/heads/main"})

    def test_discover_without_a_token_is_refused(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            code, _, err = self._main("discover", "--org", "Quantum-L9")
        self.assertEqual(code, 2)
        self.assertIn("needs a token", err)

    def test_discover_prints_the_repositories(self) -> None:
        with mock.patch.object(converge, "discover", return_value=[CONSUMER]):
            code, out, _ = self._main(
                "--token", "token", "discover", "--org", "Quantum-L9"
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), [CONSUMER])

    def test_a_failing_discovery_exits_two(self) -> None:
        with mock.patch.object(
            converge, "discover", side_effect=writer.ApplyError("GET /orgs failed")
        ):
            code, _, err = self._main(
                "--token", "token", "discover", "--org", "Quantum-L9"
            )
        self.assertEqual(code, 2)
        self.assertIn("GET /orgs failed", err)

    def test_invalid_bindings_exit_one(self) -> None:
        self.write(".l9/bindings.yaml", "schema: l9.bindings/v9\nconsumer: x\n")
        code, _, err = self._main("validate")
        self.assertEqual(code, 1)
        self.assertIn("bindings:", err)

    def test_a_consumer_that_declares_nothing_exits_zero(self) -> None:
        (self.work / ".l9/bindings.yaml").unlink()
        code, out, _ = self._main("validate")
        self.assertEqual(code, 0)
        self.assertIn("none declared", out)

    def test_a_blocked_binding_exits_two(self) -> None:
        with (
            mock.patch.object(
                converge, "channel_head", side_effect=remote.RemoteError("unreachable")
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            code = converge.main(
                ["--workspace", str(self.work), "--token", "t", "plan"]
            )
        self.assertEqual(code, 2)


class ModuleEntryPointTests(unittest.TestCase):
    def test_python_dash_m_runs_the_documented_cli(self) -> None:
        workspace = pathlib.Path(tempfile.mkdtemp(prefix="l9-bindings-"))
        (workspace / ".l9").mkdir()
        (workspace / ".l9/bindings.yaml").write_text(
            yaml.safe_dump(
                {
                    "schema": "l9.bindings/v1",
                    "consumer": CONSUMER,
                    "bindings": [CONTRACT_BINDING],
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.l9_bindings",
                "--workspace",
                str(workspace),
                "validate",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(f"1 declared for {CONSUMER}", completed.stdout)


class _Response:
    """The slice of ``http.client.HTTPResponse`` that ``remote`` reads."""

    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class RemoteRestTests(unittest.TestCase):
    """The REST half of ``remote``, against a stubbed transport."""

    def setUp(self) -> None:
        self.head = remote.ChannelHead("v3", "v3.0.2", "3.0.2", "d" * 40, None)
        self.document = json.dumps(
            {
                "schema": "l9.release-identity/v1",
                "repository": PRODUCER,
                "tag": "v3.0.2",
                "version": "3.0.2",
                "commit": "d" * 40,
                "channel": "v3",
                "files": {WHEEL: DIGEST},
            }
        ).encode()

    def _urlopen(self, routes: dict[str, bytes], status: int = 200):
        def open_(request, timeout=None):  # noqa: ANN001 - transport signature
            for suffix, body in routes.items():
                if request.full_url.endswith(suffix):
                    return _Response(status, body)
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

        return open_

    def test_the_identity_document_is_read_from_the_release_asset(self) -> None:
        release = {
            "html_url": "https://github.com/o/r/releases/tag/v3.0.2",
            "assets": [
                {"name": "l9-release.json", "url": "https://api.test/asset/1"},
            ],
        }
        routes = {
            "/releases/tags/v3.0.2": json.dumps(release).encode(),
            "/asset/1": self.document,
        }
        with mock.patch.object(remote.urllib.request, "urlopen", self._urlopen(routes)):
            found = remote.fetch_identity(PRODUCER, self.head, "token")
        self.assertEqual(found.source, "document")
        self.assertEqual(found.files, {WHEEL: DIGEST})
        self.assertEqual(found.commit, "d" * 40)
        self.assertTrue(found.release_url.endswith("/v3.0.2"))

    def test_a_release_with_only_sums_derives_the_identity(self) -> None:
        release = {
            "html_url": "u",
            "assets": [{"name": "SHA256SUMS", "url": "https://api.test/sums"}],
        }
        sums = f"{DIGEST}  {WHEEL}\n{'1' * 64}  {WHEEL}.asc\n".encode()
        routes = {
            "/releases/tags/v3.0.2": json.dumps(release).encode(),
            "/sums": sums,
        }
        with mock.patch.object(remote.urllib.request, "urlopen", self._urlopen(routes)):
            found = remote.fetch_identity(PRODUCER, self.head, "token")
        self.assertEqual(found.source, "derived")
        self.assertEqual(found.files, {WHEEL: DIGEST, f"{WHEEL}.asc": "1" * 64})

    def test_a_missing_release_is_tag_only(self) -> None:
        with mock.patch.object(
            remote.urllib.request,
            "urlopen",
            side_effect=urllib.error.HTTPError("u", 404, "Not Found", {}, None),
        ):
            found = remote.fetch_identity(PRODUCER, self.head, "token")
        self.assertEqual(found.source, "tag-only")
        self.assertEqual(found.files, {})

    def test_a_server_error_is_a_remote_error_not_an_absence(self) -> None:
        with mock.patch.object(
            remote.urllib.request,
            "urlopen",
            side_effect=urllib.error.HTTPError("u", 500, "boom", {}, None),
        ):
            with self.assertRaises(remote.RemoteError) as caught:
                remote._release(PRODUCER, "v3.0.2", None)
        self.assertIn("500", str(caught.exception))

    def test_a_transport_failure_is_a_remote_error(self) -> None:
        with mock.patch.object(
            remote.urllib.request,
            "urlopen",
            side_effect=urllib.error.URLError("no route"),
        ):
            with self.assertRaises(remote.RemoteError) as caught:
                remote._release(PRODUCER, "v3.0.2", None)
        self.assertIn("no route", str(caught.exception))

    def test_a_release_that_is_not_json_is_refused(self) -> None:
        with mock.patch.object(
            remote.urllib.request,
            "urlopen",
            self._urlopen({"/releases/tags/v3.0.2": b"<html>"}),
        ):
            with self.assertRaises(remote.RemoteError) as caught:
                remote._release(PRODUCER, "v3.0.2", None)
        self.assertIn("not JSON", str(caught.exception))

    def test_sums_that_name_no_artifact_are_refused(self) -> None:
        with self.assertRaises(remote.RemoteError):
            remote._sums(b"# empty\n", "v3.0.2")

    def test_an_asset_without_an_api_url_is_refused(self) -> None:
        release = {"assets": [{"name": "SHA256SUMS"}]}
        with self.assertRaises(remote.RemoteError) as caught:
            remote._asset(release, "SHA256SUMS", None)
        self.assertIn("no API url", str(caught.exception))

    def test_a_missing_asset_is_none(self) -> None:
        self.assertIsNone(remote._asset({"assets": []}, "SHA256SUMS", None))


class LockFormatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = pathlib.Path(tempfile.mkdtemp(prefix="l9-lock-"))

    def test_a_package_lock_reads_packages_then_dependencies(self) -> None:
        path = self.directory / "package-lock.json"
        path.write_text(
            json.dumps(
                {
                    "packages": {"node_modules/@l9/sdk": {"version": "2.0.0"}},
                    "dependencies": {"@l9/sdk": {"version": "1.0.0"}},
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(
            locks.pinned(self.directory, "package-lock.json", "@l9/sdk"), "2.0.0"
        )
        path.write_text(
            json.dumps({"dependencies": {"@l9/sdk": {"version": "1.0.0"}}}),
            encoding="utf-8",
        )
        self.assertEqual(
            locks.pinned(self.directory, "package-lock.json", "@l9/sdk"), "1.0.0"
        )

    def test_a_package_absent_from_the_lock_is_none_not_an_error(self) -> None:
        (self.directory / "uv.lock").write_text(
            '[[package]]\nname = "something-else"\nversion = "1.0.0"\n',
            encoding="utf-8",
        )
        self.assertIsNone(locks.pinned(self.directory, "uv.lock", "l9-ci-sdk"))

    def test_a_missing_lock_raises_rather_than_reporting_converged(self) -> None:
        with self.assertRaises(locks.LockUnreadable):
            locks.pinned(self.directory, "uv.lock", "l9-ci-sdk")


class DocumentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = pathlib.Path(tempfile.mkdtemp(prefix="l9-document-"))

    def test_a_non_json_destination_is_refused(self) -> None:
        path = self.directory / "binding.yaml"
        path.write_text("a: 1\n", encoding="utf-8")
        with self.assertRaises(writer.ApplyError) as caught:
            writer.read_document(path)
        self.assertIn("not JSON", str(caught.exception))

    def test_a_json_document_that_is_not_an_object_is_refused(self) -> None:
        path = self.directory / "binding.json"
        path.write_text("[]", encoding="utf-8")
        with self.assertRaises(writer.ApplyError) as caught:
            writer.read_document(path)
        self.assertIn("not a JSON object", str(caught.exception))

    def test_unreadable_json_is_refused(self) -> None:
        path = self.directory / "binding.json"
        path.write_text("{", encoding="utf-8")
        with self.assertRaises(writer.ApplyError) as caught:
            writer.read_document(path)
        self.assertIn("not readable JSON", str(caught.exception))

    def test_an_unresolved_template_token_is_refused(self) -> None:
        with self.assertRaises(writer.ApplyError) as caught:
            writer.render("tag {tag} at {unknown}", {"tag": "v1"})
        self.assertIn("unknown", str(caught.exception))


class RestWriteTests(unittest.TestCase):
    """The pull-request half of ``converge``, against a stubbed transport."""

    def test_a_post_returns_the_created_object(self) -> None:
        body = json.dumps({"number": 12, "node_id": "NODE"}).encode()
        with mock.patch.object(
            converge.urllib.request,
            "urlopen",
            lambda request, timeout=None: _Response(201, body),
        ):
            status, payload = converge._request(
                "POST", "https://api.test/pulls", "token", {"title": "t"}
            )
        self.assertEqual(status, 201)
        self.assertEqual(payload["number"], 12)

    def test_an_http_error_carries_the_response_body(self) -> None:
        with mock.patch.object(
            converge.urllib.request,
            "urlopen",
            side_effect=urllib.error.HTTPError(
                "u", 422, "Unprocessable", {}, io.BytesIO(b'{"message": "no"}')
            ),
        ):
            with self.assertRaises(writer.ApplyError) as caught:
                converge._request("POST", "https://api.test/pulls", "token", {})
        self.assertIn("422", str(caught.exception))
        self.assertIn('"no"', str(caught.exception))

    def test_a_transport_failure_is_an_apply_error(self) -> None:
        with mock.patch.object(
            converge.urllib.request,
            "urlopen",
            side_effect=urllib.error.URLError("no route"),
        ):
            with self.assertRaises(writer.ApplyError) as caught:
                converge._request("GET", "https://api.test/pulls", "token")
        self.assertIn("no route", str(caught.exception))

    def test_an_existing_pull_request_is_reused(self) -> None:
        existing = {"number": 5, "node_id": "OLD"}
        with mock.patch.object(
            converge, "_request", return_value=(200, [existing])
        ) as request:
            found = converge._pr(CONSUMER, "token", "branch", "main", "title", "body")
        self.assertEqual(found, existing)
        self.assertEqual(request.call_count, 1)
        self.assertIn(
            "/pulls?state=open&head=Quantum-L9:branch", request.call_args.args[1]
        )

    def test_a_missing_pull_request_is_created_with_the_given_base(self) -> None:
        created = {"number": 6, "node_id": "NEW"}
        with mock.patch.object(
            converge, "_request", side_effect=[(200, []), (201, created)]
        ) as request:
            found = converge._pr(CONSUMER, "token", "branch", "main", "title", "body")
        self.assertEqual(found, created)
        method, url, _, payload = request.call_args.args
        self.assertEqual(
            (method, url), ("POST", f"{remote.API}/repos/{CONSUMER}/pulls")
        )
        self.assertEqual(payload["head"], "branch")
        self.assertEqual(payload["base"], "main")
        self.assertEqual(payload["title"], "title")

    def test_auto_merge_asks_for_a_squash_on_the_pull_request_node(self) -> None:
        with mock.patch.object(converge, "_request", return_value=(200, {})) as request:
            converge._auto_merge(CONSUMER, "token", "NODE")
        payload = request.call_args.args[3]
        self.assertIn("enablePullRequestAutoMerge", payload["query"])
        self.assertEqual(payload["variables"], {"id": "NODE"})

    def test_optional_returns_404_as_an_answer(self) -> None:
        with mock.patch.object(
            converge.urllib.request,
            "urlopen",
            side_effect=urllib.error.HTTPError("u", 404, "Not Found", {}, None),
        ):
            self.assertEqual(converge._optional("https://api.test/x", "t"), (404, {}))

    def test_optional_fails_on_anything_else(self) -> None:
        with mock.patch.object(
            converge.urllib.request,
            "urlopen",
            side_effect=urllib.error.HTTPError("u", 503, "Unavailable", {}, None),
        ):
            with self.assertRaises(writer.ApplyError):
                converge._optional("https://api.test/x", "t")


class SchemaRefusalTests(unittest.TestCase):
    """``l9.bindings/v1`` is closed: every one of these is refused by name."""

    def _document(self, **overrides: object) -> dict:
        document = {
            "schema": "l9.bindings/v1",
            "consumer": CONSUMER,
            "bindings": [json.loads(json.dumps(CONTRACT_BINDING))],
        }
        document.update(overrides)
        return document

    def _binding(self, **overrides: object) -> dict:
        document = self._document()
        document["bindings"][0].update(overrides)
        return document

    def _binding_without(self, *keys: str, binding: dict | None = None) -> dict:
        """Omit keys entirely: ``None`` would trip the string guard first."""
        document = self._document()
        if binding is not None:
            document["bindings"] = [json.loads(json.dumps(binding))]
        for key in keys:
            document["bindings"][0].pop(key, None)
        return document

    def test_the_valid_declaration_parses_and_exposes_its_producer_url(self) -> None:
        bindings = model.parse(self._document())
        self.assertEqual(bindings.consumer, CONSUMER)
        self.assertEqual(
            bindings.by_id("memory").producer_url, f"https://github.com/{PRODUCER}.git"
        )
        with self.assertRaises(model.BindingsInvalid):
            bindings.by_id("absent")

    def test_invalid_declarations_are_refused_with_the_offending_place(self) -> None:
        cases: dict[str, tuple[dict, str]] = {
            "not a mapping": ([], "must be a mapping"),
            "unknown top key": (self._document(schema_extra=1), "unknown key"),
            "wrong schema": (self._document(schema="l9.bindings/v2"), "schema must be"),
            "consumer not owner/name": (
                self._document(consumer="cursor"),
                "owner/name",
            ),
            "no bindings": (self._document(bindings=[]), "at least one binding"),
            "binding not a mapping": (
                self._document(bindings=["x"]),
                "must be a mapping",
            ),
            "unknown binding key": (self._binding(auto_mrege=True), "unknown key"),
            "id not a slug": (self._binding(id="Memory"), "must match"),
            "producer not owner/name": (self._binding(producer="memory"), "owner/name"),
            "channel not a major": (self._binding(channel="3"), "major channel"),
            "unknown class": (
                self._binding(**{"class": "floating"}),
                "must be one of",
            ),
            "description not a string": (
                self._binding(description=7),
                "must be a string",
            ),
            "unknown policy key": (
                self._binding(policy={"auto_pr": True, "automerge": True}),
                "unknown key",
            ),
            "policy not a mapping": (self._binding(policy=[]), "must be a mapping"),
            "policy flag not a boolean": (
                self._binding(policy={"auto_pr": "yes"}),
                "true or false",
            ),
            "contract without proof": (
                self._binding(policy={"requires_proof": False}),
                "always requires its proof",
            ),
            "contract without file": (self._binding_without("file"), "requires file"),
            "contract without fields": (
                self._binding_without("fields"),
                "requires file",
            ),
            "contract naming a lock": (self._binding(lock="uv.lock"), "not a lock"),
            "library without lock": (
                self._binding_without("lock", binding=LIBRARY_BINDING),
                "requires lock",
            ),
            "library naming a file": (
                self._document(
                    bindings=[{**LIBRARY_BINDING, "file": "ops/config/x.json"}]
                ),
                "names a lock",
            ),
            "deployed without environment": (
                self._document(
                    bindings=[
                        {
                            "id": "c1",
                            "producer": PRODUCER,
                            "channel": "v3",
                            "class": "deployed",
                        }
                    ]
                ),
                "requires an environment",
            ),
            "deployed naming a lock": (
                self._document(
                    bindings=[
                        {
                            "id": "c1",
                            "producer": PRODUCER,
                            "channel": "v3",
                            "class": "deployed",
                            "environment": "c1",
                            "lock": "uv.lock",
                        }
                    ]
                ),
                "writes no file",
            ),
            "unknown field name": (
                self._binding(fields={"sha": "source.ref"}),
                "unknown field",
            ),
            "fields not a mapping": (self._binding(fields=[]), "non-empty mapping"),
            "field value not a path": (
                self._binding(fields={"version": 7}),
                "dotted path",
            ),
            "field mapping with an unknown key": (
                self._binding(fields={"version": {"path": "a", "extra": 1}}),
                "unknown key",
            ),
            "empty path list": (
                self._binding(fields={"version": []}),
                "non-empty list",
            ),
            "path that is not dotted": (
                self._binding(fields={"version": "a..b"}),
                "not a dotted path",
            ),
            "template on a field that carries no prose": (
                self._binding(
                    fields={
                        "version": {"path": "a", "template": "{version}"},
                    }
                ),
                "only revision_note",
            ),
            "template naming an unknown token": (
                self._binding(
                    fields={
                        "revision_note": {
                            "path": "a",
                            "template": "tag {tagg}",
                        }
                    }
                ),
                "unknown token",
            ),
            "template naming no token at all": (
                self._binding(
                    fields={"revision_note": {"path": "a", "template": "nothing"}}
                ),
                "names no known token",
            ),
            "digest without an artifact": (
                self._binding_without("artifact"),
                "must name which published file",
            ),
            "duplicate binding id": (
                self._document(
                    bindings=[
                        json.loads(json.dumps(CONTRACT_BINDING)),
                        json.loads(json.dumps(CONTRACT_BINDING)),
                    ]
                ),
                "twice",
            ),
        }
        for name, (document, expected) in cases.items():
            with self.subTest(name):
                with self.assertRaises(model.BindingsInvalid) as caught:
                    model.parse(document)
                self.assertIn(expected, str(caught.exception), name)

    def test_a_missing_declaration_is_absent_not_empty(self) -> None:
        directory = pathlib.Path(tempfile.mkdtemp(prefix="l9-bindings-"))
        self.assertIsNone(model.load(directory))

    def test_unreadable_yaml_is_refused(self) -> None:
        directory = pathlib.Path(tempfile.mkdtemp(prefix="l9-bindings-"))
        (directory / ".l9").mkdir()
        (directory / ".l9/bindings.yaml").write_text("a: [1,\n", encoding="utf-8")
        with self.assertRaises(model.BindingsInvalid) as caught:
            model.load(directory)
        self.assertIn("not readable YAML", str(caught.exception))

    def test_a_refresh_on_a_contract_binding_is_refused(self) -> None:
        binding = model.parse(self._document()).bindings[0]
        with self.assertRaises(writer.ApplyError) as caught:
            writer.refresh(pathlib.Path("."), binding, "3.0.2")
        self.assertIn("not a library binding", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
