#!/usr/bin/env bash
# Bind a manual main promotion to its actual parent pipeline and build artifacts.
set -euo pipefail
: "${CI_API_V4_URL:?}"
: "${CI_PROJECT_ID:?}"
: "${CI_JOB_TOKEN:?}"
: "${NVCM_MIRROR_API_TOKEN:?}"
: "${NVCM_PROMOTE_SOURCE_PIPELINE_ID:?}"
: "${NVCM_PROMOTE_SOURCE_SHA:?}"
: "${NVCM_PROMOTE_ENV:?}"
fail() { echo "ERROR: invalid main promotion: $*" >&2; exit 1; }
[[ "$CI_API_V4_URL" == https://* ]] || fail 'API must use HTTPS'
[[ "$NVCM_PROMOTE_SOURCE_PIPELINE_ID" =~ ^[0-9]+$ ]] || fail 'invalid parent id'
[[ "$NVCM_PROMOTE_SOURCE_SHA" =~ ^[0-9a-f]{40}$ ]] || fail 'invalid source SHA'
case "$NVCM_PROMOTE_ENV" in test|test01|kiwi-qa|demo01) ;; *) fail 'unsupported target' ;; esac
api="${CI_API_V4_URL}/projects/${CI_PROJECT_ID}"
get() { curl -fsS --max-time 30 -H "PRIVATE-TOKEN: ${NVCM_MIRROR_API_TOKEN}" "$1"; }
job="$(curl -fsS --max-time 30 -H "JOB-TOKEN: ${CI_JOB_TOKEN}" "${CI_API_V4_URL}/job")"
child_id="$(jq -r '.pipeline.id' <<< "$job")"
[[ "$child_id" =~ ^[0-9]+$ ]] || fail 'missing authenticated child id'
child="$(get "${api}/pipelines/${child_id}")"
project="$(get "$api")"
branch="$(jq -r '.default_branch' <<< "$project")"
parent="$(get "${api}/pipelines/${NVCM_PROMOTE_SOURCE_PIPELINE_ID}")"
sha="$NVCM_PROMOTE_SOURCE_SHA"
jq -e --arg branch "$branch" --arg sha "$sha" \
    '.source == "parent_pipeline" and .ref == $branch and .sha == $sha' <<< "$child" >/dev/null \
    || fail 'child must run the selected main commit'
jq -e --arg branch "$branch" --arg sha "$sha" \
    '.source == "push" and .ref == $branch and .sha == $sha and (.status == "running" or .status == "success")' \
    <<< "$parent" >/dev/null || fail 'parent must be a main push build'
protected="$(get "${api}/repository/branches/${branch}")"
jq -e '.protected == true' <<< "$protected" >/dev/null || fail 'main must be protected'
# Check the literal protected-environment button, never an operator-supplied target.
bridges="$(get "${api}/pipelines/${NVCM_PROMOTE_SOURCE_PIPELINE_ID}/bridges?per_page=100")"
jq -e --arg name "deploy-main-to-${NVCM_PROMOTE_ENV}" --argjson child "$child_id" \
    --argjson parent "$NVCM_PROMOTE_SOURCE_PIPELINE_ID" --arg sha "$sha" --arg branch "$branch" \
    --argjson user "$(jq '.user.id' <<< "$job")" \
    'any(.[]; .name == $name and .downstream_pipeline.id == $child and .pipeline.id == $parent
      and .pipeline.ref == $branch and (.pipeline.sha // .commit.id) == $sha
      and .user.id == $user and $user != null
      and (.status == "pending" or .status == "running" or .status == "success"))' \
    <<< "$bridges" >/dev/null || fail 'matching authorized main button is missing'
jobs='[]'
page=1
while :; do
    batch="$(get "${api}/pipelines/${NVCM_PROMOTE_SOURCE_PIPELINE_ID}/jobs?per_page=100&page=${page}")"
    [[ "$(jq length <<< "$batch")" != 0 ]] || break
    jobs="$(jq -cn --argjson a "$jobs" --argjson b "$batch" '$a + $b')"
    page=$((page + 1))
done
build_job() {
    local found
    found="$(jq -c --arg name "$1" '[.[] | select(.name == $name)] | max_by(.id)' <<< "$jobs")"
    jq -e '.status == "success"' <<< "$found" >/dev/null || fail "$1 did not succeed"
    jq -r '.id' <<< "$found"
}
manifest_id="$(build_job docker-manifest-main)"
chart_id="$(build_job main-build-chart)"
# Redirects are fetched without forwarding the GitLab credential to object storage.
artifact="$(mktemp)"
trap 'rm -f "$artifact"' EXIT
result="$(curl -sS --max-time 30 --proto '=https' -o "$artifact" -w '%{http_code}\n%{redirect_url}' \
    -H "JOB-TOKEN: ${CI_JOB_TOKEN}" "${api}/jobs/${manifest_id}/artifacts/digests.env")"
case "${result%%$'\n'*}" in
    200) ;;
    301|302|303|307|308)
        redirect="${result#*$'\n'}"
        [[ "$redirect" == https://* ]] || fail 'artifact redirect must use HTTPS'
        curl -fsSL --max-time 30 --max-redirs 3 --proto '=https' --proto-redir '=https' "$redirect" -o "$artifact"
        ;;
    *) fail 'build digest artifact is unavailable' ;;
esac
[[ "$(sed -n 's/^SOURCE_SHA=//p' "$artifact")" == "$sha" ]] || fail 'digest source SHA mismatch'
image_config="$(bash "${CI_PROJECT_DIR}/.gitlab/ci/scripts/image_target_env.sh" "${NVCM_VALUES_IMAGE_TARGET:-}")"
eval "$image_config"
[[ "$(sed -n 's/^IMAGE_REPOSITORY=//p' "$artifact")" == "$NVCM_IMAGE_REPOSITORY" ]] \
    || fail 'build image repository does not match the values image target'
: > digests.env
for image in NV_CONFIG_MANAGER NV_CONFIG_MANAGER_UI NV_CONFIG_MANAGER_KEA NV_CONFIG_MANAGER_KEA_ADMIN \
    NV_CONFIG_MANAGER_NAUTOBOT NV_CONFIG_MANAGER_NATS_READY NV_CONFIG_MANAGER_TEMPORAL \
    NV_CONFIG_MANAGER_TEMPORAL_BOOTSTRAP NV_CONFIG_MANAGER_TEMPORAL_UI; do
    digest="$(sed -n "s/^DIGEST_${image}=//p" "$artifact")"
    [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || fail "invalid digest for ${image}"
    printf 'DIGEST_%s=%s\n' "$image" "$digest" >> digests.env
done
cat > promote.env <<EOF
SOURCE_KIND=main
PR_NUM=0
PR_REF=${branch}
PR_SHA=${sha}
PR_SHORT_SHA=${sha:0:8}
PROMOTE_VERSION=0.0.0-main.${sha}
BUILD_PIPELINE_ID=${NVCM_PROMOTE_SOURCE_PIPELINE_ID}
CHART_BUILD_JOB_ID=${chart_id}
EOF
echo "Main build ${sha} verified for ${NVCM_PROMOTE_ENV}."
