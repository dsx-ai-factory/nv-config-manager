#!/usr/bin/env bash
# Stage 1 of the test-env promote flow: obtain a successful secret-free build
# of the vetted copy-pr-bot snapshot (pull-request/<n>) on the mirror.
#
# Resolves the snapshot's HEAD SHA, confirms the PR is still open upstream and
# warns if the snapshot lags the PR's live HEAD, then verifies and consumes the
# identified automatic build pipeline for that exact snapshot. It runs on
# an UNPROTECTED ref, so it executes the untrusted PR code without any protected
# variables. Provenance is asserted from GitLab's own pipeline metadata.
#
# Inputs (pipeline variables): NVCM_PROMOTE_PR, NVCM_PROMOTE_PR_SHA,
#                             NVCM_PROMOTE_BUILD_PIPELINE_ID, NVCM_PROMOTE_ENV
#   Optional: NVCM_PROMOTE_REQUIRE_PR_HEAD (default false),
#             NVCM_PROMOTE_ALLOW_UNVERIFIED_PR_STATE (default false)
# Requires: NVCM_MIRROR_API_TOKEN (read_api; polling/job-listing endpoints are
#           not in the CI_JOB_TOKEN allowlist), CI_JOB_TOKEN (repo clone)
# Output:   promote.env (dotenv + file artifact) - PR_NUM, PR_REF, PR_SHA,
#           PR_SHORT_SHA,
#           PROMOTE_VERSION, BUILD_PIPELINE_ID, BUILD_JOB_ID_<IMAGE> x9,
#           CHART_BUILD_JOB_ID
set -euo pipefail

: "${NVCM_PROMOTE_PR:?Set NVCM_PROMOTE_PR to the GitHub PR number}"
: "${NVCM_PROMOTE_PR_SHA:?NVCM_PROMOTE_PR_SHA is required}"
: "${NVCM_PROMOTE_BUILD_PIPELINE_ID:?NVCM_PROMOTE_BUILD_PIPELINE_ID is required}"
: "${NVCM_PROMOTE_ENV:?Set NVCM_PROMOTE_ENV to the target environment}"
: "${NVCM_MIRROR_API_TOKEN:?NVCM_MIRROR_API_TOKEN (read_api) is required}"

if ! printf '%s' "$NVCM_PROMOTE_PR" | grep -Eq '^[0-9]+$'; then
    echo "ERROR: NVCM_PROMOTE_PR must be a PR number, got '${NVCM_PROMOTE_PR}'" >&2
    exit 1
fi

github_repo="${NVCM_UPSTREAM_GITHUB_REPO:-dsx-ai-factory/nv-config-manager}"
api="${CI_API_V4_URL}/projects/${CI_PROJECT_ID}"

# Promotions consume only the automatic copy-pr-bot mirror-sync build. Manual
# and API-triggered PR pipelines can still be useful for diagnostics.
allowed_build_sources='["push"]'

PR_NUM="$NVCM_PROMOTE_PR"
PR_REF="pull-request/${PR_NUM}"

# The nine pr-build-image matrix entries (target|image), matching pr-build.yml.
matrix_entries="nv-config-manager|nv-config-manager
kea|nv-config-manager-kea
kea-admin|nv-config-manager-kea-admin
ui|nv-config-manager-ui
nb|nv-config-manager-nautobot
nats-ready|nv-config-manager-nats-ready
temporal-server|nv-config-manager-temporal
temporal-bootstrap|nv-config-manager-temporal-bootstrap
temporal-ui|nv-config-manager-temporal-ui"

api_get() {
    curl -fsS --max-time 30 -H "PRIVATE-TOKEN: ${NVCM_MIRROR_API_TOKEN}" "$@"
    return $?
}

# -----------------------------------------------------------------------------
# Resolve the current HEAD of the VETTED copy-pr-bot snapshot on the mirror
# (pull-request/<n>). This is intentionally the vetted snapshot, not the PR's
# live HEAD - the GitHub PR-HEAD comparison below surfaces any divergence.
# -----------------------------------------------------------------------------
PR_SHA="$(git ls-remote "$CI_REPOSITORY_URL" "refs/heads/${PR_REF}" | cut -f1)"
if [[ -z "$PR_SHA" ]]; then
    echo "ERROR: ${PR_REF} does not exist on the mirror." >&2
    echo "Either the PR is closed/merged, copy-pr-bot has not vetted it, or"
    echo "pull-mirroring has not replicated the branch yet."
    exit 1
fi
if [[ "$PR_SHA" != "$NVCM_PROMOTE_PR_SHA" ]]; then
    echo "ERROR: ${PR_REF} moved after its promotion request was created." >&2
    echo "  request SHA: ${NVCM_PROMOTE_PR_SHA}" >&2
    echo "  current SHA: ${PR_SHA}" >&2
    echo "Use the promotion buttons from the latest PR pipeline." >&2
    exit 1
