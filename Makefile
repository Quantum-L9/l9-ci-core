# GENERATED - L9 repository Make facade.
#
# This Core-owned template defines the stable repository-execution ABI. It owns
# no repository implementation: each consumer's Repo.mk supplies the repo-*
# leaves below. Regenerate with the pinned Core tooling: l9-repo reconcile.
#
# Do not place product logic, publication logic, or CI orchestration here.
.DEFAULT_GOAL := help
SHELL := /usr/bin/env bash
.SHELLFLAGS := -eu -o pipefail -c
L9 ?= l9

# This is deliberately mandatory. A consumer without Repo.mk has no repository
# execution implementation and must fail rather than silently passing CI.
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

# Repository-execution ABI. Repo.mk is the single implementation authority.
setup: repo-setup ## Install/bootstrap repository dependencies
validate: repo-validate ## Run repository validation
check: repo-check ## Run repository static/quality checks
test: repo-test ## Run repository tests
clean: repo-clean ## Remove repository-local disposable outputs
doctor: repo-doctor ## Verify repository-local execution tooling

# Organization governance remains outside the portable execution ABI.
wiring-check: ## Verify Governance wiring for this repository
	@$(L9) wiring-check

start: ## Run the governed session-start pipeline
	@$(L9) start

workspace-clean: ## Run governed workspace reconciliation
	@$(L9) workspace-clean

pr: ## Gate and publish through Cursor-Governance
	@$(L9) pr
