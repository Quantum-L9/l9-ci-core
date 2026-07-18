# Agent Instructions
This repository is a thin control plane.
Before changing files:
1. Read `.l9/architecture.yaml`.
2. Read `.l9/ownership.yaml`.
3. Read `.l9/sdk-compatibility.yaml`.
4. Preserve the one-way dependency from Core to SDK.
5. Do not implement SDK-owned behavior in Core.
6. Do not introduce floating dependencies.
7. Do not add analysis workflows before Phase 2 is authorized.
8. Run the complete standard-library test suite.
A change that duplicates SDK behavior is invalid even when all functional tests
pass.
