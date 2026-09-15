#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
final_validator="${repo_root}/.gitlab/ci/scripts/validate_promote_request.sh"
source_validator="${repo_root}/.gitlab/ci/scripts/validate_promote_source_pipeline.sh"
webhook_fixture="${repo_root}/.gitlab/ci/scripts/tests/fixtures/push_webhook.json"
test_dir="$(mktemp -d)"
trap 'rm -rf "$test_dir"' EXIT

pr_sha="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
main_sha="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

# The validators call only GitLab read endpoints. Return deterministic API
# metadata so the authorization boundary can be tested without network access.
curl() {
    local args=("$@") output_file="" has_job_token=false
    local has_https_proto=false has_https_redirect_proto=false
    local pipeline_name="" i arg
    for ((i = 0; i < ${#args[@]}; i++)); do
        arg="${args[$i]}"
        if [[ "$arg" == "JOB-TOKEN: job-token" ]]; then
            has_job_token=true
        elif [[ "$arg" == "-o" || "$arg" == "--output" ]]; then
            output_file="${args[$((i + 1))]}"
        elif [[ "$arg" == "--proto" && "${args[$((i + 1))]}" == "=https" ]]; then
            has_https_proto=true
        elif [[ "$arg" == "--proto-redir" && "${args[$((i + 1))]}" == "=https" ]]; then
            has_https_redirect_proto=true
        elif [[ "$arg" == name=* ]]; then
            pipeline_name="${arg#name=}"
        fi
    done
    local url="${!#}"
    if [[ "${CI_API_V4_URL:-}" != https://* ]]; then
        echo "curl must not be called for a non-HTTPS CI_API_V4_URL" >&2
        return 99
    fi
    case "$url" in
        */api/v4/job)
            # The mirror's GET /job response omits source.
            printf '{"pipeline":{"id":%s,"ref":"%s"},"user":{"id":%s}}\n' \
                "${MOCK_CURRENT_PIPELINE_ID}" "${MOCK_CURRENT_JOB_REF}" "${MOCK_CURRENT_USER_ID}"
            ;;
        */projects/7/pipelines\?sha=*\&page=1)
            if [[ "${MOCK_AMBIGUOUS_BUILD_REFS}" == true ]]; then
                printf '[{"id":100,"ref":"pull-request/123","sha":"%s","source":"push"},{"id":101,"ref":"pull-request/456","sha":"%s","source":"push"}]\n' \
                    "$pr_sha" "$pr_sha"
            else
                printf '[{"id":100,"ref":"%s","sha":"%s","source":"push"}]\n' \
                    "${MOCK_BUILD_REF}" "$pr_sha"
            fi
            ;;
        */projects/7/pipelines\?sha=*\&page=2)
            if [[ "${MOCK_SECOND_PIPELINE_PAGE}" == true ]]; then
                printf '[{"id":200,"ref":"pull-request/456","sha":"%s","source":"push"}]\n' \
                    "$pr_sha"
            else
                printf '[]\n'
            fi
            ;;
        */projects/7/pipelines/100)
            printf '{"ref":"%s","sha":"%s","status":"success","source":"push","user":{"id":7}}\n' \
                "${MOCK_BUILD_REF}" "$pr_sha" \
                | jq --arg status "${MOCK_BUILD_STATUS:-running}" \
                    --argjson user_id "${MOCK_BUILD_USER_ID:-7}" \
                    '.status = $status | .user.id = $user_id'
            ;;
        */projects/7/repository/branches/pull-request%2F123)
            printf '{"commit":{"id":"%s"}}\n' "$pr_sha" \
                | jq --arg sha "${MOCK_BRANCH_SHA:-$pr_sha}" '.commit.id = $sha'
            ;;
        */projects/7/pipelines/200/jobs\?per_page=100)
            printf '[{"id":202,"name":"test-promote-request-validate","status":"success"}]\n'
            ;;
        */projects/7/pipelines/200)
            printf '{"ref":"main","sha":"%s","status":"running","source":"trigger"}\n' \
                "$main_sha"
            ;;
        */projects/7/pipelines/300/metadata)
            [[ "$pipeline_name" == "Promote PR #123 (${pr_sha:0:8})" ]] || return 96
            printf '{}\n'
            ;;
        */projects/7/pipelines/300)
            printf '{"ref":"%s","sha":"%s","status":"running","source":"%s"}\n' \
                "${MOCK_CURRENT_PIPELINE_REF}" "$main_sha" "${MOCK_CURRENT_SOURCE}"
            ;;
        */projects/7/pipelines/400)
            printf '{"ref":"%s","sha":"%s","status":"running","source":"%s"}\n' \
                "${MOCK_CURRENT_PIPELINE_REF}" "$main_sha" "${MOCK_CURRENT_SOURCE}"
            ;;
        */projects/7/jobs/202/artifacts/promote-request.env)
            [[ "$has_job_token" == true && "$has_https_proto" == true && -n "$output_file" ]] || return 97
            if [[ "${MOCK_ARTIFACT_DIRECT:-false}" == true ]]; then
                printf 'VERIFIED_PROMOTE_PR=%s\nVERIFIED_PROMOTE_PR_SHA=%s\nVERIFIED_PROMOTE_BUILD_PIPELINE_ID=100\n' \
                    "${MOCK_VERIFIED_PR:-123}" "$pr_sha" > "$output_file"
                printf '200\n'
            else
                : > "$output_file"
                printf '302\n%s\n' "${MOCK_ARTIFACT_REDIRECT_URL}"
            fi
            ;;
        https://artifacts.example/promote-request.env)
            [[ "$has_job_token" == false && "$has_https_proto" == true \
                && "$has_https_redirect_proto" == true ]] || {
                echo "job token leaked to artifact redirect" >&2
                return 98
            }
            printf 'VERIFIED_PROMOTE_PR=%s\nVERIFIED_PROMOTE_PR_SHA=%s\nVERIFIED_PROMOTE_BUILD_PIPELINE_ID=100\n' \
                "${MOCK_VERIFIED_PR:-123}" "$pr_sha"
            ;;
        */projects/7/pipelines/200/bridges\?per_page=100)
            printf '[{"id":201,"name":"%s","status":"success","pipeline":{"id":200,"ref":"main","sha":"%s"},"user":{"id":42},"downstream_pipeline":{"id":400}}]\n' \
                "${MOCK_BRIDGE_NAME:-promote-to-test}" "$main_sha"
            ;;
        */projects/7)
            printf '{"default_branch":"main"}\n'
            ;;
        *)
            echo "unexpected curl URL: $url" >&2
            return 1
            ;;
    esac
}
export -f curl
export pr_sha main_sha

