#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Shared offline runtime and wheel packaging for installer distributions.

pip_download() {
    # The project enforces tool.uv.required-version before uv can access the network.
    uv run --project "$REPO_ROOT/installer" --no-sync --with "$NVCM_PIP_REQUIREMENT" python -m pip download "$@"
}

# Digests are pinned from the immutable upstream release, not fetched with the archive.
# https://github.com/astral-sh/python-build-standalone/releases/tag/20261026
download_installer_python() {
    local installer_dest="$1"
    local triple="$2"
    local pbs_tag="20261026"
    local pbs_cpython="3.13.18"
    local pbs_file="cpython-${pbs_cpython}+${pbs_tag}-${triple}-install_only_stripped.tar.gz"
    local pbs_url="https://github.com/astral-sh/python-build-standalone/releases/download/${pbs_tag}/${pbs_file}"
    local pbs_sha256
    pbs_sha256=$(awk -v file="$pbs_file" '$2 == file {print $1}' "$SCRIPT_DIR/python-runtimes.sha256") || return 1
    if [[ ! "$pbs_sha256" =~ ^[0-9a-f]{64}$ ]]; then
        log_error "Missing pinned Python checksum for $triple"
        return 1
    fi
    local -a checksum_command
    if command -v sha256sum &>/dev/null; then
        checksum_command=(sha256sum -c -)
    else
        checksum_command=(shasum -a 256 -c -)
    fi
    local pbs_stage
    pbs_stage=$(mktemp -d "${installer_dest}/.python-${triple}.XXXXXX") || return 1
    local pbs_archive="$pbs_stage/$pbs_file"
    log_info "Downloading Python ${pbs_cpython} for ${triple}..."
    if ! curl -fsSL --output "$pbs_archive" "$pbs_url"; then
        log_error "Failed to download Python for $triple"
        rm -rf "$pbs_stage"
        return 1
    fi
    if ! printf '%s  %s\n' "$pbs_sha256" "$pbs_archive" | "${checksum_command[@]}"; then
        log_error "Python checksum verification failed for $triple"
        rm -rf "$pbs_stage"
        return 1
    fi
    if ! tar -xz -f "$pbs_archive" -C "$pbs_stage" ||
        [[ ! -x "$pbs_stage/python/bin/python3" && ! -x "$pbs_stage/python/bin/python3.13" ]]; then
        log_error "Failed to extract Python runtime for $triple"
        rm -rf "$pbs_stage"
        return 1
    fi
    local pbs_dest="$installer_dest/python/$triple"
    if ! mkdir -p "$pbs_dest" || ! mv "$pbs_stage/python" "$pbs_dest/"; then
        log_error "Failed to package Python runtime for $triple"
        rm -rf "$pbs_stage" "$pbs_dest"
        return 1
    fi
    rm -rf "$pbs_stage"
    log_success "Verified and packaged Python ${pbs_cpython} for $triple"
}

package_installer() {
    log_info "Packaging nv-config-manager-installer wizard..."

    local installer_src="$REPO_ROOT/installer"
    if [[ ! -f "$installer_src/pyproject.toml" ]]; then
        log_warn "Installer package not found at $installer_src, skipping"
        return 0
    fi

    if ! command -v uv &>/dev/null; then
        log_error "uv is required to package the installer"
        return 1
    fi

    local installer_dest="$BUILD_DIR/installer"
    mkdir -p "$installer_dest/wheels" "$installer_dest/python"

    log_info "Building nv-config-manager-installer wheel..."
    if ! (cd "$installer_src" && uv build --wheel --out-dir "$installer_dest"); then
        log_error "Failed to build nv-config-manager-installer wheel in $installer_dest"
        exit 1
    fi
    if ! ls "$installer_dest"/nv_config_manager_installer-*.whl &>/dev/null; then
        log_error "No nv_config_manager_installer wheel found in $installer_dest after build"
        exit 1
    fi
    log_success "Built nv-config-manager-installer wheel"

    log_info "Exporting pinned requirements..."
    if ! (cd "$installer_src" && uv export --no-dev --no-emit-project \
            --format requirements.txt -o "$installer_dest/requirements.txt" --locked >/dev/null); then
        log_error "Failed to export requirements from installer lockfile"
        exit 1
    fi
    log_success "Exported requirements.txt"

    log_info "Vendoring installer dependency wheels..."
    local -a wheel_platforms=(
        manylinux2014_x86_64
        manylinux_2_17_x86_64
        manylinux2014_aarch64
        manylinux_2_17_aarch64
        macosx_11_0_arm64
        macosx_10_13_x86_64
    )

    for wheel_platform in "${wheel_platforms[@]}"; do
        log_info "Vendoring wheels for ${wheel_platform}..."
        if ! pip_download \
                -r "$installer_dest/requirements.txt" \
                --dest "$installer_dest/wheels" \
                --python-version 3.13 \
                --implementation cp \
                --abi cp313 \
                --only-binary :all: \
                --platform "$wheel_platform"; then
            log_error "Failed to vendor wheels for ${wheel_platform}"
            exit 1
        fi
    done
    log_success "Vendored dependency wheels"

    local -a pbs_triples=(
        x86_64-unknown-linux-gnu
        aarch64-unknown-linux-gnu
        x86_64-apple-darwin
        aarch64-apple-darwin
    )

    for triple in "${pbs_triples[@]}"; do
        if ! download_installer_python "$installer_dest" "$triple"; then
            return 1
        fi
    done

    _write_installer_bootstrap "$installer_dest"
    log_success "Installer packaging complete"
    return 0
}

