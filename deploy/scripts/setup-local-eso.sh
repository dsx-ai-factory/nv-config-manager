#!/bin/bash
# =============================================================================
# Setup Local ESO + OpenBao for Testing
# =============================================================================
# This script sets up a complete local ESO testing environment with OpenBao.
#
# What it does:
#   1. Verifies OpenBao and ESO are installed
#   2. Creates the nv-config-manager-dev namespace
#   3. Enables KV engines in OpenBao
#   4. Generates test secrets in OpenBao
#   5. Optionally deploys the chart with ESO enabled
#
# Usage:
#   ./scripts/setup-local-eso.sh           # Setup only
#   ./scripts/setup-local-eso.sh --deploy  # Setup + deploy chart
#
# Prerequisites:
#   - kubectl configured for local cluster
#   - OpenBao Helm chart installed (see below)
#   - ESO Helm chart installed (see below)
#
# Quick install (if not already done):
#   helm repo add openbao https://openbao.github.io/openbao-helm
#   helm repo add external-secrets https://charts.external-secrets.io
#   helm install openbao openbao/openbao -n openbao --create-namespace \
#     --set server.dev.enabled=true --set server.dev.devRootToken=root \
#     --set injector.enabled=false
#   helm install external-secrets external-secrets/external-secrets \
#     -n external-secrets --create-namespace --set installCRDs=true
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info() {
    echo -e "${BLUE}[INFO]${NC} $*"
    return 0
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $*"
    return 0
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $*"
    return 0
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $*" >&2
    return 0
}

log_step() {
    echo -e "\n${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${CYAN}Step: $*${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    return 0
}

NAMESPACE="${NAMESPACE:-nv-config-manager-dev}"
OPENBAO_NAMESPACE="openbao"
OPENBAO_POD="openbao-0"
ENVIRONMENT="${ENVIRONMENT:-dev}"

# =============================================================================
# Verification Functions
# =============================================================================

check_openbao() {
    log_step "Checking OpenBao installation"
    
    if ! kubectl -n "$OPENBAO_NAMESPACE" get pod "$OPENBAO_POD" &> /dev/null; then
        log_error "OpenBao not found. Please install it first:"
        echo ""
        echo "  helm repo add openbao https://openbao.github.io/openbao-helm"
        echo "  helm install openbao openbao/openbao -n openbao --create-namespace \\"
        echo "    --set server.dev.enabled=true \\"
        echo "    --set server.dev.devRootToken=root \\"
        echo "    --set injector.enabled=false"
        echo ""
        exit 1
    fi
    
    # Wait for pod to be ready
    if ! kubectl -n "$OPENBAO_NAMESPACE" wait --for=condition=ready pod "$OPENBAO_POD" --timeout=60s 2>/dev/null; then
        log_error "OpenBao pod is not ready"
        exit 1
    fi
    
    log_success "OpenBao is running"
    return 0
}

check_eso() {
    log_step "Checking External Secrets Operator installation"
    
    if ! kubectl get crd externalsecrets.external-secrets.io &> /dev/null; then
        log_error "ESO CRDs not found. Please install ESO first:"
        echo ""
        echo "  helm repo add external-secrets https://charts.external-secrets.io"
        echo "  helm install external-secrets external-secrets/external-secrets \\"
        echo "    -n external-secrets --create-namespace --set installCRDs=true"
        echo ""
        exit 1
    fi
    
    # Check if ESO pods are running
    if ! kubectl -n external-secrets get pods -l app.kubernetes.io/name=external-secrets -o jsonpath='{.items[0].status.phase}' 2>/dev/null | grep -q "Running"; then
        log_warn "ESO pods may not be ready yet, but CRDs exist. Continuing..."
    else
        log_success "External Secrets Operator is running"
    fi
    return 0
}

# =============================================================================
# Setup Functions
# =============================================================================

setup_namespace() {
    log_step "Setting up namespace: $NAMESPACE"
    
    kubectl create namespace "$NAMESPACE" 2>/dev/null || log_info "Namespace already exists"
    log_success "Namespace $NAMESPACE ready"
    return 0
}

