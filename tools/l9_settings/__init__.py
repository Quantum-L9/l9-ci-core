"""GitHub settings as code: plan and apply engine.

The declaration is owned by Quantum-L9/.github (``settings/``: ``org.yml``,
``repos/<repository>.yml`` and ``settings.schema.json``). This engine only
reads it, reads live state through the GitHub REST API, prints the plan, and
writes only on ``apply``. It is scheduled by Quantum-L9/l9-repo-template
(``settings-apply.yml``) with the existing repo-birth App, through Core's
reusable ``settings-runner.yml``.
"""
