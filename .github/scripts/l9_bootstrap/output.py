from __future__ import annotations
import json, os, sys, tempfile
from pathlib import Path
from typing import Any


def _serialize(data: Any) -> bytes:
    return (json.dumps(data, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def write_json(data: Any, path) -> None:
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = _serialize(data)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        os.replace(tmp, dest)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            # Best-effort cleanup of the temp file: if unlink fails (already
            # gone, or filesystem error) there is nothing further we can do,
            # so swallow it and re-raise the original write/replace error below.
            pass
        raise


def write_json_stdout(data: Any) -> None:
    sys.stdout.buffer.write(_serialize(data))
