# Security Policy
## Workflow trust model
Phase 1 workflows:
- use read-only repository permissions;
- execute the immutable event revision;
- provision only the explicitly permitted SDK commit;
- reject branches, tags, short hashes, arbitrary repositories, and arbitrary
  installation commands;
- do not publish, mutate, comment, upload, deploy, or request identity tokens.
## Reporting
Report security issues privately to the repository maintainers. Do not include
active credentials, tokens, or private repository content in public issues.
