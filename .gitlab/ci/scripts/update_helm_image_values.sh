#!/usr/bin/env bash
set -euo pipefail

values_file="${1:?usage: update_helm_image_values.sh VALUES_FILE IMAGE_REPOSITORY IMAGE_TAG}"
image_repository="${2:?usage: update_helm_image_values.sh VALUES_FILE IMAGE_REPOSITORY IMAGE_TAG}"
image_tag="${3:?usage: update_helm_image_values.sh VALUES_FILE IMAGE_REPOSITORY IMAGE_TAG}"

if [[ ! -f "$values_file" ]]; then
  echo "Values file not found: ${values_file}" >&2
  exit 1
fi

export NVCM_IMAGE_REPOSITORY="${image_repository%/}"
export NVCM_IMAGE_TAG="$image_tag"

yq -i '
  .global.images.nvConfigManager.repository = strenv(NVCM_IMAGE_REPOSITORY) + "/nv-config-manager" |
  .global.images.nvConfigManager.tag = strenv(NVCM_IMAGE_TAG) |
  .global.images.nvConfigManagerUi.repository = strenv(NVCM_IMAGE_REPOSITORY) + "/nv-config-manager-ui" |
  .global.images.nvConfigManagerUi.tag = strenv(NVCM_IMAGE_TAG) |
  .global.images.kea.repository = strenv(NVCM_IMAGE_REPOSITORY) + "/nv-config-manager-kea" |
  .global.images.kea.tag = strenv(NVCM_IMAGE_TAG) |
  .global.images.keaAdmin.repository = strenv(NVCM_IMAGE_REPOSITORY) + "/nv-config-manager-kea-admin" |
  .global.images.keaAdmin.tag = strenv(NVCM_IMAGE_TAG) |
  .global.images.nautobot.repository = strenv(NVCM_IMAGE_REPOSITORY) + "/nv-config-manager-nautobot" |
  .global.images.nautobot.tag = strenv(NVCM_IMAGE_TAG) |
  .global.images.natsReady.repository = strenv(NVCM_IMAGE_REPOSITORY) + "/nv-config-manager-nats-ready" |
  .global.images.natsReady.tag = strenv(NVCM_IMAGE_TAG) |
  .global.images.temporalServer.repository = strenv(NVCM_IMAGE_REPOSITORY) + "/nv-config-manager-temporal" |
  .global.images.temporalServer.tag = strenv(NVCM_IMAGE_TAG) |
  .global.images.temporalBootstrap.repository = strenv(NVCM_IMAGE_REPOSITORY) + "/nv-config-manager-temporal-bootstrap" |
  .global.images.temporalBootstrap.tag = strenv(NVCM_IMAGE_TAG) |
  .global.images.temporalUi.repository = strenv(NVCM_IMAGE_REPOSITORY) + "/nv-config-manager-temporal-ui" |
  .global.images.temporalUi.tag = strenv(NVCM_IMAGE_TAG) |
  .global.images.oidcProxy.repository = strenv(NVCM_IMAGE_REPOSITORY) + "/nv-config-manager-oidc-proxy" |
  .global.images.oidcProxy.tag = strenv(NVCM_IMAGE_TAG)
' "$values_file"
