# l9-ci-core repository-owned Make implementation.
#
# REPOSITORY-OWNED: hand-maintained, never generated, never rewritten by the
# repository-execution reconcile. The root Makefile is the generated make-v1
# facade; it routes setup / validate / check / test to the repo-* leaves below.
#
# Only the four repo-* leaves are the portable organization ABI. Everything
# after them is Core-local convenience. This file implements no organization
# governance and no publication: those belong to Cursor-Governance and are
# neither implemented nor proxied here.
PYTHON ?= python3
L9_REPO := $(PYTHON) -m tools.l9_repo --workspace "$(CURDIR)"

.PHONY: \
	repo-setup \
	repo-validate \
	repo-check \
	repo-test \
	lint \
	doctor \
	clean \
	status \
	change-policy \
	agent-check \
	core-validate \
	reconcile \
	attest-control-plane \
	check-release-writers

# ---------------------------------------------------------------------------
# Portable make-v1 ABI: Core self-hosts through these four leaves.
# ---------------------------------------------------------------------------
repo-setup: ## Install Core's pinned gate toolchain
	@$(PYTHON) -m pip install -r requirements-ci.txt
	@$(PYTHON) -m pip install -r requirements-repo-runtime.txt

repo-validate: ## Contract, facade, Repo.mk ABI, manifest, authority wiring, workflow integrity
	@$(L9_REPO) validate
	@$(L9_REPO) core-validate
	@$(PYTHON) tools/check_workflow_integrity.py

# Resolve ruff and mypy through the interpreter, never PATH, and assert the
# resolved versions against toolchain-lock.json before either runs.
repo-check: ## Toolchain conformance, ruff check, ruff format --check, mypy
	@$(PYTHON) tools/check_toolchain_versions.py
	@$(PYTHON) -m ruff check .
	@$(PYTHON) -m ruff format --check .
	@$(PYTHON) -m mypy

repo-test: ## Run the complete unittest suite
	@$(PYTHON) -m unittest discover --start-directory tests --pattern 'test_*.py' --verbose

# ---------------------------------------------------------------------------
# Core-local operations. Not part of the portable ABI.
# ---------------------------------------------------------------------------
lint: repo-check ## Alias of the static-check leaf

doctor: ## Verify local repository-execution tooling
	@$(L9_REPO) doctor

clean: ## Remove configured disposable outputs
	@$(L9_REPO) clean

status: ## Report local repository state
	@$(L9_REPO) status

change-policy: ## Show Core change-policy obligations
	@$(L9_REPO) change-policy

agent-check: ## Run Core evidence-bearing completion checks
	@$(L9_REPO) agent-check

core-validate: ## Core-local structural validation only
	@$(L9_REPO) core-validate

reconcile: ## Regenerate the root Makefile from the released make-v1 facade
	@$(L9_REPO) reconcile

attest-control-plane: ## Attest the live GitHub control plane (read-only)
	@$(PYTHON) tools/verify_control_plane.py

check-release-writers: ## Validate release-writer namespace uniqueness
	@$(PYTHON) tools/check_release_writers.py
