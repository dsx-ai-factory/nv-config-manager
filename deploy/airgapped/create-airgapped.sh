#!/usr/bin/env bash
#
# NVIDIA Config Manager Air-Gapped Tarball Creator
#
# Creates architecture-specific install bundles with:
# - The nv-config-manager Helm chart and packaged chart archive
# - Dependency charts and dependency manifests used by offline installs
# - Container image archives discovered from the chart, dependency charts, manifests, and extraimages.config
# - Offline installer package and bootstrap script
# - Registry upload helper for an OCI-compliant registry
# - Image loader manifests for environments that still preload node runtimes directly
#
# Output:
#   nv-config-manager-airgapped-<version>-amd64.tar.gz   - Bundle for AMD64/x86_64
#   nv-config-manager-airgapped-<version>-arm64.tar.gz   - Bundle for ARM64/aarch64
#
# Usage: ./create-airgapped.sh [OPTIONS]
#
# Options:
#   --version VERSION       Version tag for tarball naming (default: from Chart.yaml)
#   --output DIR            Output directory (default: ./output)
#   --runtime RUNTIME       Container runtime for pulling/exporting: auto, docker, or containerd (default: auto)
#   --ngc-api-key KEY       NGC API key (or set NGC_REGISTRY_TOKEN/NGC_API_KEY env var)
#   --skip-images           Skip pulling container image archives
#   --local-image-fallback  Save a locally tagged image when pulling its source ref fails
#   --allow-missing-images  Continue even if one or more images cannot be pulled or saved
#   --skip-chart            Skip chart/dependency chart packaging
#   --skip-docs             Skip copying documentation source
#   --include-skopeo        Include the build host Skopeo binary in tools/skopeo/
#   --include-agpl-observability Include AGPL Grafana/Loki/Tempo observability charts and related images
#   --skopeo-binary PATH    Skopeo binary to include (default: command -v skopeo)
#   --extra-images-file PATH Additional image references, one per line, for this bundle
#   --arch ARCH             Build only for specific architecture: amd64, arm64, or both (default: both)
#   --help                  Show help message
#
# Config Files:
#   charts.config            Extra Helm charts whose images should be included
#   ../operator-versions.env Operator/dependency chart and CRD versions used for image discovery
#   extraimages.config       Additional images not discovered from charts
#   --extra-images-file      Site-specific images, including external DCIM provider packages
#
set -euo pipefail

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script location
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Chart is in deploy/helm/ (sibling to deploy/airgapped/)
CHART_DIR="$(cd "$SCRIPT_DIR/../helm" && pwd)"
# Repo root is two levels up from deploy/airgapped/
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# Deploy directory (Helm chart, configs, and operator version pins)
DEPLOY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# Shared operator/dependency version manifest
OPERATOR_VERSIONS_FILE="$DEPLOY_DIR/operator-versions.env"

# Default configuration
VERSION=""
OUTPUT_DIR="${OUTPUT_DIR:-./output}"
SKIP_IMAGES=false
LOCAL_IMAGE_FALLBACK=${LOCAL_IMAGE_FALLBACK:-false}
ALLOW_MISSING_IMAGES=${ALLOW_MISSING_IMAGES:-false}
SKIP_CHART=false
SKIP_DOCS=false
TARGET_ARCH="both"  # amd64, arm64, or both
NGC_REGISTRY="nvcr.io"
NGC_ORG="nvidian/cfa"
# Support both NGC_REGISTRY_TOKEN (org-level) and NGC_API_KEY (user-level)
NGC_API_KEY="${NGC_REGISTRY_TOKEN:-${NGC_API_KEY:-}}"
CONTAINER_RUNTIME="${CONTAINER_RUNTIME:-auto}"  # auto, docker, or containerd
INCLUDE_SKOPEO="${INCLUDE_SKOPEO:-false}"
INCLUDE_AGPL_OBSERVABILITY="${INCLUDE_AGPL_OBSERVABILITY:-false}"
SKOPEO_BINARY="${SKOPEO_BINARY:-}"
EXTRA_IMAGES_FILE="${EXTRA_IMAGES_FILE:-}"
BUILD_DIR=""

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --version)
            VERSION="$2"
            shift 2
            ;;
        --output)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --runtime)
            CONTAINER_RUNTIME="$2"
            shift 2
            ;;
        --ngc-api-key)
            NGC_API_KEY="$2"
            shift 2
            ;;
        --skip-images)
            SKIP_IMAGES=true
            shift
            ;;
        --local-image-fallback)
            LOCAL_IMAGE_FALLBACK=true
            shift
            ;;
        --allow-missing-images)
            ALLOW_MISSING_IMAGES=true
            shift
            ;;
        --skip-chart)
            SKIP_CHART=true
            shift
            ;;
        --skip-docs)
            SKIP_DOCS=true
            shift
            ;;
        --include-skopeo)
            INCLUDE_SKOPEO=true
            shift
            ;;
        --include-agpl-observability)
            INCLUDE_AGPL_OBSERVABILITY=true
            shift
            ;;
        --skopeo-binary)
            SKOPEO_BINARY="$2"
            shift 2
            ;;
        --extra-images-file)
            EXTRA_IMAGES_FILE="$2"
            shift 2
            ;;
        --arch)
            TARGET_ARCH="$2"
            if [[ "$TARGET_ARCH" != "amd64" && "$TARGET_ARCH" != "arm64" && "$TARGET_ARCH" != "both" ]]; then
                echo -e "${RED}Invalid architecture: $TARGET_ARCH (must be amd64, arm64, or both)${NC}"
                exit 1
            fi
            shift 2
            ;;
        --help)
            sed -n '2,/^set -euo pipefail/p' "$0" | sed '/^set -euo pipefail/d; s/^# \?//'
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            exit 1
            ;;
    esac
done

# Helper functions
log_info() {
    local message="$1"
    echo -e "${BLUE}ℹ${NC} $message"
    return 0
}

log_success() {
    local message="$1"
    echo -e "${GREEN}✓${NC} $message"
    return 0
}

log_warn() {
    local message="$1"
    echo -e "${YELLOW}⚠${NC} $message"
    return 0
}

log_error() {
    local message="$1"
    echo -e "${RED}✗${NC} $message"
    return 0
}

