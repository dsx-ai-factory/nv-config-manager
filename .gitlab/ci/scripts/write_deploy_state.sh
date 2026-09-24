#!/usr/bin/env bash
# Final stage of the test-env promote flow: commit the machine-written
# deployment state for one environment to its env branch in the downstream
# ArgoCD values repository. The ApplicationSet's git file generator reads this
# file and deploys the pinned chart version + image digests.
#
# The deploy-state file is the ONLY file this script touches - human-owned
# overrides on the env branch are never modified.
#
# Requires (FILE artifacts, read as the source of truth - see "Attested inputs"
#          below): promote.env from test-promote-build (PR_NUM, PR_SHA),
#          chart.env from test-promote-chart (PROMOTE_VERSION,
#          BASELINE_REVISION, ENV_BRANCH_REVISION), and digests.env from
#          test-promote-push-images (DIGEST_<IMAGE> x9)
# Requires (eval of test_env_config.sh): NVCM_ENV, NVCM_ENV_BRANCH,
#          NVCM_ENV_NAMESPACE, NVCM_ENV_RELEASE_NAME, NVCM_ENV_STATE_DIR,
#          NVCM_ENV_ARGOCD_APPLICATION
# Requires (protected variables): NV_CONFIG_MANAGER_VALUES_PUSH_TOKEN,
#          NVCM_VALUES_REPO_PATH (or NV_CONFIG_MANAGER_VALUES_REPO_URL),
#          NVCM_CHART_REPO (Helm repo URL ArgoCD reads the chart from, e.g.
#          https://helm.ngc.nvidia.com/nvidian/cfa)
set -euo pipefail

: "${NVCM_ENV:?eval test_env_config.sh first}"
: "${NVCM_ENV_BRANCH:?eval test_env_config.sh first}"
: "${NVCM_ENV_NAMESPACE:?eval test_env_config.sh first}"
: "${NVCM_ENV_RELEASE_NAME:?eval test_env_config.sh first}"
: "${NVCM_ENV_STATE_DIR:?eval test_env_config.sh first}"
: "${NVCM_ENV_ARGOCD_APPLICATION:?eval test_env_config.sh first}"
: "${NVCM_CHART_REPO:?Set NVCM_CHART_REPO to the Helm repo URL ArgoCD reads the chart from}"

# ---------------------------------------------------------------------------
# Attested inputs: read from the producing jobs' FILE artifacts, not from their
# dotenv exports. GitLab ranks pipeline variables above dotenv report variables,
# so an operator-supplied variable of the same name would silently override the
# values these provenance guards compare against. Files cannot be overridden
# that way, so the deployment-affecting inputs are taken from disk.
#   promote.env (test-promote-build)       - resolved PR number + SHA
#   chart.env   (test-promote-chart)       - revisions + verified chart version
#   digests.env (test-promote-push-images) - registry digests as pushed
# ---------------------------------------------------------------------------
promote_attest="${CI_PROJECT_DIR}/promote.env"
chart_attest="${CI_PROJECT_DIR}/chart.env"
digest_attest="${CI_PROJECT_DIR}/digests.env"
for f in "$promote_attest" "$chart_attest" "$digest_attest"; do
    [[ -f "$f" ]] || { echo "ERROR: missing attestation artifact ${f}" >&2; exit 1; }
done

attest() {
    local key="$1" file="$2" val
    val="$(grep -m1 "^${key}=" "$file" | cut -d= -f2- || true)"
    [[ -n "$val" ]] || { echo "ERROR: ${key} missing from $(basename "$file")" >&2; exit 1; }
    printf '%s' "$val"
    return 0
}

