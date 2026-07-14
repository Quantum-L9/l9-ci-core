from __future__ import annotations
from pathlib import Path
from typing import Any, Generator, Iterator
from . import limits as _lim


def iter_workflow_files(workflow_dir=None, root=None) -> Generator[Path, None, None]:
    from .paths import repo_root as _rr

    if root is None:
        root = _rr()
    root = Path(root).resolve()
    if workflow_dir is None:
        workflow_dir = root / ".github" / "workflows"
    wdir = Path(workflow_dir).resolve()
    count = 0
    total = 0
    for p in sorted(wdir.iterdir()):
        if p.suffix.lower() not in (".yml", ".yaml"):
            continue
        # A symlink under .github/workflows is an ambiguous execution surface:
        # GitHub may consume it while a skipping validator would leave it
        # unvalidated. The governing contract requires fail-closed rejection,
        # not a silent skip.
        if p.is_symlink():
            raise ValueError(f"Symlinked workflow file is forbidden: {p}")
        if not p.is_file():
            continue
        count += 1
        if count > _lim.MAX_WORKFLOW_FILES:
            raise ValueError(f"Too many workflow files (limit {_lim.MAX_WORKFLOW_FILES})")
        file_size = p.stat().st_size
        # Per-file hard cap (hardening): reject any single oversized workflow
        # before it is parsed, independent of the aggregate scan budget.
        if file_size > _lim.MAX_WORKFLOW_FILE_BYTES:
            raise ValueError(f"{p} exceeds per-file workflow limit {_lim.MAX_WORKFLOW_FILE_BYTES}")
        total += file_size
        if total > _lim.MAX_TOTAL_SCAN_BYTES:
            raise ValueError(f"Total scan bytes exceeded limit {_lim.MAX_TOTAL_SCAN_BYTES}")
        yield p


def iter_jobs(workflow) -> Iterator[tuple[str, Any]]:
    jobs = workflow.get("jobs") or {}
    for jid, job in jobs.items():
        if isinstance(job, dict):
            yield str(jid), job


def iter_steps(job) -> Iterator[tuple[int, Any]]:
    for i, step in enumerate(job.get("steps") or []):
        if isinstance(step, dict):
            yield i, step


def _trailing_comment(node, key) -> str:
    """Return the trailing end-of-line comment on ``node[key]`` if any.

    Used to read the human-readable version annotation on a pinned ``uses:``
    scalar, e.g. ``uses: actions/checkout@<sha> # v4.2.2`` -> ``v4.2.2``.
    """
    ca = getattr(node, "ca", None)
    if not ca or not getattr(ca, "items", None):
        return ""
    tokens = ca.items.get(key)
    if not tokens:
        return ""
    for tok in tokens:
        val = getattr(tok, "value", None)
        if val:
            return val.lstrip("#").strip()
    return ""


def iter_uses_references(workflow) -> Iterator[tuple[str, int, str, str, int, str]]:
    for jid, job in iter_jobs(workflow):
        if "uses" in job:
            lc = getattr(job, "lc", None)
            line = (lc.line + 1) if lc else 0
            yield jid, -1, f"[job:{jid}]", str(job["uses"]), line, _trailing_comment(job, "uses")
        for idx, step in iter_steps(job):
            if "uses" in step:
                lc = getattr(step, "lc", None)
                line = (lc.line + 1) if lc else 0
                yield (
                    jid,
                    idx,
                    str(step.get("name", f"step-{idx}")),
                    str(step["uses"]),
                    line,
                    _trailing_comment(step, "uses"),
                )


def iter_run_blocks(workflow) -> Iterator[tuple[str, int, str, str, int]]:
    for jid, job in iter_jobs(workflow):
        for idx, step in iter_steps(job):
            if "run" in step:
                rv = str(step["run"])
                if len(rv.encode("utf-8")) > _lim.MAX_RUN_BLOCK_BYTES:
                    raise ValueError(f"run block in {jid}/step-{idx} exceeds limit")
                lc = getattr(step, "lc", None)
                line = (lc.line + 1) if lc else 0
                yield jid, idx, str(step.get("name", f"step-{idx}")), rv, line