fi
PR_SHORT_SHA="$(printf '%s' "$PR_SHA" | cut -c1-8)"
PROMOTE_VERSION="0.0.0-pr${PR_NUM}.${PR_SHORT_SHA}"
echo "PR #${PR_NUM}: ${PR_REF} @ ${PR_SHA}"
echo "Promote version: ${PROMOTE_VERSION}"

# Interactive-path upstream checks against GitHub's PR API. This API is
# authoritative for PR *state* and the PR's *true* HEAD - unlike the copy-pr-bot
# pull-request/<n> branch (PR_SHA above), which is a VETTED SNAPSHOT that may
# lag the PR (untrusted authors only re-copy on /ok to test).
pr_json="$(curl -fsS --max-time 10 "https://api.github.com/repos/${github_repo}/pulls/${PR_NUM}" || true)"
if [[ -n "$pr_json" ]]; then
    pr_state="$(printf '%s' "$pr_json" | jq -r '.state // empty')"
    if [[ "$pr_state" != "open" ]]; then
        echo "ERROR: upstream PR #${PR_NUM} is '${pr_state:-unknown}', not open. Refusing to promote." >&2
        exit 1
    fi
    # Surface (do NOT silently deploy) a vetted snapshot older than PR HEAD. We
    # deliberately promote the vetted snapshot, not the live PR HEAD - newer
    # commits are unvetted and must not run - but the operator must know when
    # what deploys is not their latest push.
    pr_head_sha="$(printf '%s' "$pr_json" | jq -r '.head.sha // empty')"
    if [[ -n "$pr_head_sha" && "$pr_head_sha" != "$PR_SHA" ]]; then
        echo "WARN: PR #${PR_NUM} HEAD is ${pr_head_sha}, but the vetted copy-pr-bot"
        echo "      snapshot (pull-request/${PR_NUM}) is ${PR_SHA}. Promoting the VETTED"
        echo "      snapshot; to deploy newer commits, have them re-vetted (/ok to test)."
        if [[ "${NVCM_PROMOTE_REQUIRE_PR_HEAD:-false}" = "true" ]]; then
            echo "ERROR: NVCM_PROMOTE_REQUIRE_PR_HEAD=true and the snapshot lags PR HEAD." >&2
            exit 1
        fi
    fi
elif [[ "${NVCM_PROMOTE_ALLOW_UNVERIFIED_PR_STATE:-false}" = "true" ]]; then
    echo "WARN: could not query GitHub PR state; NVCM_PROMOTE_ALLOW_UNVERIFIED_PR_STATE=true set, continuing."
else
    # Fail closed: don't promote a possibly-closed PR just because GitHub was
    # unreachable. Operator can explicitly override for a GitHub outage.
    echo "ERROR: could not confirm PR #${PR_NUM} is open (GitHub unreachable or rate-limited)." >&2
    echo "Re-run when GitHub is reachable, or set NVCM_PROMOTE_ALLOW_UNVERIFIED_PR_STATE=true to override."
    exit 1
fi
bash "$(dirname "$0")/pr_ref_guard.sh" "$PR_REF" "$PR_SHA"

BUILD_PIPELINE_ID="$NVCM_PROMOTE_BUILD_PIPELINE_ID"
[[ "$BUILD_PIPELINE_ID" =~ ^[0-9]+$ ]] || {
    echo "ERROR: verified source build pipeline id is missing or invalid" >&2
    exit 1
}
echo "Using source PR pipeline ${BUILD_PIPELINE_ID}; will verify its provenance and artifacts."

