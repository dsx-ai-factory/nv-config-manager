#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
version="${1:?usage: publish_installer_resource.sh VERSION OUTPUT_DIR}"
output="$(cd "${2:?missing output directory}" && pwd)"
: "${NVCM_INSTALLER_NGC_PATH:?Set the resource org/team in GitLab variables}"
: "${NVCM_INSTALLER_NGC_TOKEN_VAR:?Set the resource credential variable name}"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z][0-9A-Za-z.-]*)?$ ]] || exit 1
[[ "$NVCM_INSTALLER_NGC_PATH" =~ ^[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+$ ]] || exit 1
[[ "$NVCM_INSTALLER_NGC_TOKEN_VAR" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]] || exit 1
[[ -n "${!NVCM_INSTALLER_NGC_TOKEN_VAR:-}" ]] || { echo "Resource credential is unset" >&2; exit 1; }
export NGC_CLI_API_KEY="${!NVCM_INSTALLER_NGC_TOKEN_VAR}"
unset NGC_API_KEY
export NGC_CLI_ORG="${NVCM_INSTALLER_NGC_PATH%/*}" NGC_CLI_TEAM="${NVCM_INSTALLER_NGC_PATH#*/}"
name="${NVCM_INSTALLER_NGC_RESOURCE:-nvcm-installer}"
[[ "$name" =~ ^[a-zA-Z0-9_-]+$ ]] || exit 1
resource="${NVCM_INSTALLER_NGC_PATH}/${name}"
archive="nvcm-installer-${version}.tar.gz"
(cd "$output" && sha256sum -c "$archive.sha256")
# Upload only the two verified release files, never the checkout or credentials.
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
cp "$output/$archive" "$output/$archive.sha256" "$stage/"
if ! ngc registry resource info "$resource" >/dev/null 2>&1; then
    ngc registry resource create --display-name 'NVIDIA Config Manager Installer' \
        --short-desc 'Offline Config Manager CLI with bundled Python and dependencies' \
        --application OTHER --framework Other --format generic --precision OTHER "$resource"
fi
# Immutable release versions: a retry must not remove or overwrite a publication.
if ngc registry resource info "$resource:$version" >/dev/null 2>&1; then
    echo 'Installer resource version already exists; refusing to overwrite it' >&2
    exit 1
fi
ngc registry resource upload-version --source "$stage" "$resource:$version"
