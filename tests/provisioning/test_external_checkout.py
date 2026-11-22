"""The declared external checkout keeps the read token out of git argv."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github" / "actions" / "checkout-external" / "checkout.py"
TOKEN = "ghs_test_token_not_a_real_secret"


def _load():
    spec = importlib.util.spec_from_file_location("checkout_external", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ExternalCheckoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load()

    def test_a_missing_declaration_is_a_no_op(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(self.module.load_declaration(Path(tmp)))

    def test_a_required_missing_declaration_fails(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            unittest.mock.patch.dict(os.environ, {self.module.REQUIRED_ENV: "true"}),
        ):
            with self.assertRaises(self.module.CheckoutError):
                self.module.load_declaration(Path(tmp))

    def test_fetch_passes_the_token_only_in_the_fetch_environment(self) -> None:
        calls: list[tuple[list[str], dict[str, str] | None]] = []

        def fake_run(
            command, cwd=None, env=None, check=False, capture_output=False, text=False
        ):
            calls.append((list(command), env))
            completed = subprocess.CompletedProcess(command, 0)
            completed.stdout = "3d7212355cfa7db838807c2f39181cf95473ca76\n"
            return completed

        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "odoo-consumer"
            with unittest.mock.patch.object(self.module.subprocess, "run", fake_run):
                self.module.fetch_revision(
                    destination,
                    "cryptoxdog/IB-Odoo_19",
                    "3d7212355cfa7db838807c2f39181cf95473ca76",
                    TOKEN,
                )
        self.assertTrue(calls)
        for command, env in calls:
            self.assertNotIn(TOKEN, " ".join(command))
        fetch = [env for command, env in calls if "fetch" in command]
        self.assertEqual(len(fetch), 1)
        self.assertIsNotNone(fetch[0])
        assert fetch[0] is not None
        self.assertNotIn(self.module.READ_TOKEN_ENV, fetch[0])
        self.assertIn("AUTHORIZATION: basic ", "".join(fetch[0].values()))

    def test_publish_writes_the_path_and_not_the_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            handle = Path(tmp) / "env"
            with unittest.mock.patch.dict(os.environ, {"GITHUB_ENV": str(handle)}):
                self.module.publish(
                    "L9_ODOO_CONSUMER", str(Path(tmp) / "odoo-consumer")
                )
            text = handle.read_text(encoding="utf-8")
        self.assertIn("L9_ODOO_CONSUMER=", text)
        self.assertNotIn(TOKEN, text)

    def test_an_out_of_org_repository_uses_the_external_token(self) -> None:
        # A fine-grained token covers one resource owner, so the organization
        # token cannot read cryptoxdog/IB-Odoo_19 (CEG's declared checkout).
        org, external = "org-token", "external-token"
        self.assertEqual(
            external, self.module.token_for("cryptoxdog/IB-Odoo_19", org, external)
        )
        self.assertEqual(
            org, self.module.token_for("Quantum-L9/l9-ci-sdk", org, external)
        )
        self.assertEqual(
            org, self.module.token_for("quantum-l9/l9-ci-sdk", org, external)
        )
        # No external token configured: fall back to the organization token.
        self.assertEqual(org, self.module.token_for("cryptoxdog/IB-Odoo_19", org, ""))

    def test_main_fetches_with_the_external_token_and_clears_both(self) -> None:
        fetched: list[str] = []
        seen_env: list[dict[str, str]] = []

        def fake_fetch(destination, repository, revision, token):  # type: ignore[no-untyped-def]
            fetched.append(token)
            seen_env.append(dict(os.environ))

        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / ".l9"
            manifest.mkdir()
            (manifest / "external-checkout.json").write_text(
                json.dumps(
                    {
                        "repository": "cryptoxdog/IB-Odoo_19",
                        "ref": "3d7212355cfa7db838807c2f39181cf95473ca76",
                        "path": "odoo-consumer",
                        "env": "L9_ODOO_CONSUMER",
                    }
                ),
                encoding="utf-8",
            )
            env = {
                self.module.READ_TOKEN_ENV: "org-token",
                self.module.EXTERNAL_TOKEN_ENV: "external-token",
                self.module.WORKSPACE_ENV: tmp,
                "GITHUB_ENV": str(Path(tmp) / "github-env"),
            }
            with (
                unittest.mock.patch.dict(os.environ, env),
                unittest.mock.patch.object(self.module, "fetch_revision", fake_fetch),
            ):
                self.assertEqual(0, self.module.main())
        self.assertEqual(["external-token"], fetched)
        self.assertNotIn(self.module.READ_TOKEN_ENV, seen_env[0])
        self.assertNotIn(self.module.EXTERNAL_TOKEN_ENV, seen_env[0])

    def test_declaration_rejects_a_path_that_escapes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / ".l9"
            manifest.mkdir()
            (manifest / "external-checkout.json").write_text(
                json.dumps(
                    {
                        "repository": "cryptoxdog/IB-Odoo_19",
                        "ref": "3d7212355cfa7db838807c2f39181cf95473ca76",
                        "path": "../odoo",
                        "env": "L9_ODOO_CONSUMER",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(self.module.CheckoutError):
                self.module.load_declaration(Path(tmp))


if __name__ == "__main__":
    unittest.main()