# -----------------------------------------------------------------------------
# Trusted provenance gate. GitLab records the exact commit and ref a pipeline
# ran on; assert the build pipeline ran on the PR HEAD we resolved and guarded.
# This is the ONLY provenance check that matters: it uses GitLab metadata, not
# any file the untrusted build produced (which the PR author fully controls).
# The automatic request pipeline resolves the build id without occupying a
# runner while the build runs. If the button is pressed early, wait here in the
# user-requested promotion and fail immediately if the vetted branch moves.
# -----------------------------------------------------------------------------
poll_interval=15
build_timeout=7200
build_deadline=$((SECONDS + build_timeout))
while :; do
    build_pipeline_json="$(api_get "${api}/pipelines/${BUILD_PIPELINE_ID}")"
    build_sha="$(printf '%s' "$build_pipeline_json" | jq -r '.sha')"
    build_ref="$(printf '%s' "$build_pipeline_json" | jq -r '.ref')"
    build_status="$(printf '%s' "$build_pipeline_json" | jq -r '.status')"
    build_source="$(printf '%s' "$build_pipeline_json" | jq -r '.source')"
    if [[ "$build_sha" != "$PR_SHA" || "$build_ref" != "$PR_REF" ]]; then
        echo "ERROR: build pipeline ${BUILD_PIPELINE_ID} provenance mismatch." >&2
        echo "  got ref=${build_ref} sha=${build_sha} status=${build_status}"
        echo "  expected ref=${PR_REF} sha=${PR_SHA}"
        exit 1
    fi
    if ! printf '%s' "$allowed_build_sources" \
        | jq -e --arg s "$build_source" 'index($s) != null' >/dev/null; then
        echo "ERROR: build pipeline ${BUILD_PIPELINE_ID} has disallowed source '${build_source}'." >&2
        echo "Expected a vetted PR build pipeline ($(printf '%s' "$allowed_build_sources" | jq -r 'join(", ")'))."
        exit 1
    fi

    current_pr_sha="$(git ls-remote "$CI_REPOSITORY_URL" "refs/heads/${PR_REF}" | cut -f1)"
    if [[ "$current_pr_sha" != "$PR_SHA" ]]; then
        echo "ERROR: ${PR_REF} moved while build pipeline ${BUILD_PIPELINE_ID} was running." >&2
        echo "  request SHA: ${PR_SHA}" >&2
        echo "  current SHA: ${current_pr_sha:-missing}" >&2
        echo "Use the promotion buttons from the latest request pipeline." >&2
        exit 1
    fi

    case "$build_status" in
        success) break ;;
        created|waiting_for_resource|preparing|pending|running)
            (( SECONDS < build_deadline )) || {
                echo "ERROR: build pipeline ${BUILD_PIPELINE_ID} timed out after ${build_timeout}s." >&2
                exit 1
            }
            echo "Build pipeline ${BUILD_PIPELINE_ID} is ${build_status}; waiting..."
            sleep "$poll_interval"
            ;;
        *)
            echo "ERROR: build pipeline ${BUILD_PIPELINE_ID} ended with status '${build_status}'." >&2
            exit 1
            ;;
    esac
done
echo "Provenance OK: ${build_source} pipeline ${BUILD_PIPELINE_ID} ran on ${PR_REF}@${PR_SHA}"

# -----------------------------------------------------------------------------
# Collect the nine pr-build-image job ids and confirm artifacts are usable.
# -----------------------------------------------------------------------------
jobs_json="$(api_get "${api}/pipelines/${BUILD_PIPELINE_ID}/jobs?per_page=100")"

{
    echo "PR_NUM=${PR_NUM}"
    echo "PR_REF=${PR_REF}"
    echo "PR_SHA=${PR_SHA}"
    echo "PR_SHORT_SHA=${PR_SHORT_SHA}"
    echo "PROMOTE_VERSION=${PROMOTE_VERSION}"
    echo "BUILD_PIPELINE_ID=${BUILD_PIPELINE_ID}"
} > promote.env

now_epoch="$(date -u +%s)"
while IFS='|' read -r target image; do
    job_name="pr-build-image: [${target}, ${image}]"
    job_json="$(printf '%s' "$jobs_json" | jq -c --arg name "$job_name" '[.[] | select(.name == $name and .status == "success")] | sort_by(.id) | last // empty')"
    if [[ -z "$job_json" || "$job_json" = "null" ]]; then
        echo "ERROR: no successful '${job_name}' job in pipeline ${BUILD_PIPELINE_ID}." >&2
        exit 1
    fi
    job_id="$(printf '%s' "$job_json" | jq -r '.id')"
    expires_at="$(printf '%s' "$job_json" | jq -r '.artifacts_expire_at // empty')"
    if [[ -n "$expires_at" ]]; then
        expire_epoch="$(date -u -d "$expires_at" +%s 2>/dev/null || date -u -D "%Y-%m-%dT%H:%M:%S" -d "${expires_at%%.*}" +%s 2>/dev/null || echo 0)"
        if [[ "$expire_epoch" != "0" && "$expire_epoch" -le "$now_epoch" ]]; then
            echo "ERROR: artifacts of job ${job_id} (${job_name}) have expired." >&2
            echo "Re-run the pull-request pipeline to create fresh artifacts."
            exit 1
        fi
    fi
    key="$(printf '%s' "$image" | tr 'a-z-' 'A-Z_')"
    echo "BUILD_JOB_ID_${key}=${job_id}" >> promote.env
    echo "  ${job_name} -> job ${job_id}"
done <<EOF
${matrix_entries}
EOF

# The chart is packaged (secret-free) alongside the images; capture its job id
# so the promote pipeline can download the .tgz and publish it without ever
# rebuilding from PR source.
chart_job_id="$(printf '%s' "$jobs_json" | jq -r '[.[] | select(.name == "pr-build-chart" and .status == "success")] | sort_by(.id) | last | .id // empty')"
if [[ -z "$chart_job_id" ]]; then
    echo "ERROR: no successful 'pr-build-chart' job in pipeline ${BUILD_PIPELINE_ID}." >&2
    exit 1
fi
echo "CHART_BUILD_JOB_ID=${chart_job_id}" >> promote.env
echo "  pr-build-chart -> job ${chart_job_id}"

echo "Build stage resolved:"
cat promote.env
