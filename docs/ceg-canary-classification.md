# CEG Analyze classification

Read on 2026-11-22 from the open Cognitive.Engine.Graphs pull requests. The promotion canary leg `Canary Quantum-L9/Cognitive.Engine.Graphs` is already success on l9-ci-core `ab489297c32a0ef8e0b10058b781b42a958d5cac`.

| PR | Analyze | Subject |
|---|---|---|
| 517 | success | No upstream judge defect. A cancelled `pr / body` check remains. |
| 535 | fail | `make validate` exits 2 because the planted witness `engine/middleware.py` exists. |
| 534 | fail | `make check` exits 2 on the planted ruff T201 `print` in `engine/causal/attribution.py`. |
| 532 | fail | `make validate` exits 2 on the planted SEC-001 finding in `chassis/health.py:143`. |
| 528 | fail | Ran at 2026-11-22T01:56:09Z against Core `d50e4fada28df32163ad5844f2d5e3f5b7e42543`. The expense judge aborted on `$(if $(wildcard $(VENV)` in `Repo.mk` and on missing `scripts/docs_sync/validate_api_docs.py`, which the docs workflow calls only when the file exists. |

528 is the only pull request whose Analyze failure is an upstream judge defect. 532, 534, and 535 fail on the planted witness. Those witnesses are not patches in Cognitive.Engine.Graphs.