PR_NUM="$(attest PR_NUM "$promote_attest")"
PR_SHA="$(attest PR_SHA "$promote_attest")"
PROMOTE_VERSION="$(attest PROMOTE_VERSION "$chart_attest")"
BASELINE_REVISION="$(attest BASELINE_REVISION "$chart_attest")"
ENV_BRANCH_REVISION="$(attest ENV_BRANCH_REVISION "$chart_attest")"
DIGEST_NV_CONFIG_MANAGER="$(attest DIGEST_NV_CONFIG_MANAGER "$digest_attest")"
DIGEST_NV_CONFIG_MANAGER_UI="$(attest DIGEST_NV_CONFIG_MANAGER_UI "$digest_attest")"
DIGEST_NV_CONFIG_MANAGER_KEA="$(attest DIGEST_NV_CONFIG_MANAGER_KEA "$digest_attest")"
DIGEST_NV_CONFIG_MANAGER_KEA_ADMIN="$(attest DIGEST_NV_CONFIG_MANAGER_KEA_ADMIN "$digest_attest")"
DIGEST_NV_CONFIG_MANAGER_NAUTOBOT="$(attest DIGEST_NV_CONFIG_MANAGER_NAUTOBOT "$digest_attest")"
DIGEST_NV_CONFIG_MANAGER_NATS_READY="$(attest DIGEST_NV_CONFIG_MANAGER_NATS_READY "$digest_attest")"
DIGEST_NV_CONFIG_MANAGER_TEMPORAL="$(attest DIGEST_NV_CONFIG_MANAGER_TEMPORAL "$digest_attest")"
DIGEST_NV_CONFIG_MANAGER_TEMPORAL_BOOTSTRAP="$(attest DIGEST_NV_CONFIG_MANAGER_TEMPORAL_BOOTSTRAP "$digest_attest")"
DIGEST_NV_CONFIG_MANAGER_TEMPORAL_UI="$(attest DIGEST_NV_CONFIG_MANAGER_TEMPORAL_UI "$digest_attest")"

if [[ -n "${NV_CONFIG_MANAGER_VALUES_REPO_URL:-}" ]]; then
    # A full URL override is used as-is (provide any auth it needs in the URL).
    values_repo_url="$NV_CONFIG_MANAGER_VALUES_REPO_URL"
    # Credential-free label for logs: strip any "userinfo@" (e.g. oauth2:token@)
    # so an override URL that embeds a token can't leak it into the job log.
    values_repo_display="$(printf '%s' "$NV_CONFIG_MANAGER_VALUES_REPO_URL" | sed -E 's#://[^/@]*@#://#')"
    # No clean project path is available from a full-URL override.
    values_repo_path=""
else
    # A path builds the authenticated GitLab URL with the push token.
    values_repo_path="${NVCM_VALUES_REPO_PATH:?Set NVCM_VALUES_REPO_PATH or NV_CONFIG_MANAGER_VALUES_REPO_URL}"
    values_repo_url="https://oauth2:${NV_CONFIG_MANAGER_VALUES_PUSH_TOKEN}@${CI_SERVER_HOST}/${values_repo_path}.git"
    values_repo_display="$values_repo_path"
fi

state_file="${NVCM_ENV_STATE_DIR}/deploy-state.yaml"
OCCUPANT="${GITLAB_USER_LOGIN:-${GITLAB_USER_NAME:-ci}}"
deploy_attest="${CI_PROJECT_DIR}/deploy.env"

write_deploy_attestation() {
    local git_revision="$1"
    {
        printf 'ARGOCD_APPLICATION=%s\n' "$NVCM_ENV_ARGOCD_APPLICATION"
        printf 'ARGOCD_EXPECTED_CHART_REVISION=%s\n' "$PROMOTE_VERSION"
        printf 'ARGOCD_EXPECTED_GIT_REVISION=%s\n' "$git_revision"
    } > "$deploy_attest"
}

echo "Committing deploy-state for env '${NVCM_ENV}' to ${values_repo_display}@${NVCM_ENV_BRANCH}:${state_file}"

git clone "$values_repo_url" values-repo
cd values-repo

if git ls-remote --heads origin "${NVCM_ENV_BRANCH}" | grep -q "${NVCM_ENV_BRANCH}"; then
    git fetch origin "${NVCM_ENV_BRANCH}"
    git checkout "${NVCM_ENV_BRANCH}"
