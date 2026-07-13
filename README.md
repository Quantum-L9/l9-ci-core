# l9-ci-core

Reusable GitHub Actions workflows for L9 repositories.

Primary workflow:

```yaml
jobs:
  pr_pipeline:
    uses: Quantum-L9/l9-ci-core/.github/workflows/pr-pipeline.yml@v1
    with:
      python-version: "3.12"
      source-dir: "."
      test-dir: "tests/"
```

The workflow installs the SDK as a runtime CLI through the `l9-ci-install-command` input. The default now installs the SDK from the SDK repository pinned to commit `d32e84b` via `git+https` (`python -m pip install "l9-ci @ git+https://github.com/Quantum-L9/l9-ci-sdk.git@d32e84b7c00fc88b85f2639471dd64126251e09e"`). Callers can override this input to pin a different ref or install source. Private-repo callers set an `SDK_TOKEN` secret granting read access to `Quantum-L9/l9-ci-sdk`; public callers need no token. Once the SDK is published to an index, the default will switch back to `pip install l9-ci`. See [docs/SDK_INSTALL.md](docs/SDK_INSTALL.md) for details.

## Claude Code skills

Reusable agent skills live under [`.claude/skills/`](.claude/skills):

- **[`l9-pr-remediation`](.claude/skills/l9-pr-remediation)** — closed-loop PR remediation and review resolution: ingest CI failures + review-bot/human comments, validate every suggestion (reject false positives), batch fixes into one commit per cycle, verify all gates locally before push, reply to and resolve every thread, then loop until CI is green. Ships a canonical run-report schema and validators; its exemplary-tier evidence and the run-report validator are exercised by `tests/skills/`.
- **[`l9-skill-compiler`](.claude/skills/l9-skill-compiler)** — compiles prompts, SOPs, and playbooks into standalone zero-stub skill packs with a compressed intelligence layer.
