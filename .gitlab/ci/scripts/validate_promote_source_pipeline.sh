#!/usr/bin/env bash
# Validate a pull-request branch push webhook, resolve its secret-free build,
# and expose the GitLab-verified PR/build identity to trusted manual buttons.
#
# Output: promote-request.env, read as a file by the trusted environment-button
# jobs so trigger variables cannot override the verified PR/build identity.
set -euo pipefail

: "${CI_JOB_TOKEN:?CI_JOB_TOKEN is required}"
: "${CI_API_V4_URL:?CI_API_V4_URL is required}"
: "${CI_PROJECT_ID:?CI_PROJECT_ID is required}"
: "${NVCM_MIRROR_API_TOKEN:?NVCM_MIRROR_API_TOKEN (read_api) is required}"
: "${TRIGGER_PAYLOAD:?TRIGGER_PAYLOAD webhook file is required}"

fail() {
    echo "ERROR: invalid automatic promotion request: $*" >&2
    exit 1
}

[[ "$CI_API_V4_URL" == https://* ]] \
    || fail "CI_API_V4_URL must use HTTPS"

api="${CI_API_V4_URL}/projects/${CI_PROJECT_ID}"
poll_interval=5
create_timeout=120

api_get() {
    curl -fsS --max-time 30 -H "PRIVATE-TOKEN: ${NVCM_MIRROR_API_TOKEN}" "$@"
}

[[ -f "$TRIGGER_PAYLOAD" ]] || fail "TRIGGER_PAYLOAD is not a file"

# /job is authenticated by this job's short-lived token, so its source/ref
# cannot be redirected by overriding predefined CI variables.
current_job_json="$(curl -fsS --max-time 30 -H "JOB-TOKEN: ${CI_JOB_TOKEN}" "${CI_API_V4_URL}/job")"
current_ref="$(printf '%s' "$current_job_json" | jq -r '.pipeline.ref // .ref // empty')"
request_pipeline_id="$(printf '%s' "$current_job_json" | jq -r '.pipeline.id // empty')"
[[ "$request_pipeline_id" =~ ^[0-9]+$ ]] || fail "request pipeline id is missing"

# Some GitLab versions omit source from GET /job. Resolve it from the current
# pipeline id reported by that authenticated endpoint instead.
current_pipeline_json="$(api_get "${api}/pipelines/${request_pipeline_id}")"
request_source="$(printf '%s' "$current_pipeline_json" | jq -r '.source // empty')"
pipeline_ref="$(printf '%s' "$current_pipeline_json" | jq -r '.ref // empty')"
[[ "$pipeline_ref" == "$current_ref" ]] \
    || fail "current job and pipeline refs do not match"

project_json="$(api_get "$api")"
default_branch="$(printf '%s' "$project_json" | jq -r '.default_branch // empty')"
[[ -n "$default_branch" ]] || fail "GitLab did not report a default branch"
[[ "$current_ref" == "$default_branch" ]] \
    || fail "promotion request runs on '${current_ref:-unknown}', expected '${default_branch}'"
[[ "$request_source" == "trigger" ]] \
    || fail "pipeline source '${request_source:-unknown}' is not a push webhook trigger"

payload="$(<"$TRIGGER_PAYLOAD")"
object_kind="$(printf '%s' "$payload" | jq -r '.object_kind // empty')"
payload_project_id="$(printf '%s' "$payload" | jq -r '.project.id // .project_id // empty')"
payload_ref="$(printf '%s' "$payload" | jq -r '.ref // empty')"
payload_sha="$(printf '%s' "$payload" | jq -r '.checkout_sha // empty')"
payload_after="$(printf '%s' "$payload" | jq -r '.after // empty')"
payload_user_id="$(printf '%s' "$payload" | jq -r '.user_id // empty')"

[[ "$object_kind" == "push" ]] || fail "webhook event is not a push"
[[ "$payload_project_id" == "$CI_PROJECT_ID" ]] || fail "webhook project does not match this project"
expected_pr_ref=""
if [[ "$payload_ref" =~ ^refs/heads/pull-request/[0-9]+$ ]]; then
    expected_pr_ref="${payload_ref#refs/heads/}"
elif [[ "$payload_ref" != "$default_branch" ]]; then
    fail "webhook ref '${payload_ref}' is not a PR ref or GitLab-rewritten default branch"
fi
[[ "$payload_sha" =~ ^[0-9a-f]{40}$ ]] || fail "webhook SHA is not a full lowercase Git SHA"
[[ "$payload_after" == "$payload_sha" ]] || fail "webhook after/checkout SHA mismatch"
[[ "$payload_user_id" =~ ^[0-9]+$ ]] || fail "webhook user id is missing or invalid"

create_deadline=$((SECONDS + create_timeout))
build_pipeline_id=""
pr_ref=""
while [[ -z "$build_pipeline_id" ]]; do
    # GitLab 17.4 replaces the webhook ref in TRIGGER_PAYLOAD with the trigger
    # URL's target ref. Recover the PR ref from the push pipeline bound to the
    # webhook SHA, and reject ambiguous matches instead of guessing.
    pipelines_query="${api}/pipelines?sha=${payload_sha}&source=push&order_by=id&sort=desc&per_page=100"
    pipelines_json="$(api_get "${pipelines_query}&page=1")"
    next_page_json="$(api_get "${pipelines_query}&page=2")"
    next_page_count="$(printf '%s' "$next_page_json" | jq -r 'length')"
    (( next_page_count == 0 )) \
        || fail "webhook SHA pipeline lookup spans more than one page"
    matching_pipelines="$(printf '%s' "$pipelines_json" \
        | jq -c --arg expected_ref "$expected_pr_ref" --arg sha "$payload_sha" \
            '[.[] | select(.sha == $sha and .source == "push"
                and ((.ref // "") | test("^pull-request/[0-9]+$"))
                and ($expected_ref == "" or .ref == $expected_ref))]')"
    matching_ref_count="$(printf '%s' "$matching_pipelines" | jq -r '[.[].ref] | unique | length')"
    (( matching_ref_count <= 1 )) \
        || fail "webhook SHA matches multiple pull-request refs"
    build_pipeline_id="$(printf '%s' "$matching_pipelines" | jq -r 'sort_by(.id) | last | .id // empty')"
    pr_ref="$(printf '%s' "$matching_pipelines" | jq -r 'sort_by(.id) | last | .ref // empty')"
    [[ -n "$build_pipeline_id" ]] && break
    (( SECONDS < create_deadline )) || fail "no PR build pipeline appeared within ${create_timeout}s"
    sleep "$poll_interval"
done
[[ "$build_pipeline_id" =~ ^[0-9]+$ ]] || fail "build pipeline id is invalid"
[[ "$pr_ref" =~ ^pull-request/([0-9]+)$ ]] || fail "build pipeline PR ref is invalid"
pr_num="${BASH_REMATCH[1]}"

encoded_ref="$(printf '%s' "$pr_ref" | jq -sRr @uri)"
assert_current_branch_head() {
    local branch_json branch_sha
    branch_json="$(api_get "${api}/repository/branches/${encoded_ref}")"
    branch_sha="$(printf '%s' "$branch_json" | jq -r '.commit.id // empty')"
    [[ "$branch_sha" == "$payload_sha" ]] \
        || fail "${pr_ref} moved to '${branch_sha:-unknown}' before its promotion request was ready"
}
assert_current_branch_head

build_pipeline_json="$(api_get "${api}/pipelines/${build_pipeline_id}")"
build_ref="$(printf '%s' "$build_pipeline_json" | jq -r '.ref // empty')"
build_sha="$(printf '%s' "$build_pipeline_json" | jq -r '.sha // empty')"
build_source="$(printf '%s' "$build_pipeline_json" | jq -r '.source // empty')"
build_status="$(printf '%s' "$build_pipeline_json" | jq -r '.status // empty')"
build_user_id="$(printf '%s' "$build_pipeline_json" | jq -r '.user.id // empty')"
[[ "$build_ref" == "$pr_ref" && "$build_sha" == "$payload_sha" && "$build_source" == "push" ]] \
    || fail "build pipeline provenance does not match the webhook"
[[ "$build_user_id" == "$payload_user_id" ]] \
    || fail "build pipeline user does not match the mirror-push webhook user"
assert_current_branch_head

# Make the automatically-created pipeline easy to locate in GitLab. Naming is
# best-effort and has no role in authorization.
if ! curl -fsS --max-time 30 --request PUT -H "JOB-TOKEN: ${CI_JOB_TOKEN}" \
    -F "name=Promote PR #${pr_num} (${payload_sha:0:8})" \
    "${api}/pipelines/${request_pipeline_id}/metadata" >/dev/null; then
    echo "WARN: could not name request pipeline ${request_pipeline_id}" >&2
fi

{
    echo "VERIFIED_PROMOTE_PR=${pr_num}"
    echo "VERIFIED_PROMOTE_PR_SHA=${payload_sha}"
    echo "VERIFIED_PROMOTE_BUILD_PIPELINE_ID=${build_pipeline_id}"
} > promote-request.env

echo "Promotion request verified for PR #${pr_num}: pipeline ${build_pipeline_id} at ${payload_sha} (${build_status})."
