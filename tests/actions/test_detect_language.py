"""Language pick for Organization CI (Core)."""

from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DETECT = ROOT / ".github" / "actions" / "detect-language" / "detect.py"

spec = importlib.util.spec_from_file_location("detect_language", DETECT)
assert spec is not None and spec.loader is not None
detect = importlib.util.module_from_spec(spec)
spec.loader.exec_module(detect)


def _git_tree(files: dict[str, str]) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="l9-detect-"))
    subprocess.run(["git", "init"], cwd=tmp, check=True, capture_output=True)
    for rel, body in files.items():
        path = tmp / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=ci@example.com",
            "-c",
            "user.name=ci",
            "commit",
            "-m",
            "fixture",
        ],
        cwd=tmp,
        check=True,
        capture_output=True,
    )
    return tmp


class DetectLanguageTests(unittest.TestCase):
    def test_neither_is_none(self) -> None:
        root = _git_tree({"README.md": "docs only\n"})
        self.assertEqual(
            "none",
            detect.pick_language(languages=set(), repo_class="auto", root=root),
        )

    def test_python_only(self) -> None:
        root = _git_tree({"app.py": "x = 1\n"})
        self.assertEqual(
            "python",
            detect.pick_language(languages={"python"}, repo_class="auto", root=root),
        )

    def test_typescript_only(self) -> None:
        root = _git_tree({"src/index.ts": "export {}\n"})
        self.assertEqual(
            "typescript",
            detect.pick_language(
                languages={"typescript"}, repo_class="auto", root=root
            ),
        )

    def test_both_package_json_only_is_typescript(self) -> None:
        root = _git_tree(
            {
                "package.json": '{"name":"app"}\n',
                "src/index.ts": "export {}\n",
                "scripts/helper.py": "print(1)\n",
            }
        )
        self.assertEqual(
            "typescript",
            detect.pick_language(
                languages={"python", "typescript"}, repo_class="auto", root=root
            ),
        )

    def test_both_pyproject_only_is_python(self) -> None:
        root = _git_tree(
            {
                "pyproject.toml": "[project]\nname='app'\n",
                "app.py": "x = 1\n",
                "frontend.js": "console.log(1)\n",
            }
        )
        self.assertEqual(
            "python",
            detect.pick_language(
                languages={"python", "javascript"}, repo_class="auto", root=root
            ),
        )

    def test_both_markers_prefer_more_tracked_files_then_python(self) -> None:
        more_python = _git_tree(
            {
                "package.json": '{"name":"app"}\n',
                "requirements.txt": "pytest\n",
                "a.py": "1\n",
                "b.py": "2\n",
                "src/index.ts": "export {}\n",
            }
        )
        self.assertEqual(
            "python",
            detect.pick_language(
                languages={"python", "typescript"},
                repo_class="auto",
                root=more_python,
            ),
        )
        more_ts = _git_tree(
            {
                "package.json": '{"name":"app"}\n',
                "pyproject.toml": "[project]\nname='app'\n",
                "app.py": "1\n",
                "src/a.ts": "export {}\n",
                "src/b.ts": "export {}\n",
            }
        )
        self.assertEqual(
            "typescript",
            detect.pick_language(
                languages={"python", "typescript"},
                repo_class="auto",
                root=more_ts,
            ),
        )
        tie = _git_tree(
            {
                "package.json": '{"name":"app"}\n',
                "requirements.txt": "pytest\n",
                "app.py": "1\n",
                "src/index.ts": "export {}\n",
            }
        )
        self.assertEqual(
            "python",
            detect.pick_language(
                languages={"python", "typescript"}, repo_class="auto", root=tie
            ),
        )

    def test_seo_bot_shaped_both_markers_typescript_majority(self) -> None:
        root = _git_tree(
            {
                "package.json": '{"name":"seo-bot"}\n',
                "requirements.txt": "# incidental python tooling\n",
                "src/index.ts": "export {}\n",
                "src/crawl.ts": "export {}\n",
                "src/render.tsx": "export {}\n",
                "tools/one_off.py": "print(1)\n",
            }
        )
        self.assertEqual(
            "typescript",
            detect.pick_language(
                languages={"python", "typescript", "javascript"},
                repo_class="auto",
                root=root,
            ),
        )

    def test_repo_class_conflict_still_exits(self) -> None:
        root = _git_tree({"src/index.ts": "export {}\n"})
        with self.assertRaises(detect.DetectLanguageError) as python_conflict:
            detect.pick_language(
                languages={"typescript"}, repo_class="python", root=root
            )
        self.assertIn("repo_class=python conflicts", str(python_conflict.exception))
        py_root = _git_tree({"app.py": "1\n"})
        with self.assertRaises(detect.DetectLanguageError) as ts_conflict:
            detect.pick_language(
                languages={"python"}, repo_class="typescript", root=py_root
            )
        self.assertIn("repo_class=typescript conflicts", str(ts_conflict.exception))


if __name__ == "__main__":
    unittest.main()
