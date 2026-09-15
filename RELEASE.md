# Release Process

NVIDIA Config Manager is in Developer Preview. Releases are readiness-based,
without a fixed calendar. Maintainers coordinate scope and release notes in
[CHANGELOG.md](CHANGELOG.md) and project issues.

## Branches, Versions, and Authority

Development targets `main`; contributors use topic branches and pull requests.
The protected `pull-request/*` branches are created by copy-pr-bot for approved CI.
Do not use them as development branches.

Platform tags use `X.Y.Z` for stable releases and `X.Y.Z-rc.N` for candidates.
Go bindings additionally use `bindings/go/vX.Y.Z` (including the RC suffix for
candidates). Apply Semantic Versioning: incompatible public API or configuration
changes require a major version; compatible features a minor version; compatible
fixes a patch. Document migration steps for breaking changes before promotion.

Only repository administrators can trigger successful tag creation. Review the
release commit, CI results, migration notes, and changelog before promotion.

## Release Steps

1. Merge reviewed changes and release notes into `main`.
1. Run **Create RC Tag** with `source_ref=main`. Keep
   `require_public_ci_success=true`. The workflow checks main ancestry and creates
   the platform and Go binding tags atomically.
1. Validate the candidate using the project's release qualification process.
1. Run **Promote Release** for the validated candidate. The workflow promotes the
   same commit and publishes a GitHub Release using its changelog section.
1. Verify the source tag, Go module tag, and GitHub release notes before announcing
   availability.

The implementations are [create-rc-tag.yml](.github/workflows/create-rc-tag.yml)
and [promote-release.yml](.github/workflows/promote-release.yml). Create public
tags and release notes through these workflows; local tags are for development only.

## Distribution Channels

| Artifact | Channel and behavior |
| --- | --- |
| Source and release notes | [GitHub Releases](https://github.com/dsx-ai-factory/nv-config-manager/releases); RCs are Git tags |
| Go bindings | Versioned Git module tags; see [bindings/go/README.md](bindings/go/README.md) |
| Container images | No public container registry distribution at this time; build locally from source |
| Helm chart | No public packaged-chart distribution at this time; use the chart source under `deploy/helm/` |
| Air-gap bundles | No public prebuilt bundle distribution at this time; build from source using the documented tooling |
| Python extensions | Git or sibling checkout as listed in [README.md](README.md#separately-installable-components); no public package-index distribution at this time |

Use the [installer guide](installer/README.md) and
[air-gap deployment guide](docs/install/install-airgapped.mdx) for installation.

## Verification and Support

Current tagging workflows create annotated, unsigned Git tags. They do not provide
GPG/SSH tag signatures. Do not describe these releases as cryptographically signed.

[SECURITY.md](SECURITY.md#supported-versions) is the supported-version policy.
Report vulnerabilities through NVIDIA PSIRT. Maintainers must review that table
when promoting a new release and state bug-fix and security backport decisions in
the release notes; do not infer support from the existence of an old tag.
