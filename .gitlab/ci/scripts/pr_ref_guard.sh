#!/usr/bin/env bash
# Guard against building or promoting a stale pull-request/<n> SHA.
#
# The mirror replicates copy-pr-bot's pull-request/<n> branches from GitHub, and
# a PR can gain commits (or close) between the moment a pipeline is scheduled and
# the moment its jobs run. Any job acting on a pull-request ref calls this to
# confirm the ref still exists on the mirror and still points at the SHA being
# built/promoted.
#
# Mirror-only by design: no GitHub calls. The authoritative freshness and
# provenance checks live in the protected promote consumer -
# promote_build_pipeline.sh confirms the PR is open and warns on PR-HEAD
# divergence via the GitHub PR API, then gates on GitLab pipeline metadata
# (build sha == PR_SHA). Keeping this guard free of (unauthenticated) GitHub
# calls avoids rate limits in the many-times-per-pipeline build stage.
#
# Usage: pr_ref_guard.sh [ref] [expected-sha]
#   ref          defaults to $CI_COMMIT_REF_NAME
#   expected-sha defaults to $CI_COMMIT_SHA
#
# Uses $CI_REPOSITORY_URL, which embeds CI_JOB_TOKEN - the same token the runner
# already uses to check out the repo; it carries no protected/registry creds.
set -euo pipefail

ref="${1:-${CI_COMMIT_REF_NAME:?ref argument or CI_COMMIT_REF_NAME required}}"
expected="${2:-${CI_COMMIT_SHA:?expected-sha argument or CI_COMMIT_SHA required}}"

echo "Checking that ${ref} still points at ${expected}..."

mirror_head="$(
    timeout 30s git ls-remote "$CI_REPOSITORY_URL" "refs/heads/${ref}" |
        cut -f1
)"
if [[ -z "$mirror_head" ]]; then
    echo "ERROR: ${ref} no longer exists on the mirror (PR closed or merged?)" >&2
    exit 1
fi
if [[ "$mirror_head" != "$expected" ]]; then
    echo "ERROR: stale SHA - mirror ${ref} is at ${mirror_head}, expected ${expected}." >&2
    echo "The PR gained commits since this run was scheduled; re-run against the new HEAD."
    exit 1
fi

echo "OK: ${ref} is current at ${expected}"
