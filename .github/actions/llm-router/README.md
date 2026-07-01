<!-- L9_META
l9_schema: 1
origin: l9-ci-core
layer: [ci, agent-review-loop, llm-router-shim]
tags: [L9_CI, agent-review-loop, llm-router]
owner: platform
status: active
/L9_META -->

# LLM-Router shim

`route.mjs` bridges the Python review agents to `@quantum-l9/llm-router`
(TaskDescriptor JSON in → RoutingResult JSON out). The SDK calls it via the
`L9_LLM_ROUTER_SHIM` env / `--shim` flag; when absent it degrades to a Null
route (advisory-only).

## Enable (blocked on issue #4 — LLM-Router install/auth)

```yaml
- name: Build router shim
  run: |
    cd .github/actions/llm-router
    npm i --no-audit --no-fund   # needs .npmrc + NODE_AUTH_TOKEN (read:packages)
  # then export L9_LLM_ROUTER_SHIM=$PWD/.github/actions/llm-router/route.mjs
```

Confirm the exact `@quantum-l9/llm-router` export shape (`route` fn vs default)
when resolving #4, then remove the defensive export probing in `route.mjs`.
