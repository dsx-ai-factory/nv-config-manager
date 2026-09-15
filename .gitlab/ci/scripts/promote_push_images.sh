#!/usr/bin/env bash
# Stage 2 of the test-env promote flow: turn the untrusted build's artifacts
# into immutable registry artifacts.
#
# For each of the nine built images: download the build job's artifact archive by job
# id (race-free, CI_JOB_TOKEN allowlisted), docker load, retag to
# ${NVCM_IMAGE_REPOSITORY}/<image>:${PROMOTE_VERSION}, push, and capture the
# pushed manifest digest from the registry. Images and tars are deleted
# between iterations to bound dind disk usage.
#
# Provenance is NOT checked here: that the artifacts came from the guarded PR
# HEAD is established once, against GitLab's trusted pipeline metadata, in
# promote_build_pipeline.sh. This job must never trust or execute anything the
# untrusted build wrote.
#
# Requires (from test-promote-build FILE artifact promote.env): PR_SHORT_SHA,
#          PROMOTE_VERSION,
#          BUILD_JOB_ID_<IMAGE> x9
# Requires (from before_script): docker login already performed,
#          NVCM_IMAGE_REPOSITORY exported by image_target_env.sh
# Output:  digests.env - DIGEST_<IMAGE>=sha256:... x9 (dotenv + file artifact)
set -euo pipefail

: "${NVCM_IMAGE_REPOSITORY:?image_target_env.sh exports required}"

api="${CI_API_V4_URL}/projects/${CI_PROJECT_ID}"

# Read the resolved build metadata from test-promote-build's FILE artifact
# rather than its dotenv export: pipeline variables outrank dotenv, so a
# same-named variable could otherwise redirect which job's artifacts are
# downloaded (BUILD_JOB_ID_*) or change the tag images are pushed under.
promote_attest="${CI_PROJECT_DIR}/promote.env"
[[ -f "$promote_attest" ]] || { echo "ERROR: missing attestation artifact ${promote_attest}" >&2; exit 1; }
attest() {
    local key="$1" val
    val="$(grep -m1 "^${key}=" "$promote_attest" | cut -d= -f2- || true)"
    [[ -n "$val" ]] || { echo "ERROR: ${key} missing from promote.env" >&2; exit 1; }
    printf '%s' "$val"
    return 0
}

PROMOTE_VERSION="$(attest PROMOTE_VERSION)"
short_sha="$(attest PR_SHORT_SHA)"

images="nv-config-manager
nv-config-manager-kea
nv-config-manager-kea-admin
nv-config-manager-ui
nv-config-manager-nautobot
nv-config-manager-nats-ready
nv-config-manager-temporal
nv-config-manager-temporal-bootstrap
nv-config-manager-temporal-ui"

: > digests.env

for image in $images; do
    key="$(printf '%s' "$image" | tr 'a-z-' 'A-Z_')"
    job_id="$(attest "BUILD_JOB_ID_${key}")"

    echo ""
    echo "=== ${image} (build job ${job_id}) ==="
    workdir="$(mktemp -d)"

    echo "Downloading artifacts..."
    curl -fsS --retry 3 --retry-delay 5 -H "JOB-TOKEN: ${CI_JOB_TOKEN}" \
        -o "${workdir}/artifacts.zip" \
        "${api}/jobs/${job_id}/artifacts"
    unzip -q "${workdir}/artifacts.zip" -d "$workdir"

    # The artifact contents (image tarball) are attacker-controlled by design -
    # they are the PR's own build. Provenance (that this came from the guarded
    # PR HEAD) is established in promote_build_pipeline.sh against GitLab's
    # trusted pipeline metadata, NOT from any file the build wrote. Never
    # source or eval anything under $workdir here: this job holds registry
    # credentials, so executing untrusted build output would leak them.
    tar_file="${workdir}/images/${image}.tar.gz"
    if [[ ! -f "$tar_file" ]]; then
        echo "ERROR: job ${job_id} artifacts do not contain ${image}.tar.gz" >&2
        ls -laR "$workdir" || true
        exit 1
    fi

    echo "Loading ${image}..."
    docker load -i "$tar_file"

    src="pr-local/${image}:${short_sha}"
    dst="${NVCM_IMAGE_REPOSITORY}/${image}:${PROMOTE_VERSION}"
    docker tag "$src" "$dst"
    echo "Pushing ${dst}..."
    docker push "$dst"

    # Authoritative digest: ask the registry for the pushed manifest digest
    # (the local image id is NOT the registry manifest digest).
    # The `|| true` is required: under `set -euo pipefail` a failing inspect or
    # a non-matching grep would abort the script here, making the fallback (and
    # the validation below) unreachable.
    digest="$(docker buildx imagetools inspect "$dst" --format '{{json .Manifest}}' 2>/dev/null | grep -o '"digest": *"sha256:[0-9a-f]*"' | head -n 1 | cut -d'"' -f4 || true)"
    if [[ -z "$digest" ]]; then
        echo "  imagetools inspect yielded no digest; falling back to docker inspect"
        digest="$(docker inspect --format '{{index .RepoDigests 0}}' "$dst" 2>/dev/null | cut -d@ -f2 || true)"
    fi
    if ! printf '%s' "$digest" | grep -Eq '^sha256:[0-9a-f]{64}$'; then
        echo "ERROR: could not capture a valid digest for ${dst} (got '${digest}')" >&2
        exit 1
    fi
    echo "DIGEST_${key}=${digest}" >> digests.env
    echo "${dst}"
    echo "  digest: ${digest}"

    docker rmi "$src" "$dst" >/dev/null 2>&1 || true
    rm -rf "$workdir"
done

echo ""
echo "All images pushed:"
cat digests.env
