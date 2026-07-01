# Workflow Contract

The reusable workflows accept repo-specific configuration as inputs. They must not hardcode repo paths from Enrichment or Cognitive.

Required primary workflow: `.github/workflows/pr-pipeline.yml`.

Required action versions by target spec:

- `actions/checkout@v6`
- `actions/setup-python@v6`

Required blocking jobs: validate, lint, semgrep, test, security.
Fail-soft/reporting jobs: sbom, scorecard, Codecov upload, safety, bandit warnings.
