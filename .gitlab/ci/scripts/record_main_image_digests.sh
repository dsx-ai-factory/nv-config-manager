#!/usr/bin/env bash
# Record the main build's multi-architecture digests before exposing promotion.
set -euo pipefail
: "${NVCM_IMAGE_REPOSITORY:?image_target_env.sh exports required}"
: "${CI_COMMIT_SHA:?}"
: > digests.env
printf 'IMAGE_REPOSITORY=%s\n' "$NVCM_IMAGE_REPOSITORY" >> digests.env
printf 'SOURCE_SHA=%s\n' "$CI_COMMIT_SHA" >> digests.env
for image in nv-config-manager nv-config-manager-ui nv-config-manager-kea \
    nv-config-manager-kea-admin nv-config-manager-nautobot nv-config-manager-nats-ready \
    nv-config-manager-temporal nv-config-manager-temporal-bootstrap nv-config-manager-temporal-ui; do
    digest="$(docker buildx imagetools inspect "${NVCM_IMAGE_REPOSITORY}/${image}:${CI_COMMIT_SHA:0:8}" \
        --format '{{json .Manifest}}' | jq -r '.digest')"
    [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "Invalid digest for ${image}" >&2; exit 1; }
    key="$(printf '%s' "$image" | tr 'a-z-' 'A-Z_')"
    printf 'DIGEST_%s=%s\n' "$key" "$digest" >> digests.env
done
