# l9-ci-core repository-owned Make extensions.
#
# Core's generated standard bindings live in Repo.mk. This local layer owns only
# Core-specific validation and release-assurance helpers. It must not override a
# generated target or define publication targets: make pr remains a delegation to
# Cursor-Governance through the root Makefile.
L9_REPO ?= $(PYTHON) -m tools.l9_repo
L9_MAKE := $(PYTHON) -m tools.l9_make

.PHONY: \
	change-policy \
	agent-check \
	reconcile \
	make-render \
	make-check \
	attest-control-plane \
	check-release-writers

change-policy: ## Show Core change-policy obligations
	@$(L9_REPO) change-policy

agent-check: ## Run Core evidence-bearing completion checks
	@$(L9_REPO) agent-check

reconcile: ## Regenerate both Make artifacts from the authoritative capability plan
	@$(L9_REPO) reconcile

make-render: ## Render generated Makefile and Repo.mk from Core's approved capability plan
	@$(L9_MAKE) render --plan tools/l9_make/default-capability-plan.json --output Repo.mk --local Repo.local.mk --makefile Makefile

make-check: ## Verify generated Make artifacts and local target boundaries
	@$(L9_MAKE) check --plan tools/l9_make/default-capability-plan.json --output Repo.mk --local Repo.local.mk --makefile Makefile

# Read-only attestation of the live GitHub control plane against
# .l9/release-plane.yaml. Needs a credential in L9_CONTROL_PLANE_TOKEN,
# GH_TOKEN, or GITHUB_TOKEN; exits non-zero unless every check is PASS.
attest-control-plane: ## Attest the live GitHub control plane
	@$(PYTHON) tools/verify_control_plane.py

# Namespace-aware release-writer uniqueness: exactly one authorized writer for
# exact vX.Y.Z releases and one for the transitional v2 installer tag.
check-release-writers: ## Validate release-writer namespace uniqueness
	@$(PYTHON) tools/check_release_writers.py
