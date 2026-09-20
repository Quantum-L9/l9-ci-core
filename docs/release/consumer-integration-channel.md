# Optional Cognitive Runtime Integration Channel

`refs/tags/v2` is a **mutable compatibility tag**, not an immutable Core
release and never the organization CI runtime. The organization-required
workflow remains `Quantum-L9/l9-ci-core/.github/workflows/org-ci.yml` selected
from Core `main` by the GitHub organization ruleset. An exact Core release
remains an immutable `vMAJOR.MINOR.PATCH` audit anchor.

The tag has one narrowly bounded integration use in addition to
`install-consumer-ci@v2`: the `Quantum-L9/l9-cognitive-runtime`
`.github/workflows/release-staging.yml` workflow may reference
`analyze-semgrep.yml` and `container-release` at `@v2`. No other repository,
workflow, or Core surface is authorized by this channel.

## Required sequence

The Core change must first pass the full repository gate and independent
`@Quantum-L9/platform` review. After the change is merged to Core `main`, the
sole tag writer, `tools/publish_consumer_ci_tag.sh`, may move `v2` to the
reviewed commit and record the previous SHA. The Cognitive Runtime reference
change then requires its own review and validation. A tag move never makes a
consumer change mergeable by itself.

Every Cognitive Runtime release still records a full source revision in the
deployment pack and releases only an immutable container-image digest. The
mutable integration reference is therefore not release evidence.

## Prohibitions

The `v2` channel is never an organization required workflow, never an exact
Core release alias, never a permission to select arbitrary Core revisions, and
never a substitute for a reviewed Core or Cognitive Runtime change. Do not use
`@main` in a reusable workflow reference.
