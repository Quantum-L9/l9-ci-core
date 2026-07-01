# Required Jobs

| Job | Required | Purpose |
| --- | --- | --- |
| validate | yes | Syntax, YAML, L9 scanner checks |
| lint | yes | Ruff and mypy |
| semgrep | yes | Policy check when config exists |
| test | yes | Pytest with coverage |
| security | yes | Gitleaks, pip-audit, dependency review |
| sbom | no | Artifact generation |
| scorecard | no | OpenSSF scorecard |
| ci-gate | yes | Fan-in evaluator |
