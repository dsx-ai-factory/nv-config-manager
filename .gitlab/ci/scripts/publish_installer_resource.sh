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
umask 077
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
cp "$output/$archive" "$output/$archive.sha256" "$stage/"
# Keep the authorization header out of command arguments and logs.
printf 'Authorization: Bearer %s\n' "$NGC_CLI_API_KEY" > "$stage/auth-header"
endpoint="https://api.ngc.nvidia.com/v2/org/$NGC_CLI_ORG/team/$NGC_CLI_TEAM/resources/$name"
status="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
    --header "@$stage/auth-header" "$endpoint")"
case "$status" in
    200) ;;
    404)
        ngc registry resource create --display-name 'NVIDIA Config Manager Installer' \
            --short-desc 'Offline Config Manager CLI with bundled Python and dependencies' \
            --application OTHER --framework Other --format generic --precision OTHER "$resource"
        ;;
    *) echo "Resource lookup failed (HTTP $status); refusing to publish" >&2; exit 1 ;;
esac
# Atomically reserve a new version. Never resume an existing publication, including
# another writer's unfinished upload. Do not retry an ambiguous create response.
status="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
    --header "@$stage/auth-header" --header 'Content-Type: application/json' \
    --request POST --data "{\"versionId\":\"$version\"}" "$endpoint/versions")"
case "$status" in
    200|201) ;;
    *) echo "Version reservation failed (HTTP $status); refusing to upload" >&2; exit 1 ;;
esac
rm "$stage/auth-header"
# NGC CLI resumes our newly reserved version and finalizes it after both uploads.
ngc registry resource upload-version --source "$stage" "$resource:$version"
