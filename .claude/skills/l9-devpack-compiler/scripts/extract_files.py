#!/usr/bin/env python3
"""Extract + assemble: materialize a spec's file_plan into a target filetree.

Pulls the REAL contents of `extract`/`existing` files out of the source docs
(markdown fenced code blocks keyed by `FILE: <path>` or a filename line/heading),
organizes them into `<out>/target/<path>`, maps made-vs-remaining, scopes the
work queue to the delta, and records the signals each present file generates.
The extracted files ship inside the pack.

Doctrine (matches the rest of the skill):
  - extracted files are REAL content, verified against their contract; they are
    included in the pack.
  - build/adapt files are map-only — never stubbed.
  - a spec file with no source block -> `missing_source` (reported, not invented).
  - a doc block not in the spec -> `unplanned` (reported).
  - obvious corruption in an extracted block (smart quotes, `...` elision) ->
    `needs_review` signal; code is never silently rewritten.

Outputs under --out: target/<files>, FILETREE.md, .ai/filetree.yaml,
.ai/file-signals.yaml.

Exit codes: 0 assembled (no missing_source), 1 gaps to resolve
(missing_source/unplanned/needs_review), 2 unreadable input.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# A `FILE: <path>` marker (Build_2 / contract-doc style; content follows unfenced
# until the next marker). Real source docs overwhelmingly use this, NOT fences.
_FILE_LINE = re.compile(r"^\s*FILE:\s*(\S.*?)\s*$")
# A bare path or a `#`-heading naming a file, sitting just above a fence
# (fenced-doc style; kept as a fallback for docs that DO use fences).
_HEADING_PATH = re.compile(r"^\s*#{0,6}\s*`?([A-Za-z0-9_][A-Za-z0-9_./-]*\.[A-Za-z0-9_]+)`?\s*$")
_FENCE = re.compile(r"^\s*```")
_SMART = ("‘", "’", "“", "”")  # ' ' " "  curly quotes
# Code punctuation — a trailing line carrying any of these is real content, not a
# prose section heading, so trailing-prose trimming stops at it.
_CODE_PUNCT = set(";{}=<>()[]|&$\"'`")


def _lang_for(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return {
        "ts": "typescript",
        "js": "javascript",
        "sql": "sql",
        "json": "json",
        "toml": "toml",
        "sh": "bash",
        "py": "python",
        "yaml": "yaml",
        "yml": "yaml",
        "md": "markdown",
    }.get(ext, ext)


def _is_prose_heading(s: str) -> bool:
    """A group-boundary section heading, not code — conservative on purpose."""
    if not s or any(c in _CODE_PUNCT for c in s):
        return False
    if any(g in s for g in ("├", "└", "│", "#")):
        return False
    return len(s.split()) >= 2  # a lone token (esac/done/fi) is code, not prose


def _trim_trailing_prose(body: list[str]) -> list[str]:
    """Drop trailing blanks and an *isolated* prose section heading.

    `FILE:` blocks run to the next marker, so a group-boundary heading between the
    last file's content and the next marker ("Shared Edge Function modules") gets
    swept into the previous block. Only strip a trailing prose line that stands
    alone — the line above it is blank — so contiguous code (`supabase status`,
    `echo ...`) is never mistaken for a heading.
    """
    out = list(body)
    while out and not out[-1].strip():  # trailing blanks
        out.pop()
    # isolation: a real heading island is [<code>, <blank>, <heading>]
    while len(out) >= 2 and not out[-2].strip() and _is_prose_heading(out[-1].strip()):
        out.pop()
        while out and not out[-1].strip():
            out.pop()
    return out


def _load(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except Exception:
        try:
            import yaml  # type: ignore[import-not-found]

            return yaml.safe_load(text)
        except Exception as exc:
            raise ValueError(f"cannot parse {path.name} as json/yaml: {exc}") from exc


def _doc_paths(docs: list[str]) -> list[Path]:
    out: list[Path] = []
    for d in docs:
        p = Path(d)
        if p.is_dir():
            out += sorted(p.rglob("*.md"))
        elif p.exists():
            out.append(p)
    return out


def _add(found: dict[str, dict[str, Any]], path: str, content: str, lang: str, doc: str) -> None:
    # first marked block wins (don't clobber with later restatements/summaries)
    found.setdefault(path, {"content": content, "lang": lang, "source_doc": doc})


def _extract_file_markers(lines: list[str], doc: str, found: dict[str, dict[str, Any]]) -> None:
    """`FILE: <path>` docs: content follows unfenced until the next marker/EOF.

    If a fence immediately follows the marker, the fenced body is used instead.
    """
    markers = [i for i, ln in enumerate(lines) if _FILE_LINE.match(ln)]
    for idx, mi in enumerate(markers):
        path = _FILE_LINE.match(lines[mi]).group(1).lstrip("./")  # type: ignore[union-attr]
        end = markers[idx + 1] if idx + 1 < len(markers) else len(lines)
        j = mi + 1
        while j < end and not lines[j].strip():  # skip blanks after the marker
            j += 1
        if j < end and _FENCE.match(lines[j]):  # fenced content under the marker
            lang = lines[j].strip().lstrip("`").strip() or _lang_for(path)
            body: list[str] = []
            k = j + 1
            while k < end and not _FENCE.match(lines[k]):
                body.append(lines[k])
                k += 1
            _add(found, path, "\n".join(body) + "\n", lang, doc)
        else:  # unfenced: run to the next marker, then trim group-boundary prose
            body = _trim_trailing_prose(lines[j:end])
            if body:
                _add(found, path, "\n".join(body) + "\n", _lang_for(path), doc)


def _extract_fenced(lines: list[str], doc: str, found: dict[str, dict[str, Any]]) -> None:
    """Fenced docs: a path/heading line sits just above a ``` fence."""
    i = 0
    while i < len(lines):
        m = _HEADING_PATH.match(lines[i])
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if m and j < len(lines) and _FENCE.match(lines[j]):
            path = m.group(1).lstrip("./")
            lang = lines[j].strip().lstrip("`").strip() or _lang_for(path)
            body: list[str] = []
            k = j + 1
            while k < len(lines) and not _FENCE.match(lines[k]):
                body.append(lines[k])
                k += 1
            _add(found, path, "\n".join(body) + "\n", lang, doc)
            i = k + 1
            continue
        i += 1


