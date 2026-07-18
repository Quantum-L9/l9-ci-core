# Core Governance
## Purpose
Phase 3 controls when providers run, whether providers are required, which SDK
policy file is selected, and how results are rolled out.
It does not inspect findings or calculate gates.
## Profiles
- `pr_fast`
- `merge`
- `nightly`
- `release`
- `supply_chain`
Profiles resolve to an SDK execution profile, strictness, provider set,
provider requiredness, organization rollout mode, and optional SDK policy path.
## Modes
### Blocking
The result may become an organization-required check in Phase 4. SDK failures
already propagate as workflow failures.
### Advisory
The result may be published as informational in Phase 4. Invalid artifacts,
incompatible contracts, malformed provider reports, and internal SDK failures
remain fatal.
### Shadow
Artifacts are retained for evaluation, but Phase 4 must not publish a required
check.
### Disabled
The provider is not invoked. A provider marked required cannot be disabled.
## Waivers
Waivers are scope- and time-bounded governance records. They do not edit or
remove canonical findings. Expired or malformed waivers fail validation.
Every waiver requires:
- unique identifier;
- owner;
- reason;
- creation date;
- expiration date;
- explicit scope.
## Policy boundary
Core selects an SDK policy path. The SDK validates and interprets the policy.
Core does not parse policy semantics or reconstruct policy classifications.
