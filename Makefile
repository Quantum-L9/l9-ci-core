# GENERATED - Quantum-L9 repository Make bootstrap.
#
# This stable root file owns only operator vocabulary and governance delegation.
#   - Core-approved repository bindings are generated into Repo.mk.
#   - Repository-native extensions live in Repo.local.mk.
#   - Cross-repository governance is delegated to the l9 dispatcher.
#
# Do not place product logic, Git publication logic, or GitHub API logic here.
# Recover this bootstrap with: python3 -m tools.l9_repo reconcile
.DEFAULT_GOAL := help
SHELL := /usr/bin/env bash
.SHELLFLAGS := -eu -o pipefail -c
L9 ?= l9

# Both layers are required. A missing generated adapter or repository-native
# extension is a broken repository boundary, not a silent degrade.
include Repo.mk
include Repo.local.mk

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
# Standard repository bindings - implemented by generated Repo.mk
# ---------------------------------------------------------------------------
setup: repo-setup ## Install/bootstrap repository dependencies
validate: repo-validate ## Run the repository canonical validation
check: repo-check ## Run repository static/quality checks
test: repo-test ## Run repository tests
clean: repo-clean ## Remove repository-local disposable outputs
doctor: repo-doctor ## Verify the local execution toolchain

# ---------------------------------------------------------------------------
# Cross-repository governance - this bootstrap owns no implementation here
# ---------------------------------------------------------------------------
wiring-check: ## Verify Governance wiring for this repository
	@$(L9) wiring-check

start: ## Run the governed session-start pipeline
	@$(L9) start

workspace-clean: ## Run governed workspace reconciliation
	@$(L9) workspace-clean

pr: ## Gate and publish through Cursor-Governance
	@$(L9) pr
