<!-- L9_META
l9_schema: 1
origin: l9-ci-core
layer: [docs, ci, classifier]
tags: [L9_TEMPLATE, classifier, governance]
owner: platform
status: active
/L9_META -->

# Classifier Contract

The PR classifier is a policy-driven engine. Python owns parsing and deterministic matching. Governance YAML owns taxonomy, file extension groups, sensitive path groups, priority order, and routing meaning.

## Source of Truth

```text
.github/governance/l9-ci-shared-spec.yaml
.github/governance/label-taxonomy.yaml
.github/governance/ci-routing-policy.yaml
```

`classify_pr.py` must not hardcode language extension groups or semantic routing classes. Adding a future language such as Go, Rust, Zig, or TypeScript is a YAML policy change, not an SDK/script release.

## Canonical Classes

```text
docs_only
ci_workflow
dependency_python
app_code
security
compliance
unknown_diff
```

`unknown_diff` fails closed.

## Input Order

1. CLI file paths.
2. `CHANGED_FILES` environment variable, comma or newline separated.
3. `stdin`, newline separated.

## Policy Matching

The classifier supports these YAML matchers per taxonomy group:

```yaml
suffixes: []
prefixes: []
exact: []
contains: []
parts: []
name_prefix_suffix:
  - prefix: requirements-
    suffix: .txt
```

Multiple groups may match a file. Final class selection follows the YAML `classifier.priority` list, except a pure docs-only set returns `docs_only`.

## Fail-Closed Rules

The classifier must return `unknown_diff` when:

- no changed files are supplied
- any path cannot be classified
- no canonical class matches
- policy YAML is missing or malformed
- policy references a non-canonical class

## CLI

```bash
python .github/scripts/classify_pr.py engine/handlers.py
python .github/scripts/classify_pr.py --plain README.md
python .github/scripts/classify_pr.py --config .github/governance/l9-ci-shared-spec.yaml pyproject.toml
```

## Validation

Required tests prove:

- canonical classes load from YAML
- existing classifications still work
- a new language extension can be added by YAML only
- priority can be changed by YAML only
- malformed policy fails closed
