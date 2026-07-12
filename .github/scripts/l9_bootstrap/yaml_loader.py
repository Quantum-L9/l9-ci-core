from __future__ import annotations
import io
from pathlib import Path
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from . import limits as _lim


def _validate_depth(value, *, source: str, depth: int = 0) -> None:
    if depth > _lim.MAX_YAML_DEPTH:
        raise ValueError(
            f"YAML from {source} exceeds maximum depth "
            f"{_lim.MAX_YAML_DEPTH}"
        )

    if isinstance(value, dict):
        for key, child in value.items():
            _validate_depth(key, source=source, depth=depth + 1)
            _validate_depth(child, source=source, depth=depth + 1)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _validate_depth(child, source=source, depth=depth + 1)


def _make_yaml() -> YAML:
    y = YAML(typ="rt")
    y.version = (1, 2)
    y.allow_duplicate_keys = False
    return y


def load_yaml_file(path, max_bytes=None):
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"Not a file: {p}")
    limit = max_bytes if max_bytes is not None else _lim.MAX_WORKFLOW_FILE_BYTES
    if p.stat().st_size > limit:
        raise ValueError(f"{p} exceeds byte limit {limit}")
    text = p.read_text(encoding="utf-8")
    if len(text.encode("utf-8")) > limit:
        raise ValueError(f"{p} content exceeds byte limit {limit}")
    return parse_yaml_string(text, source=str(p))


def parse_yaml_string(text: str, source: str = "<string>"):
    limit = _lim.MAX_WORKFLOW_FILE_BYTES
    if len(text.encode("utf-8")) > limit:
        raise ValueError(f"YAML from {source} exceeds byte limit {limit}")
    try:
        value = _make_yaml().load(io.StringIO(text))
        _validate_depth(value, source=source)
        return value
    except YAMLError as exc:
        raise YAMLError(f"YAML parse error in {source}: {exc}") from exc


def get_node_location(node):
    lc = getattr(node, "lc", None)
    if lc is None:
        return None, None
    return lc.line + 1, lc.col + 1
