#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
output="$(cd "${1:?usage: test_installer_bundle.sh OUTPUT_DIR}" && pwd)"
shopt -s nullglob
archives=("$output"/nvcm-installer-*.tar.gz)
[[ ${#archives[@]} == 1 ]] || { echo "Expected exactly one installer archive" >&2; exit 1; }
archive="${archives[0]}"
(cd "$output" && sha256sum -c "$(basename "$archive").sha256")
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
tar -xzf "$archive" -C "$stage"
installer="$stage/$(basename "$archive" .tar.gz)"
# Bad proxies make accidental network access fail; bootstrap also uses --no-index.
export http_proxy=http://127.0.0.1:9 https_proxy=http://127.0.0.1:9 PIP_NO_INDEX=1
export NV_CONFIG_MANAGER_VENV="$stage/venv"
bash "$installer/install.sh"
"$installer/nvcm-installer" --help
"$installer/nvcm-installer" test --help
"$installer/nvcm-installer" pvc-updater --help
