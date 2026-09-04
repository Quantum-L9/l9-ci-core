# Repository-specific make targets only. Common targets are owned by l9-repo.

.PHONY: attest-control-plane check-release-writers

# Read-only attestation of the live GitHub control plane against
# .l9/release-plane.yaml. Needs a credential in L9_CONTROL_PLANE_TOKEN,
# GH_TOKEN, or GITHUB_TOKEN; exits non-zero unless every check is PASS.
attest-control-plane:
	@$(PYTHON) tools/verify_control_plane.py

# Namespace-aware release-writer uniqueness: exactly one authorized writer for
# exact vX.Y.Z releases and one for the transitional v2 installer tag.
check-release-writers:
	@$(PYTHON) tools/check_release_writers.py
