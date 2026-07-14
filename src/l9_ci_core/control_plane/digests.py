"""Policy digests: source (exact bytes) and semantic (canonicalized content).

Every policy file (gate registry, risk tiers, rule modes) is bound to plans
and canonical evidence by two digests:

* **source digest** — sha256 over the exact file bytes. Retained for audit;
  changes on any byte, including cosmetic whitespace.
* **semantic digest** — the file is safely parsed (YAML 1.2, no arbitrary
  object construction), canonicalized to JSON, then hashed. Stable across
  cosmetic reflow, sensitive to any meaningful content change.

Canonical evidence and evaluator comparisons use the **semantic** digest as
policy identity; the source digest is recorded alongside for forensics.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from .canonical_json import canonical_bytes, sha256_hex

__all__ = [
    "parse_yaml_safe",
    "source_digest",
    "semantic_digest",
    "policy_digests",
]


def _safe_yaml() -> YAML:
    # typ="safe" constructs only plain Python scalars/lists/dicts — never
    # arbitrary objects — which is exactly the trust boundary we want for
    # untrusted-ish policy files.
    yaml = YAML(typ="safe", pure=True)
    return yaml


def parse_yaml_safe(text: str, *, source: str = "<string>") -> Any:
    """Parse YAML text into plain Python data, safely."""
    try:
        return _safe_yaml().load(io.StringIO(text))
    except Exception as exc:  # noqa: BLE001 - re-raised with source context
        raise ValueError(f"YAML parse error in {source}: {exc}") from exc


def source_digest(path: str | Path) -> str:
    """``"sha256:<hex>"`` over the exact file bytes of ``path``."""
    data = Path(path).read_bytes()
    return "sha256:" + sha256_hex(data)


def semantic_digest(parsed: Any) -> str:
    """``"sha256:<hex>"`` over the canonical JSON of already-parsed data."""
    return "sha256:" + sha256_hex(canonical_bytes(parsed))


def policy_digests(path: str | Path) -> tuple[str, str, Any]:
    """Return ``(source_digest, semantic_digest, parsed)`` for a policy file.

    Reads the file once so the two digests describe the same bytes.
    """
    p = Path(path)
    raw = p.read_bytes()
    src = "sha256:" + sha256_hex(raw)
    parsed = parse_yaml_safe(raw.decode("utf-8"), source=str(p))
    sem = semantic_digest(parsed)
    return src, sem, parsed
