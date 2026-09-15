#!/usr/bin/env bash
# Validate how a protected test-environment promotion was started.
#
# Promotions must originate from the matching manual job in a protected
# default-branch request pipeline. The PR build itself is untrusted and never
# receives the operator's CI_JOB_TOKEN.
set -euo pipefail

: "${CI_JOB_TOKEN:?CI_JOB_TOKEN is required}"
: "${CI_API_V4_URL:?CI_API_V4_URL is required}"
: "${CI_PROJECT_ID:?CI_PROJECT_ID is required}"
: "${NVCM_MIRROR_API_TOKEN:?NVCM_MIRROR_API_TOKEN (read_api) is required}"
: "${NVCM_PROMOTE_PR:?NVCM_PROMOTE_PR is required}"
: "${NVCM_PROMOTE_PR_SHA:?NVCM_PROMOTE_PR_SHA is required}"
: "${NVCM_PROMOTE_BUILD_PIPELINE_ID:?NVCM_PROMOTE_BUILD_PIPELINE_ID is required}"
: "${NVCM_PROMOTE_ENV:?NVCM_PROMOTE_ENV is required}"

fail() {
    echo "ERROR: invalid test-environment promotion request: $*" >&2
    exit 1
}

[[ "$CI_API_V4_URL" == https://* ]] \
    || fail "CI_API_V4_URL must use HTTPS"

api="${CI_API_V4_URL}/projects/${CI_PROJECT_ID}"

api_get() {
    curl -fsS --max-time 30 -H "PRIVATE-TOKEN: ${NVCM_MIRROR_API_TOKEN}" "$@"
}

# /job is authenticated by this job's short-lived token, so its pipeline
# metadata cannot be redirected by overriding a predefined CI variable.
current_job_json="$(curl -fsS --max-time 30 -H "JOB-TOKEN: ${CI_JOB_TOKEN}" "${CI_API_V4_URL}/job")"
current_ref="$(printf '%s' "$current_job_json" | jq -r '.pipeline.ref // .ref // empty')"
current_pipeline_id="$(printf '%s' "$current_job_json" | jq -r '.pipeline.id // empty')"
current_user_id="$(printf '%s' "$current_job_json" | jq -r '.user.id // empty')"
[[ "$current_pipeline_id" =~ ^[0-9]+$ ]] || fail "current child pipeline id is missing"

# Some GitLab versions omit source from GET /job. Resolve it from the current
# pipeline id reported by that authenticated endpoint instead.
current_pipeline_json="$(api_get "${api}/pipelines/${current_pipeline_id}")"
request_source="$(printf '%s' "$current_pipeline_json" | jq -r '.source // empty')"
pipeline_ref="$(printf '%s' "$current_pipeline_json" | jq -r '.ref // empty')"
[[ "$pipeline_ref" == "$current_ref" ]] \
    || fail "current job and pipeline refs do not match"

project_json="$(api_get "$api")"
default_branch="$(printf '%s' "$project_json" | jq -r '.default_branch // empty')"
[[ -n "$default_branch" ]] || fail "GitLab did not report a default branch"
[[ "$current_ref" == "$default_branch" ]] \
    || fail "protected promotion runs on '${current_ref:-unknown}', expected '${default_branch}'"

[[ "$request_source" == "parent_pipeline" ]] \
    || fail "pipeline source '${request_source:-unknown}' is not a PR promotion child pipeline"

for key in NVCM_PROMOTE_SOURCE_PIPELINE_ID NVCM_PROMOTE_SOURCE_REF NVCM_PROMOTE_SOURCE_SHA; do
    [[ -n "${!key:-}" ]] || fail "${key} is required for a button-triggered promotion"
done
[[ "$NVCM_PROMOTE_SOURCE_PIPELINE_ID" =~ ^[0-9]+$ ]] \
    || fail "source pipeline id is not numeric"
[[ "$NVCM_PROMOTE_PR" =~ ^[0-9]+$ ]] || fail "PR number is not numeric"
[[ "$NVCM_PROMOTE_BUILD_PIPELINE_ID" =~ ^[0-9]+$ ]] \
    || fail "build pipeline id is not numeric"
[[ "$NVCM_PROMOTE_PR_SHA" =~ ^[0-9a-f]{40}$ ]] \
    || fail "PR SHA is not a full lowercase Git SHA"
[[ "$NVCM_PROMOTE_SOURCE_SHA" =~ ^[0-9a-f]{40}$ ]] \
    || fail "source SHA is not a full lowercase Git SHA"

case "$NVCM_PROMOTE_ENV" in
    test|test01) ;;
    *) fail "unsupported target environment '${NVCM_PROMOTE_ENV}'" ;;
