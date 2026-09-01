#!/usr/bin/env python3
"""Pick the Semgrep language for Organization CI (Core).

SDK capability detection remains authoritative. This helper only resolves the
ambiguous both/neither cases that used to SystemExit the required workflow.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PYTHON_MARKERS = ("pyproject.toml", "requirements.txt")
TYPESCRIPT_MARKER = "package.json"
PYTHON_SUFFIXES = {".py", ".pyi"}
TYPESCRIPT_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".mts", ".cts"}


class DetectLanguageError(RuntimeError):
    pass


def workspace_root() -> Path:
    raw = os.environ.get("L9_DETECT_ROOT") or os.environ.get("GITHUB_WORKSPACE") or "."
    return Path(raw).resolve()


def load_languages(root: Path) -> set[str]:
    payload_path = root / ".l9" / "runtime" / "capabilities.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    return set(payload["result"]["capabilities"]["languages"])


def tracked_files(root: Path) -> list[Path]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            check=False,
            capture_output=True,
        )
    except OSError:
        completed = None
    if completed is not None and completed.returncode == 0 and completed.stdout:
        names = [name.decode("utf-8") for name in completed.stdout.split(b"\0") if name]
        return [root / name for name in names]
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {".git", "node_modules", ".venv", ".l9"} for part in path.parts):
            continue
        files.append(path)
    return files


def count_suffix(files: list[Path], suffixes: set[str]) -> int:
    return sum(1 for path in files if path.suffix in suffixes)


def has_python_marker(root: Path) -> bool:
    return any((root / name).is_file() for name in PYTHON_MARKERS)


def has_typescript_marker(root: Path) -> bool:
    return (root / TYPESCRIPT_MARKER).is_file()


def pick_language(*, languages: set[str], repo_class: str, root: Path) -> str:
    has_python = "python" in languages
    has_typescript = bool({"typescript", "javascript"} & languages)

    if repo_class == "python":
        if not has_python:
            raise DetectLanguageError(
                "consumer repo_class=python conflicts with SDK capability detection"
            )
        return "python"
    if repo_class == "typescript":
        if not has_typescript:
            raise DetectLanguageError(
                "consumer repo_class=typescript conflicts with SDK capability detection"
            )
        return "typescript"

    if has_python and not has_typescript:
        return "python"
    if has_typescript and not has_python:
        return "typescript"
    if not has_python and not has_typescript:
        return "none"

    python_marker = has_python_marker(root)
    typescript_marker = has_typescript_marker(root)
    if typescript_marker and not python_marker:
        return "typescript"
    if python_marker and not typescript_marker:
        return "python"

    files = tracked_files(root)
    python_count = count_suffix(files, PYTHON_SUFFIXES)
    typescript_count = count_suffix(files, TYPESCRIPT_SUFFIXES)
    if typescript_count > python_count:
        return "typescript"
    return "python"


def emit(name: str, value: str) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as stream:
            stream.write(f"{name}={value}\n")
        return
    print(f"{name}={value}")


def main() -> int:
    root = workspace_root()
    repo_class = os.environ.get("REPO_CLASS", "auto")
    try:
        languages = load_languages(root)
        language = pick_language(
            languages=languages, repo_class=repo_class, root=root
        )
    except DetectLanguageError as error:
        print(str(error), file=sys.stderr)
        return 1
    emit("language", language)
    print(f"SDK-detected CI language: {language}; languages={sorted(languages)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
