<!-- L9_META
l9_schema: 1
parent: l9-skill-compiler
layer: reference
role: coding_stack_kernel
tags: [skill, constellation, transport_packet, gate, node]
owner: igor_beylin
status: active
version: 1.0.0
updated: 2026-07-04
/L9_META -->

# Coding Stack Kernel (Constellation Node)

## Cardinal Rule

`TransportPacket` is the ONLY wire format. `PacketEnvelope` is dead. No fallback. Violate any law → revert and ask.

## Use When

Consult when generating code for L9 Constellation nodes, handlers, or transport layers.

## Layer 0 — Transport Contract (Non-negotiable)

- **LAW-T1:** `TransportPacket` only. No legacy request adapters.
- **LAW-T2:** Handler signature MUST be `async def handler(packet: TransportPacket) -> TransportPacket`.
- **LAW-T3:** Gate-only egress. Nodes MUST only send follow-up work to `GATE_URL`.
- **LAW-T4:** Semantic change uses `derive()`. Observational change uses `with_hop()`.
- **LAW-T5:** Immutability. `TransportPacket` is immutable — no in-place mutation.
- **LAW-T6:** Transport hash stability. `hop_trace` is excluded from hash.
- **LAW-T7:** Schema validation before execution.

## Layer 1 — Routing Law (Non-negotiable)

- **LAW-R1:** Gate is routing authority.
- **LAW-R2:** Node-to-node calls are forbidden.
- **LAW-R3:** Node-origin packets must route to gate.
- **LAW-R4:** No peer awareness in workers.

## Layer 2 — Authority Boundary

- **Gate:** Workflow-stateless transport authority. Owns routing, admission, resilience.
- **Orchestrator:** Workflow authority. Owns DAG, step state, execution history.
- **Runtime Node:** Execution-only. Owns local state, caches, tool state. Forbidden from routing or branching.

## Anti-Patterns (Hard Failures)

- `PacketEnvelope` imports anywhere.
- Alternate packet types or envelopes.
- Direct node-to-node calls.
- In-place packet mutation.
- Custom transport routing logic in node.
- Workflow logic in Gate.
- Workflow DAG in runtime node.
- `eval()`, `exec()`, `compile()` usage.