_write_installer_bootstrap() {
    local dest="$1"
    cat > "$dest/install.sh" << 'INSTALL_EOF'
#!/usr/bin/env bash
# NVIDIA Config Manager Installer Bootstrap
#
# Self-contained installer that uses a bundled Python runtime. No system Python,
# pip, or network access is required on the target host.
#
# Usage:
#   ./install.sh
#   NV_CONFIG_MANAGER_VENV=/opt/nv-config-manager ./install.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OS=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m)
case "${OS}-${ARCH}" in
    linux-x86_64)      PYTHON_TRIPLE="x86_64-unknown-linux-gnu" ;;
    linux-aarch64)     PYTHON_TRIPLE="aarch64-unknown-linux-gnu" ;;
    darwin-x86_64)     PYTHON_TRIPLE="x86_64-apple-darwin" ;;
    darwin-arm64)      PYTHON_TRIPLE="aarch64-apple-darwin" ;;
    *) echo "ERROR: Unsupported platform: ${OS}-${ARCH}"; exit 1 ;;
esac

PYTHON_DIR="$SCRIPT_DIR/python/${PYTHON_TRIPLE}/python"
if [[ ! -d "$PYTHON_DIR" ]]; then
    PYTHON_DIR=$(find "$SCRIPT_DIR/python" -maxdepth 2 -name "python" -type d -path "*${PYTHON_TRIPLE}*" | head -1)
fi
if [[ -z "$PYTHON_DIR" || ! -d "$PYTHON_DIR" ]]; then
    echo "ERROR: No bundled Python found for ${PYTHON_TRIPLE}"
    exit 1
fi

PYTHON="$PYTHON_DIR/bin/python3"
if [[ ! -x "$PYTHON" ]]; then
    PYTHON=$(find "$PYTHON_DIR/bin" -name 'python3.*' -not -name '*.py' -type f | head -1)
fi
if [[ ! -x "$PYTHON" ]]; then
    echo "ERROR: Python binary not executable at $PYTHON_DIR/bin/"
    exit 1
fi

echo "Using bundled Python: $PYTHON"
echo "  Version: $("$PYTHON" --version 2>&1)"

VENV_DIR="${NV_CONFIG_MANAGER_VENV:-$HOME/.nv-config-manager-installer}"
echo "Creating virtual environment at $VENV_DIR ..."
"$PYTHON" -m venv --clear "$VENV_DIR"

echo "Installing nv-config-manager-installer from vendored wheels (offline)..."
"$VENV_DIR/bin/pip" install --quiet --no-index \
    --find-links "$SCRIPT_DIR/wheels/" \
    "$SCRIPT_DIR"/nv_config_manager_installer-*.whl

ln -sf "$VENV_DIR/bin/nv-config-manager-installer" "$SCRIPT_DIR/nv-config-manager-installer"
ln -sf "$VENV_DIR/bin/nvcm-installer" "$SCRIPT_DIR/nvcm-installer"

echo ""
echo "nvcm-installer is ready. The longer nv-config-manager-installer command is also available."
echo ""
echo "Usage:"
echo "  $SCRIPT_DIR/nvcm-installer init --config install.yaml"
echo "  $SCRIPT_DIR/nvcm-installer validate install.yaml"
echo "  $SCRIPT_DIR/nvcm-installer deploy install.yaml --chart-dir /path/to/helm --image-source registry"
echo "  $SCRIPT_DIR/nvcm-installer test install.yaml"
echo ""
echo "Or add to PATH:"
echo "  export PATH=\"$VENV_DIR/bin:\$PATH\""
INSTALL_EOF
    chmod +x "$dest/install.sh"
    log_success "Created installer/install.sh"
    return 0
}
