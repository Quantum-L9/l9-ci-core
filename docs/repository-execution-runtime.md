# Repository Execution Contract V2

**Contract ID:** `L9-CORE-REPO-EXEC-V2`
**Status:** Authoritative
**Owner:** `l9-ci-core`

## Purpose and ownership

Repository execution is a **portable consumer ABI**, separate from organization
CI governance and from repository-specific build logic. The contract exists in
the consumer repository as `.l9/repo-workflow.json`, but it is parsed,
validated, compiled, and executed only by the immutable, pinned Core checkout.
A consumer cannot alter Core semantics by vendoring `tools/l9_repo`, a schema,
or a template.

| Concern | Owner |
| --- | --- |
| Organization workflow, CI permissions, enforcement, tool pins, and execution order | Core |
| V2 schema, parser, compiler, reconciler, generated-artifact verifier | Core |
| `.l9/repo-workflow.json` declaration | Consumer |
| Generated root `Makefile` facade | Core-generated artifact in consumer |
| `Repo.mk` and native build/test commands | Consumer |
| Provider analysis, evidence, and technical findings | SDK |
| Publication governance | Cursor-Governance |

The V2 consumer contract is deliberately small:

```json
{
  "schema": "l9.repo-execution/v2",
  "facade": "make-v1",
  "required_phases": ["setup", "validate", "check", "test"]
}
```

Unknown fields fail closed. The contract cannot contain command matrices,
beneficiary metadata, Core revisions, organization policy, GitHub Actions
configuration, publication behavior, or `push`/`pull_request` settings.

## Stable ABI and facade

Core owns the sequence and meaning of these operations:

```text
make setup → make validate → make check → make test
```

The canonical generated `Makefile` routes those four targets to the
consumer-owned leaves `repo-setup`, `repo-validate`, `repo-check`, and
`repo-test` in `Repo.mk`. `Repo.mk` is mandatory and each leaf must exist;
missing leaves are contract failures, not successful no-ops. The generated
facade contains no language-specific commands and no publication commands.

`clean` and `doctor` remain optional developer-facing facade verbs. They are
not part of the authoritative four-phase CI sequence.

## Core-owned tooling

The pinned Core package provides the following operations. The command may be
exposed by an installed `l9-repo` CLI or run from a Core checkout as
`python3 -m tools.l9_repo`.

| Operation | Behavior |
| --- | --- |
| `init` | Creates a V2 declaration, generated facade, and a `Repo.mk` skeleton only when `Repo.mk` is absent. |
| `reconcile` | Validates V2 and deterministically replaces stale generated artifacts. It never overwrites an existing `Repo.mk`. |
| `validate` | Validates the consumer declaration using the embedded Core V2 schema. |
| `verify-generated` | Computes and compares generated artifacts without writing the consumer worktree; drift prints `run l9-repo reconcile`. |
| `migrate-v1` | Converts supported V1 command matrices into a consumer `Repo.mk`, writes the V2 declaration, and removes Core-specific V1 concepts. |

Core’s own `Repo.mk` self-hosts these same ABI leaves. Its Core-only checks,
change policy, evidence configuration, manifest validation, and authority
wiring reside in `.l9/core-repo-policy.json`, which is never interpreted as a
consumer protocol.

## CI runtime and failure semantics

`run-repository-verification` reads the consumer contract from the workspace
with Core code, verifies generated-facade parity, and invokes the four Make
phases in the required order. Repository commands run in the untrusted
consumer checkout with the existing read-only central-CI posture; the action
does not grant secrets, write credentials, or organization authority.

During migration, the action supports a bounded dual-read mode:

| Contract state | Migration mode result | Required mode result |
| --- | --- | --- |
| V2 | Validate, verify facade, execute ABI | Validate, verify facade, execute ABI |
| V1 | Temporary compatibility execution | Temporary compatibility execution until V1 support is retired |
| Absent | `legacy_not_applicable` typed result | `missing_repository_contract` blocking contract failure |

The organization workflow uses migration mode until the fleet gate is
satisfied. The explicit trigger for switching to required mode is: **every
governed repository has a committed, passing V2 contract and migration census
reports zero V1 and zero uncontracted governed repositories**. After that
gate, change the action input to `contract-mode: required`; remove V1 support
only after a subsequent census again proves zero V1 consumers.

Technical command failures and contract/infrastructure failures are emitted as
distinct typed results. CI never silently reconciles a pull request: generated
drift fails and names the remediation command.

## V1 migration instructions

1. Run `l9-repo migrate-v1` from the consumer repository using the approved
   Core runtime.
2. Review the generated or preserved `Repo.mk`; command matrices become
   repository-owned `repo-*` leaves.
3. Confirm `.l9/repo-workflow.json` is exactly the V2 declaration and contains
   no copied V1 authority, policy, publication, or command fields.
4. Run `l9-repo verify-generated`, then `make setup`, `make validate`,
   `make check`, and `make test`.
5. Commit `.l9/repo-workflow.json`, `Makefile`, and `Repo.mk` as appropriate.

Migration never overwrites an existing `Repo.mk`. If a repository already has
implementation leaves, the tool preserves them and converts only the portable
declaration and generated facade.

## Core self-hosting and validation

Core self-hosts through the same V2 declaration and generated facade. Its
implementation commands live in `Repo.mk`; Core-specific policy is extracted
into `.l9/core-repo-policy.json`. Before merging a Core runtime change, run:

```bash
make setup
make validate
make change-policy
make check
python3 -m unittest discover tests
make agent-check
```

Regenerate `MANIFEST.sha256` for all changed tracked files. The manifest check
is never an acceptance bypass; `L9_MANIFEST_CHECK=0` is limited to salvage or
bisect work on a knowingly drifted tree.
