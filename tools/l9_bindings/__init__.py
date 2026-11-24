"""Consumer convergence: one declared edge per dependency, one engine for all.

``.l9/bindings.yaml`` in a consumer declares what it pins, where the pin is
written, and what must happen before the pin may move. This package reads that
declaration, resolves the producer's channel head, and either reports drift
(``plan``) or opens the pull request the drift requires (``converge``).
"""
