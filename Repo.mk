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
	test-deep \
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

# Explicit PR subset from the frozen census. Not unittest discover. The recipe
# fails if any listed path is missing or if collection is zero. The full suite
# is the Core-local test-deep target.
REPO_TEST_MODULES := \
	tests/actions/test_aggregate_required_instances.py \
	tests/actions/test_run_supply_chain.py \
	tests/actions/test_plan_required_instances.py \
	tests/actions/test_repository_verification_conflict_markers.py \
	tests/governance/test_ci_runtime_invariants.py \
	tests/governance/test_core_toolchain_alignment.py \
	tests/governance/test_dependabot_core_owned_tools.py \
	tests/repo_execution/test_boundary.py \
	tests/repo_execution/test_contract.py \
	tests/repo_execution/test_contract_admission.py \
	tests/repo_execution/test_core_policy.py \
	tests/repo_execution/test_core_runtime.py \
	tests/repo_execution/test_core_self_hosting.py \
	tests/repo_execution/test_facade.py \
	tests/repo_execution/test_inherited_config.py \
	tests/repo_execution/test_legacy_v1.py \
	tests/repo_execution/test_repo_mk.py \
	tests/repo_execution/test_stamp.py \
	tests/settings_engine/test_settings_requirements.py \
	tests/workflows/test_org_ci_security_job.py \
	tests/test_unittest_collectability.py \
	tests/tools/test_advance_core_action_pins.py \
	tests/tools/test_audit_core_toolchain.py \
	tests/tools/test_check_core_pin_ancestry.py \
	tests/tools/test_check_toolchain_versions.py \
	tests/tools/test_check_workflow_integrity.py \
	tests/tools/test_manifest_integrity.py \
	tests/tools/test_nightly_sink.py \
	tests/tools/test_project_required_repositories.py \
	tests/tools/test_tool_ownership.py \
	tests/workflows/test_merge_queue_triggers.py \
	tests/workflows/test_required_check_name.py \
	tests/workflows/test_nightly_apply.py \
	tests/workflows/test_org_ci_toolchain_drift_report.py \
	tests/workflows/test_org_ci_required_instances.py \
	tests/workflows/test_org_ci_scan_tree.py \
	tests/workflows/test_security_consumer_scope.py \
	tests/workflows/test_security_pip_audit_lockset.py \
	tests/workflows/test_blocking_check_classification.py \
	tests/workflows/test_security_scan_outcomes.py

# The interpreter command that runs the subset. self-ci.yml's Repository
# contract job sets it to `python -m coverage run --parallel-mode -m unittest`
# so Core's Sonar analysis gets coverage from this same run (.coveragerc); the
# default runs the suite exactly as before.
REPO_TEST_RUNNER ?= $(PYTHON) -m unittest

repo-test: ## Run the explicit PR unittest subset
	@for module in $(REPO_TEST_MODULES); do \
		test -f "$$module" || { printf 'repo-test: missing %s\n' "$$module" >&2; exit 1; }; \
	done
	@$(PYTHON) -c 'import os, sys, unittest; mods = sys.argv[1:]; names = [m[:-3].replace(os.sep, ".") if m.endswith(".py") else m for m in mods]; count = unittest.defaultTestLoader.loadTestsFromNames(names).countTestCases(); sys.exit(0 if count else "repo-test: collection is zero")' $(REPO_TEST_MODULES)
	@$(REPO_TEST_RUNNER) --verbose $(REPO_TEST_MODULES)

# ---------------------------------------------------------------------------
# Core-local operations. Not part of the portable ABI.
# ---------------------------------------------------------------------------
test-deep: ## Full unittest discover over tests/
	@$(PYTHON) -m unittest discover --start-directory tests --pattern 'test_*.py' --verbose

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