load_operator_versions() {
    if [[ ! -f "$OPERATOR_VERSIONS_FILE" ]]; then
        log_error "Operator versions manifest not found: $OPERATOR_VERSIONS_FILE"
        exit 1
    fi

    # shellcheck disable=SC1090
    source "$OPERATOR_VERSIONS_FILE"

    local missing_versions=()
    for name in \
        GATEWAY_API_VERSION \
        ENVOY_GATEWAY_VERSION \
        CERT_MANAGER_VERSION \
        CNPG_OPERATOR_VERSION \
        PROMETHEUS_CRD_VERSION \
        PROMETHEUS_OPERATOR_VERSION; do
        if [[ -z "${!name:-}" ]]; then
            missing_versions+=("$name")
        fi
    done

    if [[ ${#missing_versions[@]} -gt 0 ]]; then
        log_error "Missing operator version pin(s): ${missing_versions[*]}"
        exit 1
    fi

    return 0
}

detect_container_runtime() {
    if [[ "$CONTAINER_RUNTIME" != "auto" ]]; then
        log_info "Using specified container runtime: $CONTAINER_RUNTIME"
        return 0
    fi
    
    log_info "Detecting container runtime..."
    
    # Check for containerd/ctr first (more common in K8s environments)
    if command -v ctr &> /dev/null && command -v crictl &> /dev/null; then
        CONTAINER_RUNTIME="containerd"
        log_info "Detected containerd runtime"
    elif command -v docker &> /dev/null; then
        CONTAINER_RUNTIME="docker"
        log_info "Detected Docker runtime"
    else
        log_error "No container runtime detected"
        log_error "Please install either:"
        log_error "  - Docker (docker), OR"
        log_error "  - containerd (ctr + crictl)"
        exit 1
    fi
    return 0
}

check_prerequisites() {
    log_info "Checking prerequisites..."
    load_operator_versions
    
    local missing_tools=()
    
    # Check for required tools
    for tool in helm tar gzip jq curl skopeo; do
        if ! command -v "$tool" &> /dev/null; then
            missing_tools+=("$tool")
        fi
    done
    
    if [[ ${#missing_tools[@]} -gt 0 ]]; then
        log_error "Missing required tools: ${missing_tools[*]}"
        log_error "Please install them and try again."
        exit 1
    fi
    
    # Check that the chart exists
    if [[ ! -f "$CHART_DIR/Chart.yaml" ]]; then
        log_error "Chart.yaml not found in $CHART_DIR"
        log_error "This script should be run from the bootstrap/ directory of nv-config-manager-chart"
        exit 1
    fi
    
    # Only require NGC_API_KEY if we're pulling images
    if [[ "$SKIP_IMAGES" != "true" && -z "$NGC_API_KEY" ]]; then
        log_warn "NGC_REGISTRY_TOKEN/NGC_API_KEY not set"
        log_warn "Images from NVCR may fail to pull without authentication"
        log_info "Set NGC_REGISTRY_TOKEN or NGC_API_KEY environment variable, or use --ngc-api-key"
    fi
    
    # Detect and verify container runtime
    if [[ "$SKIP_IMAGES" != "true" ]]; then
        detect_container_runtime
        
        if [[ "$CONTAINER_RUNTIME" = "docker" ]]; then
            if ! docker info &> /dev/null; then
                log_error "Docker is not running. Please start Docker and try again."
                exit 1
            fi
        elif [[ "$CONTAINER_RUNTIME" = "containerd" ]]; then
            if ! ctr version &> /dev/null; then
                log_error "containerd is not running or not accessible."
                exit 1
            fi
        fi
    fi
    
    log_success "All prerequisites met"
    return 0
}

setup_ngc_authentication() {
    log_info "Setting up NGC authentication..."
    
    if [[ -z "$NGC_API_KEY" ]]; then
        log_warn "NGC_REGISTRY_TOKEN/NGC_API_KEY not set"
        log_info "Attempting to pull images without NVCR authentication..."
        return 0
    fi
    
    # Login to NVCR with NGC API key
    log_info "Logging in to NVCR (nvcr.io)..."
    
    if [[ "$CONTAINER_RUNTIME" = "docker" ]]; then
        if echo "$NGC_API_KEY" | docker login nvcr.io --username '$oauthtoken' --password-stdin &> /dev/null; then
            log_success "Successfully authenticated with NVCR"
            return 0
        else
            log_error "Failed to authenticate with NVCR"
            log_error "Please check your NGC_API_KEY and try again"
            exit 1
        fi
    elif [[ "$CONTAINER_RUNTIME" = "containerd" ]]; then
        # For containerd, we'll pass credentials directly to ctr image pull
        log_success "NGC API key is set - will pass credentials directly to ctr image pull"
        return 0
    fi
    return 0
}

setup_build_dir() {
    log_info "Setting up build directory..."
    
    # Create output directory if it doesn't exist
    if ! mkdir -p "$OUTPUT_DIR" 2>/dev/null; then
        log_warn "Cannot write to $OUTPUT_DIR (might be read-only)"
        log_info "Using /tmp for output directory instead"
        OUTPUT_DIR="/tmp/nv-config-manager-bootstrap-output-$$"
        mkdir -p "$OUTPUT_DIR"
    fi
    
    # Create temporary build directory
    export TMPDIR="${TMPDIR:-/tmp}"
    TMPDIR="${TMPDIR%/}"
    BUILD_DIR=$(mktemp -d "${TMPDIR}/nv-config-manager-bootstrap-XXXXXX" 2>/dev/null || echo "${TMPDIR}/nv-config-manager-bootstrap-$$-$(date +%s)")
    mkdir -p "$BUILD_DIR"
    trap "rm -rf '$BUILD_DIR'" EXIT
    
    log_success "Build directory: $BUILD_DIR"
    log_info "Output directory: $OUTPUT_DIR"
    return 0
}

get_version() {
    if [[ -z "$VERSION" ]]; then
        # Extract version from Chart.yaml
        VERSION=$(grep '^version:' "$CHART_DIR/Chart.yaml" | awk '{print $2}' | tr -d '"' | tr -d "'")
        if [[ -z "$VERSION" ]]; then
            VERSION="0.0.0-dev"
        fi
    fi
    log_info "Using version: $VERSION"
    return 0
}

sanitize_agpl_chart_dependencies() {
    local helm_dir="$1"

    if [[ "$INCLUDE_AGPL_OBSERVABILITY" == true ]]; then
        return 0
    fi

    log_warn "Excluding AGPL Grafana, Loki, and Tempo chart dependencies from the airgap bundle"
    rm -f "$helm_dir"/charts/grafana-*.tgz "$helm_dir"/charts/loki-*.tgz "$helm_dir"/charts/tempo-*.tgz
    rm -f "$helm_dir/Chart.lock"

    python3 - "$helm_dir/Chart.yaml" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
lines = path.read_text().splitlines(keepends=True)
out = []
i = 0
while i < len(lines):
    line = lines[i]
    if (
        line.startswith("  - name: grafana")
        or line.startswith("  - name: loki")
        or line.startswith("  - name: tempo")
    ):
        i += 1
        while i < len(lines):
            nxt = lines[i]
            if nxt.startswith("  - name: ") or (nxt and not nxt.startswith(" ") and nxt.strip()):
                break
            if nxt.startswith("    ") or not nxt.strip():
                i += 1
                continue
            break
        continue
    out.append(line)
    i += 1
path.write_text("".join(out))
PY
    return 0
}

package_helm_chart() {
    if [[ "$SKIP_CHART" = true ]]; then
        log_warn "Skipping Helm chart packaging"
        return 0
    fi
    
    log_info "Packaging Helm chart from local source..."
    
    # Create helm/ directory for bundled installer workflows
    local helm_dir="$BUILD_DIR/helm"
    mkdir -p "$helm_dir"
    
    # Update chart dependencies first
    log_info "Updating chart dependencies..."
    helm dependency update "$CHART_DIR" 2>/dev/null || log_warn "No dependencies to update"
    
    # Copy chart source directly to helm/ for offline installer use
    log_info "Copying chart source..."
    cp -r "$CHART_DIR"/* "$helm_dir/"
    sanitize_agpl_chart_dependencies "$helm_dir"
    
    # Also create packaged .tgz for reference
    log_info "Creating packaged chart..."
    helm package "$helm_dir" --destination "$helm_dir"
    
    local chart_tgz=$(ls "$helm_dir"/*.tgz 2>/dev/null | head -1)
    if [[ -n "$chart_tgz" ]]; then
        log_success "Helm chart ready: $helm_dir/ (packaged: $(basename "$chart_tgz"))"
    else
        log_success "Helm chart ready: $helm_dir/"
    fi
    return 0
}

load_external_charts() {
    # Operator charts are generated from deploy/operator-versions.env.
    echo "oci://ghcr.io/cloudnative-pg/charts/cloudnative-pg:${CNPG_OPERATOR_VERSION}"
    echo "oci://quay.io/jetstack/charts/cert-manager:${CERT_MANAGER_VERSION}"
    echo "helm://https://prometheus-community.github.io/helm-charts|prometheus-operator-crds:28.0.1"
    # Reconciles the render-consumer ScaledObjects. Keep the version in sync with
    # _KEDA_CHART_VERSION in installer/src/nv_config_manager_installer/deployer.py,
    # which resolves this tarball out of the bundle when airgapped.
    echo "helm://https://kedacore.github.io/charts|keda:2.20.1"
    if [[ "$INCLUDE_AGPL_OBSERVABILITY" == true ]]; then
        echo "helm://https://prometheus-community.github.io/helm-charts|kube-prometheus-stack:${PROMETHEUS_OPERATOR_VERSION}"
    fi
    echo "oci://docker.io/envoyproxy/gateway-helm:${ENVOY_GATEWAY_VERSION}"

    # Load any additional charts from charts.config.
    local charts_config="$SCRIPT_DIR/charts.config"
    
    if [[ ! -f "$charts_config" ]]; then
        return 0
    fi
    
    while IFS= read -r line || [[ -n "$line" ]]; do
        # Skip empty lines and comments
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        # Remove leading/trailing whitespace
        line=$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        [[ -n "$line" ]] && echo "$line"
    done < "$charts_config"
    return 0
}

package_external_charts() {
    if [[ "$SKIP_CHART" = true ]]; then
        log_warn "Skipping external chart packaging"
        return 0
    fi
    
    local charts_dir="$BUILD_DIR/charts"
    mkdir -p "$charts_dir"
    
    # Load operator charts from the shared manifest plus any extra charts from config.
    local external_charts=()
    while IFS= read -r line; do
        [[ -n "$line" ]] && external_charts+=("$line")
    done < <(load_external_charts)
    
    if [[ ${#external_charts[@]} -eq 0 ]]; then
        log_info "No external charts configured"
        return 0
    fi
    
    log_info "Packaging ${#external_charts[@]} external chart(s)..."
    
    for chart_entry in "${external_charts[@]}"; do
        local pull_success=false
        
        if [[ "$chart_entry" == oci://* ]]; then
            # OCI registry format: oci://registry/path/chart:version
            local oci_url="${chart_entry%:*}"  # Remove version
            local chart_version="${chart_entry##*:}"
            local chart_name=$(basename "$oci_url")
            
            log_info "Pulling $chart_name v$chart_version from OCI registry..."
            if helm pull "$oci_url" --version "$chart_version" --destination "$charts_dir" 2>/dev/null; then
                pull_success=true
                log_success "Downloaded: ${chart_name}-${chart_version}.tgz"
            else
                log_warn "Failed to pull $chart_entry from OCI registry"
            fi
        elif [[ "$chart_entry" == helm://* ]]; then
            # Traditional Helm repo format: helm://repo-url|chart-name:version
            local helm_spec="${chart_entry#helm://}"  # Remove helm:// prefix
            local repo_url="${helm_spec%%|*}"          # Extract repo URL (before |)
            local chart_spec="${helm_spec#*|}"         # Extract chart:version (after |)
            local chart_name="${chart_spec%%:*}"       # Extract chart name
            local chart_version="${chart_spec#*:}"     # Extract version
            
            if [[ -z "$repo_url" || -z "$chart_name" || -z "$chart_version" ]]; then
                log_warn "Invalid helm entry: $chart_entry (expected helm://repo-url|chart-name:version)"
                continue
            fi
            
            log_info "Pulling $chart_name v$chart_version from Helm repo..."
            
            # Add repo temporarily with a unique name
            local temp_repo_name="nv-config-manager-temp-$(date +%s)"
            if helm repo add "$temp_repo_name" "$repo_url" &>/dev/null; then
                helm repo update "$temp_repo_name" &>/dev/null
                if helm pull "$temp_repo_name/$chart_name" --version "$chart_version" --destination "$charts_dir" 2>/dev/null; then
                    pull_success=true
                    log_success "Downloaded: ${chart_name}-${chart_version}.tgz"
                else
                    log_warn "Failed to pull $chart_name v$chart_version from $repo_url"
                fi
                helm repo remove "$temp_repo_name" &>/dev/null
            else
                log_warn "Failed to add Helm repo: $repo_url"
            fi
        else
            # NGC format: chart-name:version
            local chart_name="${chart_entry%%:*}"
            local chart_version="${chart_entry#*:}"
            
            if [[ -z "$chart_name" || -z "$chart_version" ]]; then
                log_warn "Invalid chart entry: $chart_entry (expected chart-name:version or oci://...)"
                continue
            fi
            
            local chart_tgz="${chart_name}-${chart_version}.tgz"
            local helm_repo_url="https://helm.ngc.nvidia.com/${NGC_ORG}/charts"
            local chart_url="${helm_repo_url}/${chart_tgz}"
            
            log_info "Pulling $chart_name v$chart_version from NGC..."
            
            local temp_pull_dir=$(mktemp -d)
            pushd "$temp_pull_dir" &> /dev/null
            
            if [[ -n "$NGC_API_KEY" ]]; then
                if helm fetch "$chart_url" --username='$oauthtoken' --password="$NGC_API_KEY" 2>/dev/null; then
                    pull_success=true
                fi
            else
                log_warn "NGC_API_KEY not set, trying without auth..."
                if helm fetch "$chart_url" 2>/dev/null; then
                    pull_success=true
                fi
            fi
            
            if [[ "$pull_success" = true ]] && ls *.tgz 2>/dev/null | head -1 | grep -q .; then
                mv *.tgz "$charts_dir/"
                log_success "Packaged: $chart_tgz"
            else
                log_warn "Failed to pull $chart_name:$chart_version from NGC"
            fi
            
            popd &> /dev/null
            rm -rf "$temp_pull_dir"
        fi
    done
    return 0
}

# Package dependency manifests (Gateway API CRDs, Prometheus Operator CRDs)
# These are required for airgapped deployment
# Note: Envoy Gateway is installed via the chart generated from operator-versions.env.
package_dependency_manifests() {
    if [[ "$SKIP_CHART" = true ]]; then
        log_warn "Skipping dependency manifest packaging"
        return 0
    fi
    
    local manifests_dir="$BUILD_DIR/manifests"
    mkdir -p "$manifests_dir"
    
    log_info "Packaging dependency manifests for airgapped deployment..."
    
    # Note: Envoy Gateway is installed via Helm chart from operator-versions.env
    # Only Gateway API CRDs need to be downloaded as raw manifests
    
    # Download Gateway API CRDs
    log_info "Downloading Gateway API CRDs ${GATEWAY_API_VERSION}..."
    local gateway_api_url="https://github.com/kubernetes-sigs/gateway-api/releases/download/${GATEWAY_API_VERSION}/standard-install.yaml"
    if curl -sSL "$gateway_api_url" -o "$manifests_dir/gateway-api-${GATEWAY_API_VERSION}.yaml" 2>/dev/null; then
        log_success "Downloaded: gateway-api-${GATEWAY_API_VERSION}.yaml"
    else
        log_warn "Failed to download Gateway API CRDs"
    fi
    
    # 3. Download Prometheus Operator CRDs (for --enable-monitoring)
    log_info "Downloading Prometheus Operator CRDs ${PROMETHEUS_CRD_VERSION}..."
    local prometheus_crd_url="https://github.com/prometheus-operator/prometheus-operator/releases/download/${PROMETHEUS_CRD_VERSION}/stripped-down-crds.yaml"
    if curl -sSL "$prometheus_crd_url" -o "$manifests_dir/prometheus-operator-crds-${PROMETHEUS_CRD_VERSION}.yaml" 2>/dev/null; then
        log_success "Downloaded: prometheus-operator-crds-${PROMETHEUS_CRD_VERSION}.yaml"
    else
        log_warn "Failed to download Prometheus Operator CRDs"
    fi
    
    log_success "Dependency manifests packaged"
    return 0
}

extract_images_from_external_charts() {
    local charts_dir="$1"
    local images=()
    
    # Find all external chart tarballs (excluding nv-config-manager)
    for chart_tgz in "$charts_dir"/*.tgz; do
        [[ -f "$chart_tgz" ]] || continue
        
        local chart_basename=$(basename "$chart_tgz" .tgz)
        # Skip the main nv-config-manager chart
        [[ "$chart_basename" == nv-config-manager* ]] && continue
        
        # Extract to temp dir and get images
        local temp_extract=$(mktemp -d)
        tar -xzf "$chart_tgz" -C "$temp_extract" 2>/dev/null || continue
        
        # Find the chart directory inside
        local chart_dir=$(find "$temp_extract" -maxdepth 1 -type d ! -path "$temp_extract" | head -1)
        [[ -d "$chart_dir" ]] || { rm -rf "$temp_extract"; continue; }
        
        # Use helm template to extract images
        local templated_output
        templated_output=$(helm template test "$chart_dir" 2>/dev/null) || true
        
        if [[ -n "$templated_output" ]]; then
            # Extract image references
            while IFS= read -r img; do
                [[ -n "$img" ]] && images+=("$img")
            done < <(echo "$templated_output" | grep -E 'image:|repository:' | \
                     sed -E 's/.*image:[[:space:]]*["'"'"']?([^"'"'"'[:space:]]+)["'"'"']?.*/\1/' | \
                     grep -E '^[a-zA-Z0-9]' | sort -u)
        fi
        
        rm -rf "$temp_extract"
    done
    
    printf '%s\n' "${images[@]}" | sort -u
    return $?
}

load_extra_images() {
    # Load product defaults plus an optional site-specific image list. The latter
    # is used for images that cannot be discovered from our chart, such as an
    # external DCIM provider wheel image selected in install.yaml.
    local extra_images_file
    local line
    local -a image_files=("$SCRIPT_DIR/extraimages.config")

    if [[ -n "$EXTRA_IMAGES_FILE" ]]; then
        image_files+=("$EXTRA_IMAGES_FILE")
    fi

    for extra_images_file in "${image_files[@]}"; do
        if [[ ! -f "$extra_images_file" ]]; then
            if [[ "$extra_images_file" == "$EXTRA_IMAGES_FILE" ]]; then
                log_error "Extra images file not found: $extra_images_file" >&2
                return 1
            fi
            continue
        fi

        while IFS= read -r line || [[ -n "$line" ]]; do
            # Skip empty lines and comments
            [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
            # Remove leading/trailing whitespace
            line=$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
            [[ -n "$line" ]] && echo "$line"
        done < "$extra_images_file"
    done
    return 0
}

normalize_image_reference() {
    local image_value="$1"
    local first_segment="${image_value%%/*}"
    first_segment="${first_segment%%:*}"

    if [[ "$first_segment" != *.* &&
        "$first_segment" != "docker.io" &&
        "$first_segment" != "nvcr.io" &&
        "$first_segment" != "ghcr.io" &&
        "$first_segment" != "quay.io" &&
        "$first_segment" != "gcr.io" &&
        "$first_segment" != "registry.k8s.io" ]]; then
        if [[ "$image_value" == */* ]]; then
            image_value="docker.io/${image_value}"
        else
            image_value="docker.io/library/${image_value}"
        fi
    fi

    printf '%s\n' "$image_value"
    return 0
}

extract_images_from_manifests() {
    # Extract container images from downloaded YAML manifests (e.g., Envoy Gateway, etc.)
    local manifests_dir="$1"
    local images=()
    
    if [[ ! -d "$manifests_dir" ]]; then
        return 0
    fi
    
    # Find all YAML files in manifests directory
    for manifest_file in "$manifests_dir"/*.yaml "$manifests_dir"/*.yml; do
        [[ -f "$manifest_file" ]] || continue
        
        # Extract image references from the manifest
        # Look for "image:" fields in Kubernetes manifests
        while IFS= read -r image_line; do
            # Remove leading whitespace and "image:" prefix, strip quotes
            local image_value=$(echo "$image_line" | sed -E 's/^[[:space:]]*image:[[:space:]]*//' | sed -E 's/^["'\''"]|["'\''"]$//g')
            
            # Skip empty, null, or invalid values
            if [[ -z "$image_value" || "$image_value" = "null" || "$image_value" = "~" ]]; then
                continue
            fi
            
            # Skip template variables ({{ }})
            if echo "$image_value" | grep -qE '\{\{|}}'; then
                continue
            fi
            
            # Skip if it doesn't look like an image reference
            if ! echo "$image_value" | grep -qE '^[a-zA-Z0-9._/-]+(:[a-zA-Z0-9._-]+)?$|^[a-zA-Z0-9.-]+(/[a-zA-Z0-9._/-]+)+(:[a-zA-Z0-9._-]+)?$'; then
                continue
            fi
            
            # Add default tag if missing
            if ! echo "$image_value" | grep -q ':'; then
                image_value="${image_value}:latest"
            fi
            
            image_value=$(normalize_image_reference "$image_value")
            
            images+=("$image_value")
        done < <(grep -iE '^\s*image:' "$manifest_file" 2>/dev/null | sed 's/^[[:space:]]*//')
    done
    
    # Remove duplicates and return
    printf '%s\n' "${images[@]}" | sort -u
    return $?
}

extract_images_from_chart() {
    local charts_dir="$1"
    local images=()
    
    # NOTE: Don't use log_* functions here - output is captured via process substitution
    # Write any messages to stderr instead
    
    # Check if helm is available
    if ! command -v helm &> /dev/null; then
        printf '%s\n' "${images[@]}"
        return 0
    fi
    
    # Find the extracted chart directory
    local chart_dir=$(find "$charts_dir" -maxdepth 1 -type d -name "nv-config-manager*" | head -1)
    if [[ -z "$chart_dir" || ! -f "$chart_dir/Chart.yaml" ]]; then
        # Try the packaged tgz
        chart_dir="$charts_dir"
    fi
    
    # Use helm template to render the chart and extract image references
    # Use values-airgapped-extract.yaml to provide minimal required values
    local values_file="$SCRIPT_DIR/values-airgapped-extract.yaml"
    local templated_output
    local helm_err=""
    local helm_exit
    
    if [[ -f "$values_file" ]]; then
        if helm_err=$(helm template test "$chart_dir" -f "$values_file" 2>&1); then
            helm_exit=0
        else
            helm_exit=$?
        fi
    else
        if helm_err=$(helm template test "$chart_dir" 2>&1); then
            helm_exit=0
        else
            helm_exit=$?
        fi
    fi
    
    if [[ $helm_exit -eq 0 ]]; then
        templated_output="$helm_err"
    else
        # Write error to stderr so it doesn't mix with image output
        echo "helm template failed: $helm_err" >&2
        templated_output=""
    fi
    
    if [[ -n "$templated_output" ]]; then
        # Extract image references from the templated output
        while IFS= read -r image_line; do
            # Remove leading whitespace and "image:" prefix
            local image_value=$(echo "$image_line" | sed -E 's/^[[:space:]]*image:[[:space:]]*//' | sed -E 's/^["'\''"]|["'\''"]$//g')
            
            # Skip empty, null, or invalid values
            if [[ -z "$image_value" || "$image_value" = "null" || "$image_value" = "~" ]]; then
                continue
            fi
            
            # Skip template variables ({{ }})
            if echo "$image_value" | grep -qE '\{\{|}}'; then
                continue
            fi
            
            # Skip if it doesn't look like an image reference
            if ! echo "$image_value" | grep -qE '^[a-zA-Z0-9._/-]+(:[a-zA-Z0-9._-]+)?$|^[a-zA-Z0-9.-]+(/[a-zA-Z0-9._/-]+)+(:[a-zA-Z0-9._-]+)?$'; then
                continue
            fi
            
            # Add default tag if missing
            if ! echo "$image_value" | grep -q ':'; then
                image_value="${image_value}:latest"
            fi
            
            image_value=$(normalize_image_reference "$image_value")
            
            images+=("$image_value")
        done < <(echo "$templated_output" | grep -iE '^\s*image:' | sed 's/^[[:space:]]*//')
    fi
    
    # Remove duplicates and return
    printf '%s\n' "${images[@]}" | sort -u
    return $?
}


find_skopeo_for_copy() {
    if [[ -n "$SKOPEO_BINARY" && -x "$SKOPEO_BINARY" ]]; then
        printf '%s\n' "$SKOPEO_BINARY"
        return 0
    fi
    if command -v skopeo &>/dev/null; then
        command -v skopeo
        return 0
    fi
    return 1
}

save_with_skopeo_archive() {
    local image="$1"
    local archive="$2"
    local arch="$3"
    local skopeo_bin
    skopeo_bin=$(find_skopeo_for_copy) || return 1

    local -a skopeo_args=(copy --override-os linux --override-arch "$arch")
    if [[ "$image" == nvcr.io/* && -n "$NGC_API_KEY" ]]; then
        skopeo_args+=(--src-creds "\$oauthtoken:${NGC_API_KEY}")
    fi

    "$skopeo_bin" "${skopeo_args[@]}" "docker://${image}" "docker-archive:${archive}:${image}"
    return $?
}

save_image_archive() {
    local image="$1"
    local archive="$2"
    local arch="$3"

    if docker save --platform "linux/$arch" -o "$archive" "$image"; then
        return 0
    fi

    rm -f "$archive"
    log_warn "  Docker could not export $image for linux/$arch; trying Skopeo archive copy"
    if save_with_skopeo_archive "$image" "$archive" "$arch"; then
        return 0
    fi

    rm -f "$archive"
    return 1
}

pull_docker_images() {
    local arch="${1:-amd64}"
    
    if [[ "$SKIP_IMAGES" = true ]]; then
        log_warn "Skipping Docker image pulling"
        return 0
    fi
    
    # Setup NGC authentication before pulling images (only on first call)
    if [[ "$arch" = "amd64" || "$TARGET_ARCH" = "arm64" ]]; then
        setup_ngc_authentication
    fi
    
    log_info "Pulling and saving Docker images for $arch architecture..."
    
    local images_dir="$BUILD_DIR/images-$arch"
    local helm_dir="$BUILD_DIR/helm"
    mkdir -p "$images_dir"
    
    # Extract images from chart
    log_info "Extracting image references from chart..."
    local images=()
    local temp_err=$(mktemp)
    if [[ -d "$helm_dir" ]]; then
        local extracted_output
        extracted_output=$(extract_images_from_chart "$helm_dir" 2>"$temp_err")
        while IFS= read -r line; do
            [[ -n "$line" ]] && images+=("$line")
        done <<< "$extracted_output"
    fi
    
    # Log any helm template errors
    if [[ -s "$temp_err" ]]; then
        log_warn "Helm template errors encountered:"
        while IFS= read -r err_line; do
            [[ -n "$err_line" ]] && log_info "  $err_line"
        done < "$temp_err"
    fi
    rm -f "$temp_err"
    
    # Load extra images from extraimages.config
    log_info "Loading extra images from extraimages.config..."
    local extra_images=()
    while IFS= read -r line; do
        [[ -n "$line" ]] && extra_images+=("$line")
    done < <(load_extra_images)
    
    if [[ ${#extra_images[@]} -gt 0 ]]; then
        log_info "Found ${#extra_images[@]} extra image(s) in extraimages.config"
        images+=("${extra_images[@]}")
    fi
    
    # Extract images from external charts (from charts.config in charts/ dir)
    local charts_dir="$BUILD_DIR/charts"
    log_info "Extracting images from external charts..."
    local external_chart_images=()
    while IFS= read -r line; do
        [[ -n "$line" ]] && external_chart_images+=("$line")
    done < <(extract_images_from_external_charts "$charts_dir")
    
    if [[ ${#external_chart_images[@]} -gt 0 ]]; then
        log_info "Found ${#external_chart_images[@]} image(s) from external charts"
        images+=("${external_chart_images[@]}")
    fi
    
    # Extract images from dependency manifests (Envoy Gateway, Gateway API, etc.)
    local manifests_dir="$BUILD_DIR/manifests"
    log_info "Extracting images from dependency manifests..."
    local manifest_images=()
    while IFS= read -r line; do
        [[ -n "$line" ]] && manifest_images+=("$line")
    done < <(extract_images_from_manifests "$manifests_dir")
    
    if [[ ${#manifest_images[@]} -gt 0 ]]; then
        log_info "Found ${#manifest_images[@]} image(s) from dependency manifests (Envoy Gateway, etc.)"
        images+=("${manifest_images[@]}")
    fi
    
    # Remove duplicates
    local unique_images=()
    while IFS= read -r img; do
        [[ -n "$img" ]] && unique_images+=("$img")
    done < <(printf '%s\n' "${images[@]}" | sort -u)
    images=("${unique_images[@]}")
    
    if [[ ${#images[@]} -eq 0 ]]; then
        log_warn "No images found"
        return 0
    fi
    
    log_info "Found ${#images[@]} unique image(s) to pull"
    
    local image_list_file="$images_dir/image-list.txt"
    local failed_images=()
    : > "$image_list_file"
    
    for image in "${images[@]}"; do
        local filename=$(echo "$image" | sed 's|[/:@]|-|g').tar
        
        log_info "  Pulling $image..."
        
        local pull_success=false
        
        if [[ "$CONTAINER_RUNTIME" = "docker" ]]; then
            set +e
            local pull_err=""
            pull_err=$(docker pull --platform linux/$arch "$image" 2>&1)
            local pull_exit=$?
            set -e
            
            if [[ $pull_exit -eq 0 ]]; then
                log_success "  Pulled $image"
                log_info "  Saving $image to $filename..."
                save_image_archive "$image" "$images_dir/$filename" "$arch"
                pull_success=true
            else
                log_warn "  Failed to pull $image for $arch"
                if [[ -n "$pull_err" ]]; then
                    log_info "  Error: $(echo "$pull_err" | grep -v "^$" | head -3 | tr '\n' ' ')"
                fi
                if [[ "$LOCAL_IMAGE_FALLBACK" = true ]] && docker image inspect "$image" &>/dev/null; then
                    log_warn "  Using local image for $image because remote pull failed"
                    log_info "  Saving $image to $filename..."
                    if save_image_archive "$image" "$images_dir/$filename" "$arch"; then
                        pull_success=true
                    else
                        rm -f "$images_dir/$filename"
                        log_warn "  Failed to save local image $image"
                    fi
                fi
            fi
        elif [[ "$CONTAINER_RUNTIME" = "containerd" ]]; then
            # Use ctr for pulling and exporting with containerd
            # Pass credentials directly for NVCR images
            local pull_exit=1
            local export_exit=1
            
            # Pull with specified platform for all images (multi-arch manifests)
            set +e  # Temporarily disable exit on error
            if [[ "$image" == nvcr.io/* && -n "$NGC_API_KEY" ]]; then
                # NVCR images - pull with specified platform
                # Retry up to 3 times for large images
                local pull_output=""
                local retry_count=0
                pull_exit=1
                while [[ $retry_count -lt 3 && $pull_exit -ne 0 ]]; do
                    if [[ $retry_count -gt 0 ]]; then
                        log_info "  Retrying pull (attempt $((retry_count + 1))/3)..."
                        sleep 2
                    fi
                    pull_output=$(timeout 300 ctr image pull --snapshotter=native --platform linux/$arch -u '$oauthtoken:'"$NGC_API_KEY" "$image" 2>&1)
                    pull_exit=$?
                    
                    # Check if timeout occurred
                    if echo "$pull_output" | grep -q "timeout\|timed out\|Killed"; then
                        log_warn "  Pull timed out (attempt $((retry_count + 1))/3)"
                        pull_exit=1
                    fi
                    retry_count=$((retry_count + 1))
                done
                
                # Log error if pull failed
                if [[ $pull_exit -ne 0 ]]; then
                    log_info "  Pull error: $(echo "$pull_output" | grep -v "^$" | head -5 | tr '\n' ' ')"
                fi
            else
                # For public registries, pull with platform specification
                local pull_output=""
                # Try with native snapshotter first (avoids overlayfs whiteout issues in CI)
                pull_output=$(ctr image pull --snapshotter=native --platform linux/$arch "$image" 2>&1)
                pull_exit=$?
                # If native snapshotter fails, try default overlayfs
                if [[ $pull_exit -ne 0 ]]; then
                    pull_output=$(ctr image pull --platform linux/$arch "$image" 2>&1)
                    pull_exit=$?
                fi
                # Log error if pull failed
                if [[ $pull_exit -ne 0 ]]; then
                    log_warn "  Pull failed for $arch architecture (image may not have $arch variant)"
                fi
            fi
            set -e  # Re-enable exit on error
            
            # Verify architecture matches what we requested
            if [[ $pull_exit -eq 0 ]]; then
                # Check actual platforms of pulled image
                local list_output=$(ctr image list "name==$image" 2>&1)
                # PLATFORMS is the 6th field
                local platforms=$(echo "$list_output" | awk 'NR==2 {print $6}')
                
                if [[ -n "$platforms" && "$platforms" != "-" ]]; then
                    # Check if our requested architecture is in the platforms list
                    if echo "$platforms" | grep -q "linux/$arch"; then
                        log_info "  Verified architecture: linux/$arch"
                    else
                        log_warn "  Image $image does not have linux/$arch (available: $platforms)"
                        log_warn "  Removing image and skipping..."
                        ctr image rm "$image" &>/dev/null || true
                        pull_exit=1
                    fi
                else
                    log_warn "  Could not verify architecture for $image - continuing anyway"
                fi
            fi
            
            if [[ $pull_exit -eq 0 ]]; then
                log_success "  Pulled $image"
                log_info "  Exporting $image to $filename..."
                set +e  # Temporarily disable exit on error
                
                # Export the image with platform filter to get single-arch tarball
                local export_err=""
                export_err=$(ctr image export --platform linux/$arch "$images_dir/$filename" "$image" 2>&1)
                export_exit=$?
                
                # If export failed, try re-pulling
                if [[ $export_exit -ne 0 ]] && echo "$export_err" | grep -q "content digest.*not found"; then
                    log_info "  Export failed, re-pulling image..."
                    # Remove the image first to ensure clean state
                    ctr image rm "$image" &> /dev/null || true
                    # Re-pull (use native snapshotter to avoid whiteout issues)
                    if [[ "$image" == nvcr.io/* && -n "$NGC_API_KEY" ]]; then
                        ctr image pull --snapshotter=native --platform linux/$arch -u '$oauthtoken:'"$NGC_API_KEY" "$image" &> /dev/null || true
                    else
                        ctr image pull --snapshotter=native --platform linux/$arch "$image" &> /dev/null || true
                    fi
                    # Wait a moment for content to be available
                    sleep 2
                    # Try export again with platform filter
                    rm -f "$images_dir/$filename"
                    export_err=$(ctr image export --platform linux/$arch "$images_dir/$filename" "$image" 2>&1)
                    export_exit=$?
                fi
                set -e  # Re-enable exit on error
                
                # Check if export succeeded and file exists with non-zero size
                if [[ $export_exit -eq 0 && -f "$images_dir/$filename" ]]; then
                    local file_size=$(stat -f%z "$images_dir/$filename" 2>/dev/null || stat -c%s "$images_dir/$filename" 2>/dev/null || echo "0")
                    if [[ "$file_size" -gt 0 ]]; then
                        pull_success=true
                        log_success "  Exported $image (${file_size} bytes)"
                    else
                        log_warn "  Export created empty file for $image (removing)"
                        rm -f "$images_dir/$filename"
                        if [[ -n "$export_err" ]]; then
                            log_info "  Export error: $export_err"
                        fi
                    fi
                else
                    log_warn "  Failed to export $image (pull succeeded but export failed)"
                    # Remove empty file if it was created
                    [[ -f "$images_dir/$filename" && ! -s "$images_dir/$filename" ]] && rm -f "$images_dir/$filename"
                    if [[ -n "$export_err" ]]; then
                        log_info "  Export error: $export_err"
                    fi
                fi
            else
                log_warn "  Failed to pull $image (might not exist or no access)"
            fi
        fi
        
        if [[ "$pull_success" = true ]]; then
            log_success "  Saved $image"
            echo "$image" >> "$image_list_file"
        else
            failed_images+=("$image")
        fi
    done

    if [[ ${#failed_images[@]} -gt 0 ]]; then
        log_error "Failed to pull or save ${#failed_images[@]} image(s) for $arch:"
        for failed_image in "${failed_images[@]}"; do
            log_error "  $failed_image"
        done
        if [[ "$ALLOW_MISSING_IMAGES" = true ]]; then
            log_warn "Continuing because --allow-missing-images was set; bundle may not install offline"
        else
            log_error "Bundle image set is incomplete. Set --allow-missing-images only for best-effort diagnostics."
            exit 1
        fi
    fi
    
    log_success "Docker images pulled and saved for $arch"
    return 0
}

create_documentation() {
    if [[ "$SKIP_DOCS" = true ]]; then
        log_warn "Skipping documentation copy"
        return 0
    fi

    local docs_src="$REPO_ROOT/docs"
    local docs_dest="$BUILD_DIR/docs"
    if [[ -d "$docs_src" ]]; then
        cp -r "$docs_src" "$docs_dest"
        log_success "Included docs/ in tarball"
    else
        log_warn "docs/ not found; documentation will not be included"
    fi
    return 0
}

copy_skopeo_tool() {
    if [[ "$INCLUDE_SKOPEO" != true ]]; then
        return 0
    fi

    local src="$SKOPEO_BINARY"
    if [[ -z "$src" ]]; then
        src=$(command -v skopeo || true)
    fi

    if [[ -z "$src" || ! -x "$src" ]]; then
        log_error "--include-skopeo requested but no executable Skopeo binary was found"
        log_error "Install skopeo on the build host or pass --skopeo-binary /path/to/skopeo"
        exit 1
    fi

    local tools_dir="$BUILD_DIR/tools/skopeo"
    mkdir -p "$tools_dir"
    cp "$src" "$tools_dir/skopeo"
    chmod +x "$tools_dir/skopeo"

    if [[ -f "$REPO_ROOT/LICENSE" ]]; then
        cp "$REPO_ROOT/LICENSE" "$tools_dir/LICENSE.Apache-2.0.txt"
    fi

    cat > "$tools_dir/README.md" << 'SKOPEO_README'
# Bundled Skopeo

This optional Skopeo binary is used by `../upload-to-registry.sh` when no system Skopeo is present. Set `SKOPEO_BIN` to override the binary path.

Skopeo is licensed under Apache License 2.0. If this binary came from an external package distribution, keep the package's third-party notices with the bundle as required by that distribution.
SKOPEO_README

    log_success "Included Skopeo binary: $src"
    log_warn "Verify the bundled Skopeo binary is compatible with the target OS/architecture"
    return 0
}

copy_deployment_files() {
    log_info "Copying deployment files..."

    # Sample/config values are already in helm/ because the chart source is copied there.
    log_info "Sample configuration files included in helm/ directory"

    local manifests_dir="$SCRIPT_DIR/manifests"
    if [[ -d "$manifests_dir" ]]; then
        cp -r "$manifests_dir" "$BUILD_DIR/"
        chmod +x "$BUILD_DIR/manifests/"*.sh 2>/dev/null || true
        log_success "Copied manifests/"
    fi

    local jobs_dir="$REPO_ROOT/components/nautobot/nv_config_manager_jobs"
    if [[ -d "$jobs_dir" ]]; then
        mkdir -p "$BUILD_DIR/components/nautobot"
        cp -r "$jobs_dir" "$BUILD_DIR/components/nautobot/"
        log_success "Copied components/nautobot/nv_config_manager_jobs/"
    else
        log_warn "nv_config_manager_jobs not found at $jobs_dir"
    fi

    local configs_dir="$DEPLOY_DIR/configs"
    if [[ -d "$configs_dir" ]]; then
        cp -r "$configs_dir" "$BUILD_DIR/"
        log_success "Copied deploy/configs/"
    fi

    if [[ -f "$OPERATOR_VERSIONS_FILE" ]]; then
        cp "$OPERATOR_VERSIONS_FILE" "$BUILD_DIR/"
        log_success "Copied operator-versions.env"
    fi

    local upload_script="$SCRIPT_DIR/upload-to-registry.sh"
    if [[ -f "$upload_script" ]]; then
        cp "$upload_script" "$BUILD_DIR/upload-to-registry.sh"
        chmod +x "$BUILD_DIR/upload-to-registry.sh"
        log_success "Copied upload-to-registry.sh"
    else
        log_error "Registry upload helper not found: $upload_script"
        exit 1
    fi

    copy_skopeo_tool
    package_installer
    return 0
}


pip_download() {
    if command -v pip &>/dev/null; then
        pip download "$@"
    else
        uv run --with pip python -m pip download "$@"
    fi
    return $?
}

package_installer() {
    log_info "Packaging nv-config-manager-installer wizard..."

    local installer_src="$REPO_ROOT/installer"
    if [[ ! -f "$installer_src/pyproject.toml" ]]; then
        log_warn "Installer package not found at $installer_src, skipping"
        return 0
    fi

    if ! command -v uv &>/dev/null; then
        log_warn "uv not available, skipping installer packaging"
        log_warn "Install uv (https://docs.astral.sh/uv/) to include the installer in the bundle"
        return 0
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

    local pbs_tag="20260320"
    local pbs_cpython="3.13.12"
    local pbs_base="https://github.com/astral-sh/python-build-standalone/releases/download"
    local -a pbs_triples=(
        x86_64-unknown-linux-gnu
        aarch64-unknown-linux-gnu
        x86_64-apple-darwin
        aarch64-apple-darwin
    )

    for triple in "${pbs_triples[@]}"; do
        local pbs_file="cpython-${pbs_cpython}+${pbs_tag}-${triple}-install_only_stripped.tar.gz"
        local pbs_url="${pbs_base}/${pbs_tag}/${pbs_file}"
        local pbs_dest="$installer_dest/python/${triple}"
        log_info "Downloading Python ${pbs_cpython} for ${triple}..."
        mkdir -p "$pbs_dest"
        if curl -fsSL "$pbs_url" | tar -xz -C "$pbs_dest"; then
            if [[ -x "$pbs_dest/python/bin/python3" || -x "$pbs_dest/python/bin/python3.13" ]]; then
                log_success "Downloaded Python for ${triple}"
            else
                log_warn "Python binary not found after extracting ${triple}"
            fi
        else
            log_warn "Failed to download Python for ${triple}"
            rm -rf "$pbs_dest"
        fi
    done

    local uv_version
    uv_version=$(uv --version 2>/dev/null | awk '{print $2}' || echo "0.7.0")
    for arch in amd64 arm64; do
        local uv_arch
        case "$arch" in
            amd64) uv_arch="x86_64-unknown-linux-gnu" ;;
            arm64) uv_arch="aarch64-unknown-linux-gnu" ;;
            *) log_warn "Unsupported arch: $arch"; continue ;;
        esac
        local uv_url="https://github.com/astral-sh/uv/releases/download/${uv_version}/uv-${uv_arch}.tar.gz"
        local uv_dest="$installer_dest/uv-${arch}"
        log_info "Downloading uv binary for $arch..."
        if curl -fsSL "$uv_url" | tar -xz -C "$installer_dest" 2>/dev/null; then
            if mv "$installer_dest/uv-${uv_arch}/uv" "$uv_dest" && chmod +x "$uv_dest" && [[ -x "$uv_dest" ]]; then
                rm -rf "$installer_dest/uv-${uv_arch}"
                log_success "Downloaded uv for $arch"
            else
                log_warn "Failed to place uv binary for $arch"
                rm -rf "$installer_dest/uv-${uv_arch}" "$uv_dest" 2>/dev/null || true
            fi
        else
            log_warn "Failed to download uv for $arch (optional)"
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
echo "  $SCRIPT_DIR/nvcm-installer deploy install.yaml --chart-dir ../helm --image-source registry"
echo ""
echo "Or add to PATH:"
echo "  export PATH=\"$VENV_DIR/bin:\$PATH\""
INSTALL_EOF
    chmod +x "$dest/install.sh"
    log_success "Created installer/install.sh"
    return 0
}

create_manifest() {
    local arch="${1:-amd64}"

    log_info "Creating manifest for $arch..."

    local chart_version=$(grep '^version:' "$CHART_DIR/Chart.yaml" | awk '{print $2}' | tr -d '"' | tr -d "'")
    local app_version
    app_version=$(grep '^appVersion:' "$CHART_DIR/Chart.yaml" | awk '{print $2}' | tr -d '"' | tr -d "'" || true)
    app_version="${app_version:-$chart_version}"

    cat > "$BUILD_DIR/manifest-$arch.json" << EOF
{
    "bundleType": "airgapped-install-bundle",
    "version": "$VERSION",
    "chartVersion": "$chart_version",
    "appVersion": "$app_version",
    "architecture": "$arch",
    "created": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
    "source": "nv-config-manager air-gapped bundle",
    "chart": "helm/nv-config-manager-${chart_version}.tgz",
    "uploadHelper": "upload-to-registry.sh",
    "images": $(cat "$BUILD_DIR/images-$arch/image-list.txt" 2>/dev/null | jq -R . | jq -s . || echo '[]')
}
EOF

    log_success "Manifest created for $arch"
    return 0
}

create_tarball() {
    local arch="${1:-amd64}"

    log_info "Creating final tarball for $arch..."

    local tarball_name="nv-config-manager-airgapped-${VERSION}-${arch}.tar.gz"
    local abs_output_dir=$(cd "$OUTPUT_DIR" && pwd)
    local tarball_path="$abs_output_dir/$tarball_name"
    local root_folder="nv-config-manager-airgapped-${VERSION}-${arch}"
    local tarball_build_dir=$(mktemp -d "${TMPDIR}/nv-config-manager-tarball-${arch}-XXXXXX")
    local content_dir="$tarball_build_dir/$root_folder"
    mkdir -p "$content_dir"

    [[ -d "$BUILD_DIR/helm" ]] && cp -r "$BUILD_DIR/helm" "$content_dir/"
    [[ -d "$BUILD_DIR/docs" ]] && cp -r "$BUILD_DIR/docs" "$content_dir/"
    [[ -d "$BUILD_DIR/manifests" ]] && cp -r "$BUILD_DIR/manifests" "$content_dir/"
    [[ -d "$BUILD_DIR/charts" ]] && cp -r "$BUILD_DIR/charts" "$content_dir/"
    [[ -d "$BUILD_DIR/components" ]] && cp -r "$BUILD_DIR/components" "$content_dir/"
    [[ -d "$BUILD_DIR/configs" ]] && cp -r "$BUILD_DIR/configs" "$content_dir/"
    [[ -d "$BUILD_DIR/installer" ]] && cp -r "$BUILD_DIR/installer" "$content_dir/"
    [[ -d "$BUILD_DIR/tools" ]] && cp -r "$BUILD_DIR/tools" "$content_dir/"
    [[ -f "$BUILD_DIR/operator-versions.env" ]] && cp "$BUILD_DIR/operator-versions.env" "$content_dir/"
    [[ -f "$BUILD_DIR/upload-to-registry.sh" ]] && cp "$BUILD_DIR/upload-to-registry.sh" "$content_dir/"
    [[ -f "$content_dir/upload-to-registry.sh" ]] && chmod +x "$content_dir/upload-to-registry.sh"

    [[ -d "$content_dir/helm/charts" ]] && rmdir "$content_dir/helm/charts" 2>/dev/null || true

    if [[ -d "$BUILD_DIR/images-$arch" ]]; then
        cp -r "$BUILD_DIR/images-$arch" "$content_dir/images"
    else
        mkdir -p "$content_dir/images"
        : > "$content_dir/images/image-list.txt"
    fi

    if [[ -f "$BUILD_DIR/manifest-$arch.json" ]]; then
        cp "$BUILD_DIR/manifest-$arch.json" "$content_dir/manifest.json"
    fi

    cat > "$content_dir/README.md" << 'BUNDLE_README'
# NVIDIA Config Manager Air-Gapped Bundle

This bundle contains the Helm chart, dependency charts, dependency manifests, offline installer, optional docs, image archives, and a registry upload helper.

## Upload Images And Chart To An OCI Registry

    ./upload-to-registry.sh \
      --registry registry.example.com/nv-config-manager \
      --chart-registry registry.example.com/nv-config-manager/charts \
      --username '<user>' \
      --password-stdin

The helper uploads images from images/image-list.txt and the packaged Helm chart from helm/. It writes image-map.tsv with source and target image references. If tools/skopeo/skopeo is present, the helper uses it automatically; otherwise it uses system Skopeo or Docker.

## Install From Bundle

    ./installer/install.sh
    ./installer/nvcm-installer init --config install.yaml
    ./installer/nvcm-installer deploy install.yaml --chart-dir helm --image-source registry

Configure install.yaml image settings to point at the registry image paths written in image-map.tsv. If the target environment preloads node runtimes instead of using a registry, use manifests/load-airgapped-images.sh before deploying.
BUNDLE_README

    pushd "$tarball_build_dir" &> /dev/null
    tar -czf "$tarball_path" "$root_folder"
    popd &> /dev/null

    rm -rf "$tarball_build_dir"

    local tarball_size=$(du -h "$tarball_path" | cut -f1)
    log_success "Tarball created: $tarball_path ($tarball_size)"

    if command -v shasum &> /dev/null; then
        pushd "$OUTPUT_DIR" &> /dev/null
        shasum -a 256 "$tarball_name" > "$tarball_name.sha256"
        popd &> /dev/null
        log_success "Checksum created: $tarball_path.sha256"
    fi
    return 0
}

print_summary() {
    local abs_output_dir=$(cd "$OUTPUT_DIR" && pwd)

    echo ""
    echo "================================================"
    echo "  Airgapped Package(s) Created Successfully!"
    echo "================================================"
    echo ""
    echo "  Version: $VERSION"
    echo ""

    if [[ "$TARGET_ARCH" = "both" || "$TARGET_ARCH" = "amd64" ]]; then
        echo "  AMD64 Package: $abs_output_dir/nv-config-manager-airgapped-${VERSION}-amd64.tar.gz"
    fi
    if [[ "$TARGET_ARCH" = "both" || "$TARGET_ARCH" = "arm64" ]]; then
        echo "  ARM64 Package: $abs_output_dir/nv-config-manager-airgapped-${VERSION}-arm64.tar.gz"
    fi
    echo ""
    echo "To use this package:"
    echo "  1. Transfer the appropriate tarball to your target environment"
    echo "  2. Extract: tar -xzf nv-config-manager-airgapped-${VERSION}-<arch>.tar.gz"
    echo "  3. cd nv-config-manager-airgapped-${VERSION}-<arch>/"
    echo "  4. Upload images and chart: ./upload-to-registry.sh --registry registry.example.com/nv-config-manager --chart-registry registry.example.com/nv-config-manager/charts --username '<user>' --password-stdin"
    echo "  5. Install CLI: ./installer/install.sh"
    echo "  6. Configure/deploy with ./installer/nvcm-installer"
    echo ""
    return 0
}

# Main execution
main() {
    echo "================================================"
    echo "  NVIDIA Config Manager Airgapped Tarball Creator"
    echo "================================================"
    echo ""
    
    check_prerequisites
    setup_build_dir
    get_version
    
    echo ""
    echo "Configuration:"
    echo "  Chart Source: $CHART_DIR"
    echo "  Version: $VERSION"
    echo "  Architecture(s): $TARGET_ARCH"
    echo "  Output: $OUTPUT_DIR"
    echo "  Build: $BUILD_DIR"
    echo "  Include Skopeo: $INCLUDE_SKOPEO"
    echo "  Local Image Fallback: $LOCAL_IMAGE_FALLBACK"
    echo "  Allow Missing Images: $ALLOW_MISSING_IMAGES"
    echo ""
    
    # Package Helm chart and dependency artifacts.
    package_helm_chart
    package_external_charts
    package_dependency_manifests

    # Copy local docs, manifests, configs, upload helper, and offline installer.
    create_documentation
    copy_deployment_files
    
    # Build architectures
    local archs=()
    if [[ "$TARGET_ARCH" = "both" ]]; then
        archs=("amd64" "arm64")
    else
        archs=("$TARGET_ARCH")
    fi
    
    for arch in "${archs[@]}"; do
        echo ""
        echo "------------------------------------------------"
        echo "  Processing $arch architecture"
        echo "------------------------------------------------"
        pull_docker_images "$arch"
        create_manifest "$arch"
        create_tarball "$arch"
    done
    
    print_summary
    
    log_success "All done!"
    return 0
}

# Run main function
main