def extract_blocks(doc_files: list[Path]) -> dict[str, dict[str, Any]]:
    """Return {path: {content, lang, source_doc}} for every marked block.

    A doc that carries `FILE:` markers is parsed as unfenced-marker style (the real
    source-doc convention); otherwise it is scanned for path/heading-above-fence
    blocks. Real docs rarely fence, so the marker path is primary.
    """
    found: dict[str, dict[str, Any]] = {}
    for doc in doc_files:
        lines = doc.read_text(encoding="utf-8", errors="ignore").splitlines()
        if any(_FILE_LINE.match(ln) for ln in lines):
            _extract_file_markers(lines, doc.name, found)
        else:
            _extract_fenced(lines, doc.name, found)
    return found


def _match(path: str, blocks: dict[str, dict[str, Any]]) -> str | None:
    if path in blocks:
        return path
    if "*" in path:  # dir/glob spec entry -> any block under the prefix
        prefix = path.split("*", 1)[0]
        for bp in blocks:
            if bp.startswith(prefix):
                return bp
        return None
    if path.endswith("/"):
        for bp in blocks:
            if bp.startswith(path):
                return bp
        return None
    base = path.rsplit("/", 1)[-1]  # basename fallback
    for bp in blocks:
        if bp.rsplit("/", 1)[-1] == base:
            return bp
    return None


def _corruption(content: str) -> str | None:
    if any(q in content for q in _SMART):
        return "smart-quotes present (likely doc copy corruption)"
    if re.search(r"^\s*\.\.\.\s*$", content, re.M):
        return "`...` elision present"
    return None


def _signals_for(
    entry: dict[str, Any],
    spec: dict[str, Any],
    state: str,
    src: str | None,
    needs_review: str | None,
) -> dict[str, Any]:
    path = entry.get("path", "")
    contract = entry.get("contract_ref")
    invs = [
        inv["id"]
        for inv in spec.get("invariants", []) or []
        if isinstance(inv, dict) and path.rsplit("/", 1)[-1] in str(inv.get("enforcement", ""))
    ]
    validated_by = [
        c["name"]
        for c in spec.get("commands", []) or []
        if isinstance(c, dict) and path.rsplit("/", 1)[-1] in str(c.get("cmd", ""))
    ]
    layer = None
    if path.startswith("contracts/") or path.endswith(".schema.json"):
        layer = "L4-contract"
    elif path.startswith("supabase/") or path.startswith("src/") or path.startswith("actions/"):
        layer = "impl"
    elif path.startswith(".github/") or path.startswith(".ai/") or path.startswith(".l9/"):
        layer = "L1/L4"
    sig: dict[str, Any] = {"path": path, "state": state}
    if contract:
        sig["satisfies_contract"] = contract
    if invs:
        sig["enforces_invariants"] = invs
    if validated_by:
        sig["validated_by"] = validated_by
    if layer:
        sig["populates_layer"] = layer
    if src:
        sig["source_doc"] = src
    if needs_review:
        sig["needs_review"] = needs_review
    return sig