else
    echo "ERROR: env branch '${NVCM_ENV_BRANCH}' does not exist in ${values_repo_display}." >&2
    echo "Seed it from main first (see the downstream values repository's README migration steps)."
    exit 1
fi

if [[ ! -f "$state_file" ]]; then
    echo "ERROR: ${state_file} not found on ${NVCM_ENV_BRANCH}; the env is not seeded." >&2
    exit 1
fi

# The render gate in test-promote-chart validated this env's overrides at a
# specific env-branch revision. resource_group serializes promote/rollback/
# release runs, but not human pushes to the env branch, so the overrides can
# change in between. Refuse to write deploy-state against overrides that were
# never validated - fail closed and let the operator re-run.
current_env_rev="$(git rev-parse HEAD)"
if [[ "$current_env_rev" != "$ENV_BRANCH_REVISION" ]]; then
    echo "ERROR: ${NVCM_ENV_BRANCH} moved since the render gate validated it." >&2
    echo "  validated: ${ENV_BRANCH_REVISION}"
    echo "  current:   ${current_env_rev}"
    echo "Someone pushed to the env branch mid-promote, so its overrides are"
    echo "unvalidated against this chart. Re-run the promote pipeline."
    exit 1
fi

# Honor a manual hold: an occupant who set hold: true is protecting the slot.
CURRENT_HOLD=$(yq -r '.hold // false' "$state_file")
current_occupant=$(yq -r '.occupant // "none"' "$state_file")
if [[ "$CURRENT_HOLD" = "true" && "$current_occupant" != "$OCCUPANT" ]]; then
    echo "ERROR: ${NVCM_ENV} is on hold by '${current_occupant}' (deploy-state hold: true)." >&2
    echo "Coordinate with them or have them release the hold before promoting."
    exit 1
fi

# Baseline pin: the downstream values repository main SHA whose baseline values the render gate
# validated against, attested by test-promote-chart in chart.env (read above).
# Consuming that exact SHA - rather than re-resolving origin/main here - keeps
# the deployed baseline identical to the one that was validated even if main
# moved in between. Pinning (vs tracking main) also makes rollback exact.
BASELINE_REV="$BASELINE_REVISION"

export NVCM_ENV NVCM_ENV_NAMESPACE NVCM_ENV_BRANCH NVCM_ENV_RELEASE_NAME \
    NVCM_CHART_REPO PROMOTE_VERSION PR_SHA PR_NUM OCCUPANT BASELINE_REV \
    CURRENT_HOLD \
    DIGEST_NV_CONFIG_MANAGER DIGEST_NV_CONFIG_MANAGER_UI \
    DIGEST_NV_CONFIG_MANAGER_KEA DIGEST_NV_CONFIG_MANAGER_KEA_ADMIN \
    DIGEST_NV_CONFIG_MANAGER_NAUTOBOT DIGEST_NV_CONFIG_MANAGER_NATS_READY \
    DIGEST_NV_CONFIG_MANAGER_TEMPORAL DIGEST_NV_CONFIG_MANAGER_TEMPORAL_BOOTSTRAP \
    DIGEST_NV_CONFIG_MANAGER_TEMPORAL_UI
UPDATED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
export UPDATED_AT

trap 'rm -f "${state_file}.new" "${state_file}.merged"' EXIT

