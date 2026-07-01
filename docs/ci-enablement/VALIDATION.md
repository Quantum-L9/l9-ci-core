# VALIDATION — CI enablement (l9-ci-core)

Local evidence gathered before opening the PR (repo root, git checkout, matching CI).

## Blocking gate — pytest with coverage

```
$ pytest -q --cov=.github/scripts --cov-report=xml --cov-report=term-missing
Name                             Stmts   Miss  Cover
.github/scripts/classify_pr.py     191     58    70%
30 passed
```

`coverage.xml` written for Sonar import.

## Blocking gate — payload schema meta-validation

```
$ python -c "import json;from jsonschema import Draft202012Validator; \
    Draft202012Validator.check_schema(json.load(open('schemas/agent-review-payload.schema.json')))"
check_schema OK; declared $schema: https://json-schema.org/draft/2020-12/schema
required fields: 14
```

A real payload emitted by `l9-ci gate --emit-agent-payload` was separately
confirmed to `jsonschema.validate` cleanly against this schema.

## Advisory gates (measured)

```
$ ruff check .          -> All checks passed!  (kept advisory)
$ mypy .github/scripts  -> pre-existing findings (advisory)
```

## Workflow structure

`pr-checks.yml`, `pr-repair.yml`, `agent-payload-contract.yml` each parse as YAML
with `on`, `permissions`, `jobs`, and steps in every job. The existing
`test_workflows.py::test_all_workflows_parse_as_yaml` also covers the new files
(all 30 tests pass with them present).

## Secret / fork safety

- `gitguardian`/`sonar` restricted to same-repo events; detect their secret and
  skip when absent. The real-payload step is guarded on `L9_CI_INSTALL_SPEC`.
- No `pull_request_target`. Diff grepped — no secrets committed.

## Unknowns confirmed labeled

`sonar.projectKey`/`organization` = `UNKNOWN_…`; `L9_CI_INSTALL_SPEC`, `SDK_TOKEN`
visibility, and the PR_Repair dispatch handler enumerated in MANIFEST.md.
