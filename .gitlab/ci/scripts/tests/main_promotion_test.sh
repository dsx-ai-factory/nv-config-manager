#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
test_dir="$(mktemp -d)"
trap 'rm -rf "$test_dir"' EXIT
export CI_API_V4_URL=https://gitlab.example/api/v4 CI_PROJECT_ID=7 CI_JOB_TOKEN=job-token NVCM_MIRROR_API_TOKEN=read-token
export NVCM_PROMOTE_SOURCE_PIPELINE_ID=100 NVCM_PROMOTE_SOURCE_SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
export MOCK_REF=main MOCK_KIND=main MOCK_MANIFEST=docker-manifest-main MOCK_CHART=main-build-chart MOCK_IMAGE_TAG=aaaaaaaa
export MOCK_TARGET=test MOCK_ARTIFACT_SHA=$NVCM_PROMOTE_SOURCE_SHA MOCK_REPO=registry.example/nvcm
export NVCM_PROMOTE_ENV=test MOCK_SOURCE=push MOCK_SHA=$NVCM_PROMOTE_SOURCE_SHA MOCK_PROTECTED=true MOCK_USER=7 MOCK_STATUS=success MOCK_DIGEST=true MOCK_PAGE=1
curl() {
    local url="${!#}" file='' previous='' arg
    for arg in "$@"; do
        [[ "$previous" != -o ]] || file="$arg"
        previous="$arg"
    done
    case "$url" in
        */job) printf '{"pipeline":{"id":200,"ref":"%s"},"user":{"id":7}}' "$MOCK_REF" ;;
        */projects/7) echo '{"default_branch":"main"}' ;;
        */pipelines/200) printf '{"source":"parent_pipeline","ref":"%s","sha":"%s"}' "$MOCK_REF" "$NVCM_PROMOTE_SOURCE_SHA" ;;
        */pipelines/100) printf '{"source":"%s","ref":"%s","sha":"%s","status":"success"}' "$MOCK_SOURCE" "$MOCK_REF" "$MOCK_SHA" ;;
        */repository/branches/main) printf '{"protected":%s}' "$MOCK_PROTECTED" ;;
        */repository/tags/*) printf '{"protected":%s,"commit":{"id":"%s"}}' "$MOCK_PROTECTED" "$MOCK_SHA" ;;
        */bridges*) printf '[{"name":"deploy-%s-to-%s","downstream_pipeline":{"id":200},"pipeline":{"id":100,"ref":"%s","sha":"%s"},"user":{"id":%s},"status":"success"}]' "$MOCK_KIND" "$MOCK_TARGET" "$MOCK_REF" "$NVCM_PROMOTE_SOURCE_SHA" "$MOCK_USER" ;;
        */jobs\?*page=1)
            if [[ "$MOCK_PAGE" == 2 ]]; then echo '[{"id":5,"name":"other","status":"success"}]'; else
                printf '[{"id":10,"name":"%s","status":"%s"},{"id":11,"name":"%s","status":"success"}]' "$MOCK_MANIFEST" "$MOCK_STATUS" "$MOCK_CHART"
            fi ;;
        */jobs\?*page=2)
            if [[ "$MOCK_PAGE" == 2 ]]; then
                printf '[{"id":10,"name":"%s","status":"%s"},{"id":11,"name":"%s","status":"success"}]' "$MOCK_MANIFEST" "$MOCK_STATUS" "$MOCK_CHART"
            else echo '[]'; fi ;;
        */jobs\?*page=3) echo '[]' ;;
        */artifacts/digests.env)
            printf 'IMAGE_REPOSITORY=%s\n' "$MOCK_REPO" > "$file"
            printf 'IMAGE_TAG=%s\n' "$MOCK_IMAGE_TAG" >> "$file"
            printf 'SOURCE_SHA=%s\n' "$MOCK_ARTIFACT_SHA" >> "$file"
            for key in NV_CONFIG_MANAGER NV_CONFIG_MANAGER_UI NV_CONFIG_MANAGER_KEA NV_CONFIG_MANAGER_KEA_ADMIN NV_CONFIG_MANAGER_NAUTOBOT NV_CONFIG_MANAGER_NATS_READY NV_CONFIG_MANAGER_TEMPORAL NV_CONFIG_MANAGER_TEMPORAL_BOOTSTRAP NV_CONFIG_MANAGER_TEMPORAL_UI; do
                if [[ "$MOCK_DIGEST" == true ]]; then printf 'DIGEST_%s=sha256:%064d\n' "$key" 0 >> "$file"; else printf 'DIGEST_%s=bad\n' "$key" >> "$file"; fi
            done
            printf '200\n' ;;
        *) echo "Unexpected endpoint: $url" >&2; return 99 ;;
    esac
}
export -f curl
export CI_PROJECT_DIR="$repo_root" NVCM_IMAGE_REPOSITORY=registry.example/nvcm
cd "$test_dir"
validator="$repo_root/.gitlab/ci/scripts/prepare_main_promotion.sh"
for env in test test01 kiwi-qa demo01; do
    export NVCM_PROMOTE_ENV="$env" MOCK_TARGET="$env"
    bash "$validator" > /dev/null
    [[ "$(sed -n 's/^PR_SHA=//p' promote.env)" == "$NVCM_PROMOTE_SOURCE_SHA" ]]
    [[ "$(sed -n 's/^CHART_BUILD_JOB_ID=//p' promote.env)" == 11 ]]
    [[ "$(wc -l < digests.env | tr -d ' ')" == 9 ]]
