from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from l9_repo.authority import AuthorityError, validate_authority  # noqa: E402


def config() -> dict[str, object]:
    return json.loads((ROOT / ".l9/repo-workflow.json").read_text(encoding="utf-8"))


def write_required(root: pathlib.Path, data: dict[str, object]) -> None:
    authority = data["authority"]
    assert isinstance(authority, dict)
    metadata = data["metadata"]
    assert isinstance(metadata, dict)
    for key in ("target_authorities", "generated_artifacts"):
        for relative in authority[key]:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture\n", encoding="utf-8")
    for relative in authority["derived_documents"]:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"{metadata['artifact_id']} {metadata['artifact_version']}\n",
            encoding="utf-8",
        )
    manifests = authority["dependency_manifests"]
    for key in ("target_required", "component_bundled"):
        for relative in manifests[key]:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture\n", encoding="utf-8")


class AuthorityTests(unittest.TestCase):
    def test_target_root_release_docs_are_not_required(self) -> None:
        data = config()
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            write_required(root, data)
            validate_authority(root, data)
            self.assertFalse((root / "AUTHORITY.md").exists())
            self.assertFalse((root / "MANIFEST.md").exists())

    def test_missing_target_authority_fails(self) -> None:
        data = config()
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            write_required(root, data)
            (root / ".l9/ownership.yaml").unlink()
            with self.assertRaisesRegex(AuthorityError, "missing target authority"):
                validate_authority(root, data)

    def test_derived_document_requires_component_identity(self) -> None:
        data = config()
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            write_required(root, data)
            derived = root / "docs/repository-execution-runtime.md"
            derived.write_text("# runtime\n", encoding="utf-8")
            with self.assertRaisesRegex(AuthorityError, "authoritative token"):
                validate_authority(root, data)


if __name__ == "__main__":
    unittest.main()