create_token_secret() {
    log_step "Creating OpenBao token secret in $NAMESPACE"
    
    # Create the token secret for ESO to authenticate with OpenBao
    # Using the default dev mode token "root"
    kubectl create secret generic openbao-token \
        --from-literal=token=root \
        -n "$NAMESPACE" 2>/dev/null || log_info "Token secret already exists"
    
    log_success "Token secret ready"
    return 0
}

enable_kv_engines() {
    log_step "Enabling KV v2 secrets engines in OpenBao"
    
    # Enable nv-config-manager secrets path (main secrets)
    kubectl -n "$OPENBAO_NAMESPACE" exec "$OPENBAO_POD" -- \
        bao secrets enable -path=nv-config-manager kv-v2 2>/dev/null || \
        log_info "nv-config-manager KV engine already enabled"
    
    # Enable secrets path (network credentials)
    kubectl -n "$OPENBAO_NAMESPACE" exec "$OPENBAO_POD" -- \
        bao secrets enable -path=secrets kv-v2 2>/dev/null || \
        log_info "secrets KV engine already enabled"
    
    log_success "KV engines ready"
    return 0
}

generate_test_secrets() {
    log_step "Generating test secrets in OpenBao"
    
    # Simple path structure: {environment}/{secret}
    local env_path="${ENVIRONMENT}"
    
    # Helper function to write secrets
    write_secret() {
        local path="$1"
        shift
        kubectl -n "$OPENBAO_NAMESPACE" exec "$OPENBAO_POD" -- \
            env BAO_TOKEN=root bao kv put "nv-config-manager/${path}" "$@" >/dev/null 2>&1 || return $?
        return 0
    }
    
    # Nautobot
    write_secret "${env_path}/nautobot" \
        token="test-nautobot-token-12345" \
        nats_password="test-nats-password"
    log_info "  ✓ nautobot"
    
    # Redis
    write_secret "${env_path}/redis" \
        password="test-redis-password"
    log_info "  ✓ redis"
    
    # PostgreSQL (CNPG)
    write_secret "${env_path}/postgres" \
        temporal_user="temporal" \
        temporal_password="test-temporal-pass" \
        temporal_visibility_user="temporal_visibility" \
        temporal_visibility_password="test-visibility-pass" \
        config_store_user="config_store" \
        config_store_password="test-configstore-pass" \
        dhcp_user="dhcp" \
        dhcp_password="test-dhcp-pass" \
        nautobot_user="nautobot" \
        nautobot_password="test-nautobot-db-pass"
    log_info "  ✓ cnpg"
    
    # Network
    write_secret "${env_path}/network" \
        user="admin" \
        password="test-network-password"
    log_info "  ✓ network"
    
    # Redfish
    write_secret "${env_path}/redfish" \
        lenovo_default_user="USERID" \
        lenovo_default_password="PASSW0RD" \
        lenovo_config_manager_password="test-lenovo-nv-config-manager" \
        bluefield_default_user="admin" \
        bluefield_default_password="admin" \
        bluefield_config_manager_password="test-bluefield-nv-config-manager"
    log_info "  ✓ redfish"
    
    # BMC
    write_secret "${env_path}/bmc" \
        'bmc-creds.json={"default": {"username": "admin", "password": "admin"}}'
    log_info "  ✓ bmc"
    
    # Slack
    write_secret "${env_path}/slack" \
        token="xoxb-test-slack-token"
    log_info "  ✓ slack"
    
    # DHCP
    write_secret "${env_path}/dhcp" \
        password="test-dhcp-password"
    log_info "  ✓ dhcp"
    
    # UFM
    write_secret "${env_path}/ufm" \
        ufm_api_user="admin" \
        ufm_api_token_r1="test-ufm-token"
    log_info "  ✓ ufm"
    
    # Nautobot App
    write_secret "${env_path}/nautobot-app" \
        admin_password="admin" \
        django_secret_key="test-django-secret-key-that-is-long-enough-for-django" \
        superuser_api_token="0123456789abcdef0123456789abcdef01234567"
    log_info "  ✓ nautobot-app"
    
    log_success "All test secrets created"
    return 0
}