done
export MOCK_PAGE=2
bash "$validator" > /dev/null
reject() { if bash "$validator" > /dev/null 2>&1; then echo "Expected rejection: $1" >&2; exit 1; fi; }
export NVCM_PROMOTE_ENV=prod; reject production; export NVCM_PROMOTE_ENV=test MOCK_TARGET=test
export MOCK_SOURCE=trigger; reject trigger; export MOCK_SOURCE=push
export MOCK_SHA=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb; reject mismatched-sha; export MOCK_SHA=$NVCM_PROMOTE_SOURCE_SHA
export MOCK_PROTECTED=false; reject unprotected; export MOCK_PROTECTED=true
export MOCK_USER=8; reject operator; export MOCK_USER=7
export MOCK_STATUS=failed; reject failed-build; export MOCK_STATUS=success
export MOCK_TARGET=demo01; reject wrong-button; export MOCK_TARGET=test
export MOCK_ARTIFACT_SHA=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb; reject artifact-sha; export MOCK_ARTIFACT_SHA=$NVCM_PROMOTE_SOURCE_SHA
export MOCK_REPO=registry.example/other; reject repository; export MOCK_REPO=registry.example/nvcm
export MOCK_DIGEST=false; reject invalid-digest
# Reuse the same fixtures to check RC artifacts and the tag authorization boundary.
export NVCM_PROMOTE_SOURCE_TAG=1.4.0-rc.4 MOCK_REF=1.4.0-rc.4 MOCK_KIND=rc MOCK_MANIFEST=update-version MOCK_CHART=helm-publish-release MOCK_IMAGE_TAG=1.4.0-rc.4 MOCK_DIGEST=true
for env in test test01 kiwi-qa demo01; do
    export NVCM_PROMOTE_ENV="$env" MOCK_TARGET="$env"
    bash "$validator" > /dev/null
    [[ "$(sed -n 's/^PROMOTE_VERSION=//p' promote.env)" == 1.4.0-rc.4 ]]
    [[ "$(sed -n 's/^SOURCE_KIND=//p' promote.env)" == rc ]]
done
export MOCK_PROTECTED=false; reject unprotected-rc; export MOCK_PROTECTED=true
export MOCK_IMAGE_TAG=aaaaaaaa; reject wrong-release-image-tag; export MOCK_IMAGE_TAG=1.4.0-rc.4
export MOCK_STATUS=failed; reject failed-release; export MOCK_STATUS=success
export NVCM_PROMOTE_SOURCE_TAG=1.4.0 MOCK_REF=1.4.0; reject stable-tag
export NVCM_PROMOTE_SOURCE_TAG=1.4.0-beta.1 MOCK_REF=1.4.0-beta.1; reject other-prerelease
unset NVCM_PROMOTE_SOURCE_TAG
# Exercise the build-side digest recorder as well as the consuming validator.
docker() {
    if [[ "${MOCK_DIGEST}" == true ]]; then printf '{"digest":"sha256:%064d"}' 0; else echo '{"digest":"invalid"}'; fi
}
export -f docker
export CI_COMMIT_SHA=$NVCM_PROMOTE_SOURCE_SHA MOCK_DIGEST=true
bash "$repo_root/.gitlab/ci/scripts/record_main_image_digests.sh"
[[ "$(sed -n 's/^SOURCE_SHA=//p' digests.env)" == "$CI_COMMIT_SHA" ]]
[[ "$(wc -l < digests.env | tr -d ' ')" == 12 ]]
export MOCK_DIGEST=false
if bash "$repo_root/.gitlab/ci/scripts/record_main_image_digests.sh" > /dev/null 2>&1; then
    echo 'Recorder accepted an invalid registry digest' >&2; exit 1
fi
printf 'Main and RC promotion checks passed (four targets, pagination, provenance and digest rejection cases).\n'
