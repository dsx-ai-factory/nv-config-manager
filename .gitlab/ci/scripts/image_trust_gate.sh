#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Run only from trusted main/tag checkouts, never from downloaded PR sources.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT
images=(nv-config-manager nv-config-manager-kea nv-config-manager-kea-admin
    nv-config-manager-ui nv-config-manager-nautobot nv-config-manager-nats-ready
    nv-config-manager-temporal nv-config-manager-temporal-bootstrap nv-config-manager-temporal-ui)

audit_image() {
    local ref="$1" image="$2"
    local options=()
    [[ "$image" != nv-config-manager-ui ]] || options+=(--node)
    mkdir -p "$workdir/home"
    # Protected GitLab variables are available to trusted orchestration, but
    # never to the audit process. Use an empty HOME/registry config as well.
    # The image's Node binary runs with no network or host mounts in the checker.
    env -i PATH="$PATH" HOME="$workdir/home" DOCKER_CONFIG="$workdir/home" \
        DOCKER_HOST="${DOCKER_HOST:-unix:///var/run/docker.sock}" UV_PYTHON_DOWNLOADS=never \
        sh "$repo_root/build/check-image-trust.sh" "$ref" "${options[@]}"
}

attest() {
    local value
    value="$(grep -m1 "^$1=" "${CI_PROJECT_DIR}/promote.env" | cut -d= -f2-)"
    [[ -n "$value" ]] || { echo "Missing $1 in promote.env" >&2; exit 1; }
    printf '%s' "$value"
}

case "${1:-}" in
    main)
        arch="${2:?Specify amd64 or arm64}"
        [[ "$arch" == amd64 || "$arch" == arm64 ]] || exit 2
        mkdir -p audited-images
        : > "audited-images/$arch.txt"
        for image in "${images[@]}"; do
            tag="${NVCM_IMAGE_REPOSITORY:?}/$image:${CI_COMMIT_SHORT_SHA:?}-$arch"
            digest="$(docker buildx imagetools inspect "$tag" --format '{{json .Manifest}}' | jq -r '.digest')"
            [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "Invalid digest for $tag" >&2; exit 1; }
            ref="${NVCM_IMAGE_REPOSITORY}/$image@$digest"
            docker pull --platform "linux/$arch" "$ref"
            actual_platform="$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$ref")"
            [[ "$actual_platform" == "linux/$arch" ]] || { echo "Unexpected platform: $actual_platform" >&2; exit 1; }
            audit_image "$ref" "$image"
            printf '%s\n' "$ref" >> "audited-images/$arch.txt"
            docker image rm "$ref"
        done
        ;;
    manifests)
        for image in "${images[@]}"; do
            refs=()
            for arch in amd64 arm64; do
                prefix="${NVCM_IMAGE_REPOSITORY:?}/$image@"
                ref="$(grep -F "$prefix" "audited-images/$arch.txt")"
                digest="${ref#"$prefix"}"
                [[ "$ref" == "$prefix"* && "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || {
                    echo "Missing or invalid audited $arch digest for $image" >&2; exit 1;
                }
                refs+=("$ref")
            done
            # Never resolve the mutable architecture tags again after auditing.
            docker buildx imagetools create -t "${NVCM_IMAGE_REPOSITORY}/$image:${CI_COMMIT_SHORT_SHA:?}" "${refs[@]}"
        done
        ;;
    promote)
        short_sha="$(attest PR_SHORT_SHA)"
        [[ "$short_sha" =~ ^[0-9a-f]{8}$ ]] || exit 1
        : > audited-pr-images.sha256
        for image in "${images[@]}"; do
            key="$(printf '%s' "$image" | tr 'a-z-' 'A-Z_')"
            job_id="$(attest "BUILD_JOB_ID_$key")"
            [[ "$job_id" =~ ^[0-9]+$ ]] || exit 1
            curl -fsS --retry 3 --retry-delay 5 -H "JOB-TOKEN: ${CI_JOB_TOKEN:?}" \
                -o "$workdir/artifacts.zip" \
                "${CI_API_V4_URL:?}/projects/${CI_PROJECT_ID:?}/jobs/$job_id/artifacts"
            # Read only the expected member. Never extract PR-controlled paths
            # or symlinks into the trusted checkout or audit workspace.
            archive="$workdir/$image.tar.gz"
            unzip -p "$workdir/artifacts.zip" "images/$image.tar.gz" > "$archive"
            docker load --input "$archive"
            ref="$(docker image inspect --format '{{.Id}}' "pr-local/$image:$short_sha")"
            [[ "$ref" =~ ^sha256:[0-9a-f]{64}$ ]] || exit 1
            audit_image "$ref" "$image"
            hash="$(sha256sum "$archive" | cut -d' ' -f1)"
            printf '%s  %s.tar.gz\n' "$hash" "$image" >> audited-pr-images.sha256
            docker image rm "$ref"
            rm -f "$archive" "$workdir/artifacts.zip"
        done
        ;;
    verify-archive)
        image="${2:?Specify image name}"
        archive="${3:?Specify downloaded archive}"
        hash="$(awk -v name="$image.tar.gz" '$2 == name {print $1}' "${CI_PROJECT_DIR}/audited-pr-images.sha256")"
        [[ "$hash" =~ ^[0-9a-f]{64}$ ]] || { echo "Missing audit receipt for $image" >&2; exit 1; }
        printf '%s  %s\n' "$hash" "$archive" | sha256sum --check -
        ;;
    *) echo "Usage: $0 main ARCH | manifests | promote | verify-archive IMAGE ARCHIVE" >&2; exit 2 ;;
esac
