# Governance

NVIDIA Config Manager is governed by NVIDIA project maintainers.

## Maintainer Responsibilities

Maintainers are responsible for:

- Reviewing issues and pull requests.
- Keeping CI, release, security, and documentation workflows healthy.
- Enforcing the [Code of Conduct](CODE_OF_CONDUCT.md).
- Coordinating vulnerability handling through the process in
  [SECURITY.md](SECURITY.md).
- Promoting release tags through the protected release workflow.

## Contribution Model

Contributions are accepted through pull requests. Contributors must follow
[CONTRIBUTING.md](CONTRIBUTING.md), including the Developer Certificate of
Origin sign-off process.

Technical decisions are made through normal issue and pull request discussion.
Maintainers may request design notes for broad behavior changes, compatibility
changes, or changes that affect deployment, security, or public APIs.

## Release Governance

Release tags are created through protected GitHub workflows and are expected to
be reviewed by project maintainers before promotion. Release notes should be
tracked in [CHANGELOG.md](CHANGELOG.md).

## Maintainer Membership

Contributors may nominate themselves or another contributor in a governance issue,
describing sustained contributions, review experience, and the proposed ownership
area. Existing maintainers discuss the nomination; repository administrators make
the final membership decision and apply access changes. Record the outcome and
update [MAINTAINERS.md](MAINTAINERS.md) and [CODEOWNERS](.github/CODEOWNERS) together.

A maintainer may step down by notifying the other maintainers. For inactivity,
maintainers first contact the person privately to agree on a handoff; administrators
may then remove active review routing and access and record emeritus status with
the person's consent. Emeritus status recognizes prior service and grants no access.
Returning maintainers use the nomination process again. Conduct-related removal
follows [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), with confidential details kept private.
