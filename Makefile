# GENERATED - Quantum-L9 repository Make facade.
#
# This file is the portable operator vocabulary. It owns no implementation.
#   - Repository capabilities are implemented in Repo.mk as repo-* leaves.
#   - Cross-repository governance is delegated to the `l9` dispatcher, which
#     resolves CONSUMER_SAFE targets against the Cursor-Governance Makefile.
#
# Do not place product logic, Git publication logic, or GitHub API logic here.
# Run `make reconcile` to restore canonical form. If this file is unparseable,
# recover with: python3 -m tools.l9_repo reconcile
.DEFAULT_GOAL := help
SHELL := /usr/bin/env bash
.SHELLFLAGS := -eu -o pipefail -c
L9 ?= l9

# Required repository-owned implementation boundary. This is `include`, not
# `-include`: a missing Repo.mk is a broken repository, not a silent degrade.
include Repo.mk

.PHONY: \
	help \
	setup \
	validate \
	check \
	test \
	clean \
	doctor \
	wiring-check \
	start \
	workspace-clean \
	pr

help: ## Show repository commands
	@awk '\
		BEGIN { FS = ":.*## "; } \
		/^[A-Za-z0-9_.-]+:.*## / { \
			printf "  %-24s %s\n", $$1, $$2; \
		}' $(MAKEFILE_LIST)

# ---------------------------------------------------------------------------
# Repository-local capabilities - implemented by Repo.mk
# ---------------------------------------------------------------------------
setup: repo-setup ## Install/bootstrap repository dependencies
validate: repo-validate ## Run the repository canonical validation
check: repo-check ## Run repository static/quality checks
test: repo-test ## Run repository tests
clean: repo-clean ## Remove repository-local disposable outputs
doctor: repo-doctor ## Verify the local execution toolchain

# ---------------------------------------------------------------------------
# Cross-repository governance - this facade owns no implementation here
# ---------------------------------------------------------------------------
wiring-check: ## Verify Governance wiring for this repository
	@$(L9) wiring-check

start: ## Run the governed session-start pipeline
	@$(L9) start

workspace-clean: ## Run governed workspace reconciliation
	@$(L9) workspace-clean

pr: ## Gate and publish through Cursor-Governance
	@$(L9) pr
