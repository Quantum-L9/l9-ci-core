# l9-ci-core repository implementation.
#
# The generated root Makefile owns the common operator vocabulary and delegates
# every repository capability here. This file owns only l9-ci-core-specific
# execution. It must not implement organization governance: publication lives
# behind `make pr` -> `l9 pr` -> Cursor-Governance, and this file therefore
# defines no `pr` target and no `push` target.
PYTHON ?= python3
L9_REPO := $(PYTHON) -m tools.l9_repo --workspace "$(CURDIR)"

.PHONY: \
	repo-setup \
	repo-validate \
	repo-check \
	repo-test \
	repo-clean \
	repo-doctor \
	change-policy \
	agent-check \
	status \
	reconcile \
	attest-control-plane \
	check-release-writers

# ---------------------------------------------------------------------------
# Common facade implementation
# ---------------------------------------------------------------------------
repo-setup:
	@$(L9_REPO) setup

repo-validate:
	@$(L9_REPO) validate

repo-check:
	@$(L9_REPO) check

repo-test:
	@$(L9_REPO) test

repo-clean:
	@$(L9_REPO) clean

repo-doctor:
	@$(L9_REPO) doctor

# ---------------------------------------------------------------------------
# l9-ci-core-specific capabilities
# ---------------------------------------------------------------------------
change-policy: ## Show Core change-policy obligations
	@$(L9_REPO) change-policy

agent-check: ## Run Core evidence-bearing completion checks
	@$(L9_REPO) agent-check

status: ## Show Core-local repository state
	@$(L9_REPO) status

reconcile: ## Regenerate the root Makefile from the canonical template
	@$(L9_REPO) reconcile

# Read-only attestation of the live GitHub control plane against
# .l9/release-plane.yaml. Needs a credential in L9_CONTROL_PLANE_TOKEN,
# GH_TOKEN, or GITHUB_TOKEN; exits non-zero unless every check is PASS.
attest-control-plane: ## Attest the live GitHub control plane
	@$(PYTHON) tools/verify_control_plane.py

# Namespace-aware release-writer uniqueness: exactly one authorized writer for
# exact vX.Y.Z releases and one for the transitional v2 installer tag.
check-release-writers: ## Validate release-writer namespace uniqueness
	@$(PYTHON) tools/check_release_writers.py