def assemble(
    spec: dict[str, Any], blocks: dict[str, dict[str, Any]], out: Path, repo: Path | None
) -> dict[str, Any]:
    tree: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []
    matched_blocks: set[str] = set()
    written = 0

    for entry in spec.get("file_plan", []) or []:
        path = entry.get("path", "")
        status = entry.get("status", "build")
        if status in ("extract", "existing"):
            bp = _match(path, blocks)
            if bp:
                matched_blocks.add(bp)
                content = blocks[bp]["content"]
                review = _corruption(content)
                dest = out / "target" / bp
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8")
                written += 1
                state = "made" if not review else "made_needs_review"
                tree.append({"path": bp, "state": state, "status": status})
                signals.append(
                    _signals_for(
                        {**entry, "path": bp}, spec, state, blocks[bp]["source_doc"], review
                    )
                )
            elif status == "existing" and repo and (repo / path).exists():
                tree.append({"path": path, "state": "made", "status": "existing-in-repo"})
                signals.append(_signals_for(entry, spec, "made", "repo", None))
            else:
                state = "missing_source"
                tree.append({"path": path, "state": state, "status": status})
                signals.append(_signals_for(entry, spec, state, None, None))
                remaining.append(
                    {"path": path, "reason": "declared extract/existing but no source block found"}
                )
        elif status == "deferred":
            tree.append({"path": path, "state": "deferred", "status": status})
        else:  # build | adapt -> map-only, no file written
            tree.append({"path": path, "state": "remaining", "status": status})
            remaining.append({"path": path, "reason": f"{status}: build the remainder"})

    unplanned = sorted(set(blocks) - matched_blocks)
    return {
        "written": written,
        "tree": tree,
        "signals": signals,
        "remaining": remaining,
        "unplanned": unplanned,
    }


def _emit(out: Path, spec: dict[str, Any], result: dict[str, Any]) -> None:
    ai = out / ".ai"
    ai.mkdir(parents=True, exist_ok=True)
    # filetree map (yaml-ish, but json is valid yaml -> portable without PyYAML)
    (ai / "filetree.yaml").write_text(
        "# target filetree — made vs remaining (json is valid yaml)\n"
        + json.dumps({"filetree": result["tree"]}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    (ai / "file-signals.yaml").write_text(
        "# per-file signals (json is valid yaml)\n"
        + json.dumps({"file_signals": result["signals"]}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    made = [t for t in result["tree"] if t["state"].startswith("made")]
    lines = ["# Target Filetree", "", "## Made (extracted / existing)", ""]
    lines += [f"- `{t['path']}`  ({t['state']})" for t in made] or ["- (none)"]
    lines += ["", "## Remaining to build", ""]
    lines += [f"- `{r['path']}` — {r['reason']}" for r in result["remaining"]] or ["- (none)"]
    if result["unplanned"]:
        lines += ["", "## Unplanned (in docs, not in spec — resolve)", ""]
        lines += [f"- `{p}`" for p in result["unplanned"]]
    (out / "FILETREE.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract + assemble a spec's file_plan into a pack."
    )
    parser.add_argument("spec", help="Path to a build spec (json/yaml)")
    parser.add_argument("--docs", nargs="+", required=True, help="Source docs (dirs or files)")
    parser.add_argument("--out", required=True, help="Pack output dir")
    parser.add_argument("--repo", default=None, help="Optional in-scope repo root")
    parser.add_argument("--json", action="store_true", help="Emit the full report as JSON")
    args = parser.parse_args()
    spec_path, out = Path(args.spec), Path(args.out)
    repo = Path(args.repo) if args.repo else None
    if not spec_path.is_file():
        print(f"FAIL: not a file: {spec_path}", file=sys.stderr)
        return 2
    try:
        spec = _load(spec_path)
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    blocks = extract_blocks(_doc_paths(args.docs))
    result = assemble(spec, blocks, out, repo)
    _emit(out, spec, result)

    review = [s for s in result["signals"] if s.get("needs_review")]
    if args.json:
        print(
            json.dumps(
                {k: result[k] for k in ("written", "remaining", "unplanned")}
                | {"needs_review": review},
                indent=2,
            )
        )
    else:
        print(f"extracted {result['written']} file(s) into {out}/target/")
        print(
            f"remaining_to_build: {len(result['remaining'])} · unplanned: {len(result['unplanned'])} "
            f"· needs_review: {len(review)}"
        )
        for r in result["remaining"]:
            print(f"  - remaining: {r['path']} — {r['reason']}")
        for p in result["unplanned"]:
            print(f"  - unplanned: {p}")
        for s in review:
            print(f"  - needs_review: {s['path']} — {s['needs_review']}")
    gaps = (
        any(t["state"] == "missing_source" for t in result["tree"])
        or bool(review)
        or bool(result["unplanned"])
    )
    return 1 if gaps else 0


if __name__ == "__main__":
    raise SystemExit(main())