yq -n '
  .env = strenv(NVCM_ENV) |
  .namespace = strenv(NVCM_ENV_NAMESPACE) |
  .envBranch = strenv(NVCM_ENV_BRANCH) |
  .releaseName = strenv(NVCM_ENV_RELEASE_NAME) |
  .chartRepo = strenv(NVCM_CHART_REPO) |
  .chartVersion = strenv(PROMOTE_VERSION) |
  .baselineRevision = strenv(BASELINE_REV) |
  .images.nvConfigManager = strenv(DIGEST_NV_CONFIG_MANAGER) |
  .images.nvConfigManagerUi = strenv(DIGEST_NV_CONFIG_MANAGER_UI) |
  .images.kea = strenv(DIGEST_NV_CONFIG_MANAGER_KEA) |
  .images.keaAdmin = strenv(DIGEST_NV_CONFIG_MANAGER_KEA_ADMIN) |
  .images.nautobot = strenv(DIGEST_NV_CONFIG_MANAGER_NAUTOBOT) |
  .images.natsReady = strenv(DIGEST_NV_CONFIG_MANAGER_NATS_READY) |
  .images.temporalServer = strenv(DIGEST_NV_CONFIG_MANAGER_TEMPORAL) |
  .images.temporalBootstrap = strenv(DIGEST_NV_CONFIG_MANAGER_TEMPORAL_BOOTSTRAP) |
  .images.temporalUi = strenv(DIGEST_NV_CONFIG_MANAGER_TEMPORAL_UI) |
  .sourceSHA = strenv(PR_SHA) |
  .pr = (strenv(PR_NUM) | tonumber) |
  .occupant = strenv(OCCUPANT) |
  .updatedAt = strenv(UPDATED_AT) |
  .hold = (strenv(CURRENT_HOLD) == "true")
' > "${state_file}.new"

# Keep the leading DO-NOT-HAND-EDIT comment header from the existing file.
# Assemble into a separate file: redirecting straight onto "$state_file" would
# truncate it before awk could read the header back out of it.
{
    awk '/^---$/ { next } /^#/ { print; next } { exit }' "$state_file"
    cat "${state_file}.new"
} > "${state_file}.merged"
mv "${state_file}.merged" "$state_file"
rm -f "${state_file}.new"

# Snapshot the blessed baseline onto the env branch, in this same commit.
#
# ArgoCD renders every value file for this env from ONE revision of the values
# repository - it rejects a multi-source Application referencing one repo at two
# revisions, so the appset cannot read the baseline from main while reading the
# overrides from the env branch. The baseline therefore has to BE on the branch.
#
# Committing it here, alongside deploy-state.yaml, is what keeps rollback exact:
# re-committing a prior deploy-state also restores the baseline it was rendered
# against, with no dependency on main's history. BASELINE_REV stays in
# deploy-state as provenance recording where this snapshot came from.
baseline_file="${NVCM_ENV_BASELINE_VALUES}"
if ! git cat-file -e "${BASELINE_REV}:${baseline_file}" 2>/dev/null; then
    echo "ERROR: ${BASELINE_REV} does not contain ${baseline_file}." >&2
    echo "The render gate validated against a baseline this commit lacks - refusing"
    echo "to deploy a baseline that was never validated."
    exit 1
fi
git show "${BASELINE_REV}:${baseline_file}" > "$baseline_file"

if git diff --quiet "$state_file" "$baseline_file"; then
    echo "No deploy-state or baseline changes; ${NVCM_ENV} is already at ${PROMOTE_VERSION}."
    write_deploy_attestation "$(git rev-parse HEAD)"
    exit 0
fi

echo "Deploy-state diff:"
git diff "$state_file"
if ! git diff --quiet "$baseline_file"; then
    echo "Baseline snapshot diff (from main @ ${BASELINE_REV}):"
    git diff --stat "$baseline_file"
fi

git add "$state_file" "$baseline_file"
git commit -m "[nvcm CI] Promote PR #${PR_NUM} (${PROMOTE_VERSION}) to ${NVCM_ENV}

Source commit: ${PR_SHA}
Baseline: ${BASELINE_REV}
Triggered by: ${OCCUPANT}
Pipeline: ${CI_PIPELINE_URL}"
git push origin "HEAD:refs/heads/${NVCM_ENV_BRANCH}"
write_deploy_attestation "$(git rev-parse HEAD)"

echo ""
echo "Deploy-state committed. Waiting for ArgoCD to sync ${NVCM_ENV} to chart ${PROMOTE_VERSION} with digest-pinned images."
# Only build the web view URL from a known project path - a full-URL override
# has no clean path and could otherwise produce a malformed/credential URL.
if [[ -n "$values_repo_path" ]]; then
    echo "View: https://${CI_SERVER_HOST}/${values_repo_path}/-/commits/${NVCM_ENV_BRANCH}"
fi