run_source_validator() (
    cd "$test_dir"
    local payload_file="$test_dir/push-webhook-under-test.json"
    rm -f promote-request.env
    jq \
        --arg object_kind "${TEST_OBJECT_KIND:-push}" \
        --argjson project_id "${TEST_PROJECT_ID:-7}" \
        --arg ref "${TEST_REF:-refs/heads/pull-request/123}" \
        --arg after "${TEST_AFTER:-$pr_sha}" \
        --arg checkout_sha "${TEST_CHECKOUT_SHA:-$pr_sha}" \
        --argjson user_id "${TEST_USER_ID:-7}" \
        '.object_kind = $object_kind
         | .project_id = $project_id
         | .project.id = $project_id
         | .ref = $ref
         | .after = $after
         | .checkout_sha = $checkout_sha
         | .user_id = $user_id' \
        "$webhook_fixture" > "$payload_file"
    export MOCK_CURRENT_SOURCE="${TEST_CURRENT_SOURCE:-trigger}" MOCK_CURRENT_PIPELINE_ID=300 MOCK_CURRENT_USER_ID=7
    export MOCK_CURRENT_JOB_REF="${TEST_CURRENT_JOB_REF:-main}"
    export MOCK_CURRENT_PIPELINE_REF="${TEST_CURRENT_PIPELINE_REF:-main}"
    export MOCK_BUILD_USER_ID="${TEST_BUILD_USER_ID:-7}"
    export MOCK_BUILD_REF="${TEST_BUILD_REF:-pull-request/123}"
    export MOCK_AMBIGUOUS_BUILD_REFS="${TEST_AMBIGUOUS_BUILD_REFS:-false}"
    export MOCK_SECOND_PIPELINE_PAGE="${TEST_SECOND_PIPELINE_PAGE:-false}"
    export MOCK_BRANCH_SHA="${TEST_BRANCH_SHA:-$pr_sha}"
    export CI_JOB_TOKEN=job-token CI_API_V4_URL="${TEST_CI_API_V4_URL:-https://gitlab.example/api/v4}" CI_PROJECT_ID=7
    export NVCM_MIRROR_API_TOKEN=read-token TRIGGER_PAYLOAD="$payload_file"
    bash "$source_validator" || exit $?
    grep -qx 'VERIFIED_PROMOTE_PR=123' promote-request.env
    grep -qx "VERIFIED_PROMOTE_PR_SHA=${pr_sha}" promote-request.env
    grep -qx 'VERIFIED_PROMOTE_BUILD_PIPELINE_ID=100' promote-request.env
)

