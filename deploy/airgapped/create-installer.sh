#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Build the offline CLI distribution without charts or container images.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# shellcheck source=deploy/helm/files/python-tooling.sh
. "$REPO_ROOT/deploy/helm/files/python-tooling.sh"
# shellcheck source=deploy/airgapped/installer-packaging.sh
. "$SCRIPT_DIR/installer-packaging.sh"

version="${1:?usage: create-installer.sh VERSION [OUTPUT_DIR]}"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z][0-9A-Za-z.-]*)?$ ]] || {
    echo "Expected a Docker-compatible semver version" >&2; exit 1;
}
output="${2:-$SCRIPT_DIR/output}"
mkdir -p "$output"
output="$(cd "$output" && pwd)"
BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT
log_info() { printf '%s\n' "$*"; }
log_success() { printf '%s\n' "$*"; }
log_warn() { printf '%s\n' "$*" >&2; }
log_error() { printf '%s\n' "$*" >&2; }
package_installer
cp "$REPO_ROOT/LICENSE" "$BUILD_DIR/installer/"
cp "$REPO_ROOT/installer/README.md" "$BUILD_DIR/installer/"
# Match the single-root layout consumed by the offline bootstrap and NMC.
name="nvcm-installer-${version}"
mv "$BUILD_DIR/installer" "$BUILD_DIR/$name"
tar -czf "$output/$name.tar.gz" -C "$BUILD_DIR" "$name"
(cd "$output" && sha256sum "$name.tar.gz" > "$name.tar.gz.sha256")