deploy_chart() {
    log_step "Deploying NVIDIA Config Manager chart with ESO enabled"
    
    helm upgrade --install nv-config-manager "$PROJECT_DIR" \
        -f "$PROJECT_DIR/values-local-eso-test.yaml" \
        -n "$NAMESPACE" \
        --create-namespace \
        --wait --timeout 120s
    
    log_success "Chart deployed"
    return 0
}

verify_eso() {
    log_step "Verifying ESO can read secrets"
    
    # Wait for the nv-config-manager-unified-config ExternalSecret to sync
    log_info "Waiting for ExternalSecret to sync..."
    
    local max_attempts=30
    local attempt=0
    
    while [[ $attempt -lt $max_attempts ]]; do
        local status
        status=$(kubectl -n "$NAMESPACE" get externalsecret nv-config-manager-unified-config -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "Unknown")
        
        if [[ "$status" == "True" ]]; then
            log_success "ExternalSecret synced successfully!"
            
            # Show the created secret
            echo ""
            log_info "nv-config-manager-ini secret created. First 30 lines:"
            kubectl -n "$NAMESPACE" get secret nv-config-manager-ini -o jsonpath='{.data.nv-config-manager\.ini}' 2>/dev/null | \
                base64 -d | head -30
            echo "..."
            
            return 0
        fi
        
        local reason
        reason=$(kubectl -n "$NAMESPACE" get externalsecret nv-config-manager-unified-config -o jsonpath='{.status.conditions[?(@.type=="Ready")].message}' 2>/dev/null || echo "")
        
        if [[ -n "$reason" && "$reason" != "null" ]]; then
            log_info "  Status: $reason"
        fi
        
        ((attempt++))
        sleep 2
    done
    
    log_error "ExternalSecret did not sync within timeout"
    log_info "Debug info:"
    kubectl -n "$NAMESPACE" get externalsecret nv-config-manager-unified-config -o yaml
    kubectl -n "$NAMESPACE" describe secretstore vault-secretstore-nv-config-manager
    return 1
}

# =============================================================================
# Main
# =============================================================================

main() {
    local do_deploy=false
    local argument
    
    # Parse arguments
    for argument in "$@"; do
        case "$argument" in
            --deploy|-d)
                do_deploy=true
                ;;
            *)
                log_error "Unknown argument: $argument"
                return 2
                ;;
        esac
    done
    
    echo ""
    echo -e "${GREEN}╔═══════════════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║           Local ESO + OpenBao Setup for NVIDIA Config Manager                    ║${NC}"
    echo -e "${GREEN}╚═══════════════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    
    check_openbao
    check_eso
    setup_namespace
    create_token_secret
    enable_kv_engines
    generate_test_secrets
    
    if [[ "$do_deploy" == "true" ]]; then
        deploy_chart
        verify_eso
    fi
    
    echo ""
    echo -e "${GREEN}╔═══════════════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║                         Setup Complete!                                   ║${NC}"
    echo -e "${GREEN}╚═══════════════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    
    if [[ "$do_deploy" == "true" ]]; then
        log_info "Chart deployed and ESO verified!"
        echo ""
        log_info "Check ExternalSecrets status:"
        echo "  kubectl -n $NAMESPACE get externalsecrets"
        echo ""
        log_info "View the generated nv-config-manager.ini:"
        echo "  kubectl -n $NAMESPACE get secret nv-config-manager-ini -o jsonpath='{.data.nv-config-manager\\.ini}' | base64 -d"
    else
        log_info "OpenBao secrets are ready. Deploy the chart with:"
        echo ""
        echo "  helm upgrade --install nv-config-manager . -f values-local-eso-test.yaml -n $NAMESPACE --create-namespace"
        echo ""
        log_info "Or run with --deploy to deploy automatically:"
        echo "  ./scripts/setup-local-eso.sh --deploy"
    fi
    return 0
}

main "$@"