run_final_validator() (
    export MOCK_CURRENT_SOURCE="${TEST_CURRENT_SOURCE:-parent_pipeline}" MOCK_CURRENT_PIPELINE_ID=400 MOCK_CURRENT_USER_ID=42
    export MOCK_CURRENT_JOB_REF="${TEST_CURRENT_JOB_REF:-main}"
    export MOCK_CURRENT_PIPELINE_REF="${TEST_CURRENT_PIPELINE_REF:-main}"
    export CI_JOB_TOKEN=job-token CI_API_V4_URL="${TEST_CI_API_V4_URL:-https://gitlab.example/api/v4}" CI_PROJECT_ID=7
    export NVCM_MIRROR_API_TOKEN=read-token
    export NVCM_PROMOTE_PR=123 NVCM_PROMOTE_PR_SHA="$pr_sha"
    export NVCM_PROMOTE_BUILD_PIPELINE_ID=100 NVCM_PROMOTE_ENV=test
    export NVCM_PROMOTE_SOURCE_PIPELINE_ID=200
    export NVCM_PROMOTE_SOURCE_REF=main NVCM_PROMOTE_SOURCE_SHA="$main_sha"
    export MOCK_BRIDGE_NAME="${TEST_BRIDGE_NAME:-promote-to-test}"
    export MOCK_VERIFIED_PR="${TEST_VERIFIED_PR:-123}"
    export MOCK_ARTIFACT_REDIRECT_URL="${TEST_ARTIFACT_REDIRECT_URL:-https://artifacts.example/promote-request.env}"
    export MOCK_ARTIFACT_DIRECT="${TEST_ARTIFACT_DIRECT:-false}"
    bash "$final_validator"
)

assert_final_rejected() {
    local expected="$1" output
    if output="$(run_final_validator 2>&1)"; then
        echo "expected final validator to reject request" >&2
        exit 1
    fi
    grep -Fq "$expected" <<<"$output"
}

assert_source_rejected() {
    local expected="$1" output
    if output="$(run_source_validator 2>&1)"; then
        echo "expected source validator to reject request" >&2
        exit 1
    fi
    grep -Fq "$expected" <<<"$output"
}

run_source_validator
TEST_AMBIGUOUS_BUILD_REFS=true run_source_validator
TEST_REF=main run_source_validator
run_final_validator
TEST_ARTIFACT_DIRECT=true run_final_validator
TEST_CI_API_V4_URL=http://gitlab.example/api/v4 assert_source_rejected "CI_API_V4_URL must use HTTPS"
TEST_CURRENT_SOURCE=web assert_source_rejected "pipeline source 'web' is not a push webhook trigger"
TEST_CURRENT_PIPELINE_REF=other assert_source_rejected "current job and pipeline refs do not match"
TEST_OBJECT_KIND=pipeline assert_source_rejected "webhook event is not a push"
TEST_PROJECT_ID=8 assert_source_rejected "webhook project does not match"
TEST_REF=refs/heads/main assert_source_rejected "is not a PR ref or GitLab-rewritten default branch"
TEST_REF=main TEST_AMBIGUOUS_BUILD_REFS=true assert_source_rejected "matches multiple pull-request refs"
TEST_REF=main TEST_SECOND_PIPELINE_PAGE=true assert_source_rejected "lookup spans more than one page"
TEST_AFTER=cccccccccccccccccccccccccccccccccccccccc assert_source_rejected "after/checkout SHA mismatch"
TEST_BUILD_USER_ID=8 assert_source_rejected "build pipeline user does not match"
TEST_BRANCH_SHA=cccccccccccccccccccccccccccccccccccccccc assert_source_rejected "moved to"
TEST_CI_API_V4_URL=http://gitlab.example/api/v4 assert_final_rejected "CI_API_V4_URL must use HTTPS"
TEST_CURRENT_SOURCE=push assert_final_rejected "pipeline source 'push' is not a PR promotion child pipeline"
TEST_CURRENT_PIPELINE_REF=other assert_final_rejected "current job and pipeline refs do not match"
TEST_ARTIFACT_REDIRECT_URL=http://artifacts.example/promote-request.env assert_final_rejected "artifact redirect must use HTTPS"
TEST_VERIFIED_PR=999 assert_final_rejected "verified request PR does not match"
TEST_BRIDGE_NAME=promote-to-test01 assert_final_rejected "source trigger job"

echo "validate promote request tests passed"
