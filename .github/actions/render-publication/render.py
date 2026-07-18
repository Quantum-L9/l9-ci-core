#!/usr/bin/env python3
"""Render a bounded publication payload from an SDK-owned projection."""

from __future__ import annotations
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ALLOWED_MODES = {"blocking", "advisory", "shadow", "disabled"}
ALLOWED_RESULTS = {"success", "failure", "cancelled", "skipped"}
FULL_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
MAX_TITLE = 255
MAX_SUMMARY = 60_000
MAX_TEXT = 60_000
MAX_ANNOTATIONS = 50
MAX_MESSAGE = 64_000


class PublicationError(RuntimeError):
    pass


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise PublicationError(f"{name} is required")
    return value


def optional(name: str) -> str:
    return os.environ.get(name, "").strip()


def workspace_path(value: str, *, must_exist: bool) -> Path:
    workspace = Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd())).resolve()
    candidate = Path(value)
    path = (
        candidate.resolve()
        if candidate.is_absolute()
        else (workspace / candidate).resolve()
    )
    try:
        path.relative_to(workspace)
    except ValueError as error:
        raise PublicationError("publication path escapes GITHUB_WORKSPACE") from error
    if must_exist and not path.is_file():
        raise PublicationError(f"file does not exist: {path}")
    return path


def string_value(document: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = document.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def normalize_annotation(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    path = value.get("path")
    message = value.get("message")
    if not isinstance(path, str) or not path.strip():
        return None
    if not isinstance(message, str) or not message.strip():
        return None
    start_line = value.get("start_line", value.get("line", 1))
    end_line = value.get("end_line", start_line)
    if not isinstance(start_line, int) or start_line < 1:
        return None
    if not isinstance(end_line, int) or end_line < start_line:
        return None
    level = value.get(
        "annotation_level",
        value.get("level", "warning"),
    )
    if level not in {"notice", "warning", "failure"}:
        level = "warning"
    annotation: dict[str, Any] = {
        "path": path.lstrip("/")[:4096],
        "start_line": start_line,
        "end_line": end_line,
        "annotation_level": level,
        "message": message[:MAX_MESSAGE],
    }
    title = value.get("title")
    if isinstance(title, str) and title.strip():
        annotation["title"] = title.strip()[:255]
    raw_details = value.get("raw_details")
    if isinstance(raw_details, str) and raw_details.strip():
        annotation["raw_details"] = raw_details[:MAX_MESSAGE]
    return annotation


def extract_annotations(document: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: Any = document.get("annotations", [])
    output = document.get("output")
    if not candidates and isinstance(output, dict):
        candidates = output.get("annotations", [])
    if not isinstance(candidates, list):
        return []
    annotations: list[dict[str, Any]] = []
    for value in candidates:
        annotation = normalize_annotation(value)
        if annotation is not None:
            annotations.append(annotation)
        if len(annotations) == MAX_ANNOTATIONS:
            break
    return annotations


def workflow_conclusion(mode: str, result: str) -> str:
    if result == "success":
        return "success"
    if result == "cancelled":
        return "cancelled"
    if result == "skipped":
        return "neutral"
    if mode == "blocking":
        return "failure"
    return "neutral"


def projection_summary(document: dict[str, Any]) -> str:
    summary = string_value(
        document,
        "summary",
        "markdown",
        "body",
        "text",
    )
    output = document.get("output")
    if not summary and isinstance(output, dict):
        summary = string_value(
            output,
            "summary",
            "markdown",
            "text",
        )
    return summary


def emit(name: str, value: str) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        delimiter = f"L9_{name.upper().replace('-', '_')}_EOF"
        with open(target, "a", encoding="utf-8") as stream:
            stream.write(f"{name}<<{delimiter}\n")
            stream.write(value)
            stream.write(f"\n{delimiter}\n")
    else:
        print(f"{name}={value}")


def main() -> int:
    try:
        payload_path = workspace_path(
            required("L9_AGENT_PAYLOAD"),
            must_exist=True,
        )
        output_path = workspace_path(
            required("L9_PUBLICATION_OUTPUT"),
            must_exist=False,
        )
        profile = required("L9_PROFILE")
        mode = required("L9_MODE")
        provider = required("L9_PROVIDER")
        sdk_revision = required("L9_SDK_REVISION").lower()
        governance_digest = required("L9_GOVERNANCE_DIGEST").lower()
        repository_revision = required("L9_REPOSITORY_REVISION").lower()
        workflow_result = required("L9_WORKFLOW_RESULT")
        run_url = required("L9_RUN_URL")
        artifact_url = optional("L9_ARTIFACT_URL")
        if mode not in ALLOWED_MODES:
            raise PublicationError(f"unsupported mode: {mode}")
        if workflow_result not in ALLOWED_RESULTS:
            raise PublicationError(f"unsupported workflow result: {workflow_result}")
        if not FULL_SHA.fullmatch(sdk_revision):
            raise PublicationError("SDK revision must be a full commit SHA")
        if not FULL_SHA.fullmatch(repository_revision):
            raise PublicationError("repository revision must be a full commit SHA")
        if not re.fullmatch(r"[0-9a-f]{64}", governance_digest):
            raise PublicationError("governance digest must be a SHA-256 digest")
        try:
            projection = json.loads(payload_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise PublicationError(
                f"agent payload is not valid JSON: {error}"
            ) from error
        if not isinstance(projection, dict):
            raise PublicationError("agent payload must contain a JSON object")
        projected_title = string_value(
            projection,
            "title",
            "name",
        )
        title = projected_title or f"L9 {provider} · {profile}"
        projected_summary = projection_summary(projection)
        annotations = extract_annotations(projection)
        metadata = [
            "### L9 CI result",
            "",
            f"- **Provider:** `{provider}`",
            f"- **Profile:** `{profile}`",
            f"- **Mode:** `{mode}`",
            f"- **Workflow result:** `{workflow_result}`",
            f"- **SDK revision:** `{sdk_revision}`",
            f"- **Repository revision:** `{repository_revision}`",
            f"- **Governance digest:** `{governance_digest}`",
            f"- **Workflow run:** {run_url}",
        ]
        if artifact_url:
            metadata.append(f"- **Artifacts:** {artifact_url}")
        summary_parts = ["\n".join(metadata)]
        if projected_summary:
            summary_parts.extend(
                [
                    "",
                    "### SDK projection",
                    "",
                    projected_summary,
                ]
            )
        if annotations:
            summary_parts.extend(
                [
                    "",
                    f"Published annotations: {len(annotations)}",
                ]
            )
        summary = "\n".join(summary_parts)[:MAX_SUMMARY]
        conclusion = workflow_conclusion(mode, workflow_result)
        publication = {
            "schema": "l9.core-publication/v1",
            "name": title[:MAX_TITLE],
            "head_sha": repository_revision,
            "status": "completed",
            "conclusion": conclusion,
            "output": {
                "title": title[:MAX_TITLE],
                "summary": summary,
                "text": projected_summary[:MAX_TEXT],
                "annotations": annotations,
            },
            "metadata": {
                "profile": profile,
                "mode": mode,
                "provider": provider,
                "sdk_revision": sdk_revision,
                "governance_digest": governance_digest,
                "workflow_result": workflow_result,
                "run_url": run_url,
                "artifact_url": artifact_url,
            },
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(
                publication,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        emit("title", title[:MAX_TITLE])
        emit("summary", summary)
        emit("conclusion", conclusion)
        emit("annotation-count", str(len(annotations)))
        return 0
    except PublicationError as error:
        print(f"render-publication: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
