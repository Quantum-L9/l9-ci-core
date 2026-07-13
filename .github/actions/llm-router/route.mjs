#!/usr/bin/env node
// L9_META: origin=l9-ci-core; layer=[ci,agent-review-loop,llm-router-shim]; status=active
//
// Router shim for the L9 Agent Review Loop. Reads a TaskDescriptor JSON on
// stdin and writes a RoutingResult JSON on stdout, resolving the model via
// @quantum-l9/llm-router. On ANY error it emits a null route so the Python
// review agent degrades to advisory (never blocks on routing).
//
// PENDING: install/auth for @quantum-l9/llm-router — see l9-ci-core issue #4.
// Until the package resolves in CI, the SDK skips this shim and runs Null.
let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (d) => (input += d));
process.stdin.on("end", async () => {
  const nullRoute = (reason) =>
    process.stdout.write(JSON.stringify({ provider: "null", reason }));
  try {
    const task = JSON.parse(input || "{}");
    const mod = await import("@quantum-l9/llm-router");
    // Support a few plausible export shapes; confirm against the package in #4.
    const router = mod.createRouter ? mod.createRouter() : mod.default || mod;
    const routeFn = mod.route || (router && router.route);
    if (typeof routeFn !== "function") return nullRoute("router_export_unknown");
    const result = await routeFn.call(router, task);
    process.stdout.write(JSON.stringify(result));
  } catch (e) {
    nullRoute("shim_error:" + (e && e.message ? e.message : "unknown"));
  }
});
