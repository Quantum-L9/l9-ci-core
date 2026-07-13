"""L9 CI Core.

Installable Python package for the declarative gate control plane. The
``control_plane`` subpackage holds the typed primitives (canonical JSON,
digests, models, schemas) and the stage skeletons (context, changed-files,
risk, registry, planner, executor, results, evaluator) that are fleshed out
across the PR-B control-plane series.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