esac
[[ "$NVCM_PROMOTE_SOURCE_REF" == "$default_branch" ]] \
    || fail "source request ref '${NVCM_PROMOTE_SOURCE_REF}' is not the default branch"

source_pipeline_json="$(api_get "${api}/pipelines/${NVCM_PROMOTE_SOURCE_PIPELINE_ID}")"
source_pipeline_ref="$(printf '%s' "$source_pipeline_json" | jq -r '.ref // empty')"
source_pipeline_sha="$(printf '%s' "$source_pipeline_json" | jq -r '.sha // empty')"
source_pipeline_status="$(printf '%s' "$source_pipeline_json" | jq -r '.status // empty')"
source_pipeline_source="$(printf '%s' "$source_pipeline_json" | jq -r '.source // empty')"

[[ "$source_pipeline_ref" == "$NVCM_PROMOTE_SOURCE_REF" ]] \
    || fail "source pipeline ref is '${source_pipeline_ref}', expected '${NVCM_PROMOTE_SOURCE_REF}'"
[[ "$source_pipeline_sha" == "$NVCM_PROMOTE_SOURCE_SHA" ]] \
    || fail "source pipeline SHA does not match the button request"
case "$source_pipeline_status" in
    running|success) ;;
    *) fail "source pipeline status is '${source_pipeline_status}'" ;;
esac
[[ "$source_pipeline_source" == "trigger" ]] \
    || fail "source pipeline was not created by the protected push webhook"

source_jobs_json="$(api_get "${api}/pipelines/${NVCM_PROMOTE_SOURCE_PIPELINE_ID}/jobs?per_page=100")"
validation_job_id="$(printf '%s' "$source_jobs_json" \
    | jq -r '[.[] | select(.name == "test-promote-request-validate")] | sort_by(.id) | last | .id // empty')"
validation_job_status="$(printf '%s' "$source_jobs_json" \
    | jq -r '[.[] | select(.name == "test-promote-request-validate")] | sort_by(.id) | last | .status // empty')"
[[ "$validation_job_id" =~ ^[0-9]+$ ]] || fail "source request validation job is missing"
[[ "$validation_job_status" == "success" ]] \
    || fail "source request validation job is '${validation_job_status:-missing}', not successful"

# The trusted webhook validator records the verified PR/build identity in a
# file artifact. Read that file rather than pipeline variables, whose values
# can have been supplied by the trigger caller at the highest precedence.
artifact_body="$(mktemp)"
trap 'rm -f "$artifact_body"' EXIT
artifact_result="$(curl -sS --max-time 30 --proto '=https' \
    -o "$artifact_body" -w '%{http_code}\n%{redirect_url}' \
    -H "JOB-TOKEN: ${CI_JOB_TOKEN}" \
    "${api}/jobs/${validation_job_id}/artifacts/promote-request.env")"
