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

## 2026-11-22 correction: #357 turned an abort into a false ZERO_TESTS

#357 stopped the judge aborting on `$(if $(wildcard $(VENV)/bin/python),$(VENV)/bin/python,python3)` by deleting every make function call during expansion. The Cognitive.Engine.Graphs `repo-test` recipe is `PYTHONPATH=... $(REPO_PY) -m pytest tests/ -m unit`, so `$(REPO_PY)` expanded to nothing, the command word vanished, and the leading `PYTHONPATH=` assignment made the shell classifier drop the whole statement. The judge saw no test command and reported `ZERO_TESTS no bounded nonempty fast selection` on every pull request (528, 538, 539, 540, 541, 542, 543), whatever the diff.

Three judge defects, all in Core, are repaired in `tools/pr_expense_guard.py`:

| Defect | Before | After |
|---|---|---|
| Make function expansion | deleted, leaving an empty command word | `$(if C,A,B)` resolves when both branches are equal or are the same Python interpreter; any other function stays an explicit `$(make-function)` token, and a test recipe whose command word is one is `UNKNOWN_SCRIPT` |
| Leading `NAME=value` / `env` prefix | the statement was dropped, so `CI=1 pytest tests/` was invisible | the prefix is stripped and the command is classified; array appends and command-substitution assignments are still not read as commands |
| `make test` that classifies nothing | `ZERO_TESTS` | `UNKNOWN_SCRIPT make test runs no command the judge can classify: <recipe>` |

With the repaired judge, Cognitive.Engine.Graphs `main@9d17d01` is judged `REPO_TEST_FULL pytest tests/`: a marker filter over the whole `tests/` tree is a whole-tree suite under the judge's existing policy. That verdict is the policy, not a defect. It is a consumer or Core policy decision and is not patched in Cognitive.Engine.Graphs from Core.