artifact_http="${artifact_result%%$'\n'*}"
artifact_redirect="${artifact_result#*$'\n'}"
case "$artifact_http" in
    200)
        verified_request="$(<"$artifact_body")"
        ;;
    301|302|303|307|308)
        [[ "$artifact_redirect" == https://* ]] \
            || fail "artifact redirect must use HTTPS"
        # Do not send the GitLab job token to object storage or a CDN. The
        # redirect URL is already authorized by GitLab and is fetched alone.
        verified_request="$(curl -fsSL --max-time 30 --max-redirs 3 \
            --proto '=https' --proto-redir '=https' "$artifact_redirect")"
        ;;
    *)
        fail "could not download verified request artifact (HTTP ${artifact_http:-000})"
        ;;
esac
rm -f "$artifact_body"
trap - EXIT
verified_value() {
    local key="$1"
    printf '%s\n' "$verified_request" | grep -m1 "^${key}=" | cut -d= -f2-
}
[[ "$(verified_value VERIFIED_PROMOTE_PR)" == "$NVCM_PROMOTE_PR" ]] \
    || fail "verified request PR does not match PR #${NVCM_PROMOTE_PR}"
[[ "$(verified_value VERIFIED_PROMOTE_PR_SHA)" == "$NVCM_PROMOTE_PR_SHA" ]] \
    || fail "verified request SHA does not match PR SHA"
[[ "$(verified_value VERIFIED_PROMOTE_BUILD_PIPELINE_ID)" == "$NVCM_PROMOTE_BUILD_PIPELINE_ID" ]] \
    || fail "verified request build pipeline does not match the promotion"

source_bridges_json="$(api_get "${api}/pipelines/${NVCM_PROMOTE_SOURCE_PIPELINE_ID}/bridges?per_page=100")"
# The exact trusted-main bridge name binds the target environment, and its
# downstream id proves that this bridge created the current child pipeline.
# GitLab applies the protected-environment ACL to the literal environment on
# that bridge before an operator can play it.
source_job_json="$(printf '%s' "$source_bridges_json" \
    | jq -c --arg name "promote-to-${NVCM_PROMOTE_ENV}" --arg child_id "$current_pipeline_id" \
        '[.[] | select(.name == $name and (.downstream_pipeline.id | tostring) == $child_id)] | sort_by(.id) | last // empty')"
[[ -n "$source_job_json" && "$source_job_json" != "null" ]] \
    || fail "source trigger job for child pipeline ${current_pipeline_id} is missing"
source_job_name="$(printf '%s' "$source_job_json" | jq -r '.name // empty')"
source_job_pipeline_id="$(printf '%s' "$source_job_json" | jq -r '.pipeline.id // empty')"
source_job_ref="$(printf '%s' "$source_job_json" | jq -r '.pipeline.ref // empty')"
source_job_sha="$(printf '%s' "$source_job_json" | jq -r '.pipeline.sha // .commit.id // empty')"
source_job_status="$(printf '%s' "$source_job_json" | jq -r '.status // empty')"
source_job_user_id="$(printf '%s' "$source_job_json" | jq -r '.user.id // empty')"

[[ "$source_job_name" == "promote-to-${NVCM_PROMOTE_ENV}" ]] \
    || fail "source job is '${source_job_name}', expected 'promote-to-${NVCM_PROMOTE_ENV}'"
[[ "$source_job_pipeline_id" == "$NVCM_PROMOTE_SOURCE_PIPELINE_ID" ]] \
    || fail "source job does not belong to source pipeline ${NVCM_PROMOTE_SOURCE_PIPELINE_ID}"
[[ "$source_job_ref" == "$NVCM_PROMOTE_SOURCE_REF" ]] \
    || fail "source job ref does not match '${NVCM_PROMOTE_SOURCE_REF}'"
[[ "$source_job_sha" == "$NVCM_PROMOTE_SOURCE_SHA" ]] \
    || fail "source job SHA does not match the button request"
case "$source_job_status" in
    pending|running|success) ;;
    *) fail "source job status is '${source_job_status}'" ;;
esac
[[ -n "$source_job_user_id" && -n "$current_user_id" ]] \
    || fail "GitLab did not report complete job user provenance"
[[ "$current_user_id" == "$source_job_user_id" ]] \
    || fail "protected pipeline user does not match the person who pressed the button"

echo "Promotion button authorized: ${source_job_name} from protected ${source_pipeline_ref}@${source_pipeline_sha}."
