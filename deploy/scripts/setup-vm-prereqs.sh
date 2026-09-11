#!/bin/bash
# =============================================================================
# NVIDIA Config Manager VM Setup Script
# =============================================================================
# This script sets up a complete NVIDIA Config Manager development/demo environment
# on a remote VM, including:
#   - System prerequisites (Docker, kubectl, Helm, Kind)
#   - NFS server for shared storage
#   - Kind cluster with multi-node configuration
#   - NFS CSI driver and storage class
#   - MetalLB for LoadBalancer services
#   - Node labels for workload distribution
#
# Optional Security Stack (install before air-gap phase):
#   - SPIRE: SPIFFE identity provider for mTLS/JWT-SVIDs
#   - KeyCloak: Local OIDC/SSO provider (replaces Azure AD for demos)
#   - OpenBao: Secrets management (HashiCorp Vault fork)
#   - ESO: External Secrets Operator (syncs secrets from OpenBao)
#
# Usage:
#   ./setup-vm-prereqs.sh [OPTIONS]
#
# Options:
#   --kind-config PATH    Path to Kind config file (default: creates inline config)
#   --cluster-name NAME   Kind cluster name (default: nv-config-manager)
#   --skip-prereqs        Skip prerequisite installation (for re-runs)
#   --skip-cluster        Skip cluster creation (prereqs only)
#   --airgapped           Apply CoreDNS fix for air-gapped environments
#
# Security Stack Options (requires network access, run before air-gap):
#   --install-spire       Install SPIRE for SPIFFE workload identity
#   --install-keycloak    Install KeyCloak for local SSO/OIDC
#   --install-openbao     Install OpenBao for secrets management
#   --install-eso         Install External Secrets Operator
#   --install-security-stack  Install all security components (SPIRE, KeyCloak, OpenBao, ESO)
#   --keycloak-hostname HOST  KeyCloak hostname (default: keycloak.config-manager.demo)
#   --spiffe-trust-domain TD  SPIFFE trust domain (default: config-manager.demo)
#   --sites SITE          Create config secrets for site (can be specified multiple times)
#   --eso-config-output PATH  Output path for generated ESO config (default: ~/eso-config.yaml)
#   --environment-path PATH   Environment path prefix in Vault secrets (default: demo)
#
#   --help                Show this help message
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
OPERATOR_VERSIONS_FILE="$DEPLOY_DIR/operator-versions.env"

# Defaults
CLUSTER_NAME="nv-config-manager"
KIND_CONFIG=""
SKIP_PREREQS=false
SKIP_CLUSTER=false
AIRGAPPED=false

# Security Stack Defaults
INSTALL_SPIRE=false
INSTALL_KEYCLOAK=false
INSTALL_OPENBAO=false
INSTALL_ESO=false
INSTALL_ENVOY_GATEWAY=false
INSTALL_CERT_MANAGER=false
KEYCLOAK_HOSTNAME="keycloak.config-manager.demo"
BASE_HOSTNAME="config-manager.demo"
SPIFFE_TRUST_DOMAIN="config-manager.demo"
KEYCLOAK_ADMIN_PASSWORD=""  # Auto-generated if not set
CONFIG_SECRETS_SITES=()  # Sites to create config secrets for
ESO_CONFIG_OUTPUT=""  # Path to output generated ESO config (default: ~/eso-config.yaml)
ENVIRONMENT_PATH="demo"  # Environment path prefix in Vault (default: demo)

# Component versions. Prefer the shared manifest; keep defaults for standalone copies.
GATEWAY_API_VERSION="v1.4.1"
ENVOY_GATEWAY_VERSION="v1.6.5"
CERT_MANAGER_VERSION="v1.20.2"
OPENBAO_CHART_VERSION="0.29.3"
if [[ -f "$OPERATOR_VERSIONS_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$OPERATOR_VERSIONS_FILE"
fi

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --kind-config)
            KIND_CONFIG="$2"
            shift 2
            ;;
        --cluster-name)
            CLUSTER_NAME="$2"
            shift 2
            ;;
        --skip-prereqs)
            SKIP_PREREQS=true
            shift
            ;;
        --skip-cluster)
            SKIP_CLUSTER=true
            shift
            ;;
        --airgapped)
            AIRGAPPED=true
            shift
            ;;
        --install-spire)
            INSTALL_SPIRE=true
            shift
            ;;
        --install-keycloak)
            INSTALL_KEYCLOAK=true
            shift
            ;;
        --install-openbao)
            INSTALL_OPENBAO=true
            shift
            ;;
        --install-eso)
            INSTALL_ESO=true
            shift
            ;;
        --install-security-stack)
            INSTALL_SPIRE=true
            INSTALL_KEYCLOAK=true
            INSTALL_OPENBAO=true
            INSTALL_ESO=true
            INSTALL_ENVOY_GATEWAY=true
            INSTALL_CERT_MANAGER=true
            shift
            ;;
        --install-envoy-gateway)
            INSTALL_ENVOY_GATEWAY=true
            INSTALL_CERT_MANAGER=true  # Envoy Gateway needs cert-manager for TLS
            shift
            ;;
        --install-cert-manager)
            INSTALL_CERT_MANAGER=true
            shift
            ;;
        --base-hostname)
            BASE_HOSTNAME="$2"
            shift 2
            ;;
        --keycloak-hostname)
            KEYCLOAK_HOSTNAME="$2"
            shift 2
            ;;
        --spiffe-trust-domain)
            SPIFFE_TRUST_DOMAIN="$2"
            shift 2
            ;;
        --keycloak-admin-password)
            KEYCLOAK_ADMIN_PASSWORD="$2"
            shift 2
            ;;
        --sites)
            CONFIG_SECRETS_SITES+=("$2")
            shift 2
            ;;
        --eso-config-output)
            ESO_CONFIG_OUTPUT="$2"
            shift 2
            ;;
        --environment-path)
            ENVIRONMENT_PATH="$2"
            shift 2
            ;;
        --help|-h)
            head -45 "$0" | tail -40
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

echo "================================================"
echo "  NVIDIA Config Manager VM Setup"
echo "================================================"
echo ""
echo "Configuration:"
echo "  Cluster name: $CLUSTER_NAME"
echo "  Kind config:  ${KIND_CONFIG:-inline}"
echo "  Skip prereqs: $SKIP_PREREQS"
echo "  Skip cluster: $SKIP_CLUSTER"
echo "  Air-gapped:   $AIRGAPPED"
echo ""
if [[ "$INSTALL_SPIRE" == "true" || "$INSTALL_KEYCLOAK" == "true" || "$INSTALL_OPENBAO" == "true" || "$INSTALL_ESO" == "true" ]]; then
    echo "Security Stack:"
    echo "  SPIRE:    $INSTALL_SPIRE"
    echo "  KeyCloak: $INSTALL_KEYCLOAK"
    echo "  OpenBao:  $INSTALL_OPENBAO"
    echo "  ESO:      $INSTALL_ESO"
    [[ "$INSTALL_KEYCLOAK" == "true" ]] && echo "  KeyCloak hostname: $KEYCLOAK_HOSTNAME"
    [[ "$INSTALL_SPIRE" == "true" ]] && echo "  SPIFFE trust domain: $SPIFFE_TRUST_DOMAIN"
    echo ""
fi

# =============================================================================
# PREREQUISITES
# =============================================================================
if [[ "$SKIP_PREREQS" != "true" ]]; then

    # -------------------------------------------------------------------------
    # Step 1: Update System
    # -------------------------------------------------------------------------
    echo "ℹ Step 1: Updating system packages..."
    sudo apt update && sudo apt upgrade -y
    echo "✓ System updated"
    echo ""

    # -------------------------------------------------------------------------
    # Step 2: Install Docker
    # -------------------------------------------------------------------------
    if ! command -v docker &> /dev/null; then
        echo "ℹ Step 2: Installing Docker..."
        sudo apt install -y ca-certificates curl gnupg
        sudo install -m 0755 -d /etc/apt/keyrings
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
        sudo chmod a+r /etc/apt/keyrings/docker.gpg
        echo \
          "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
          $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
          sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
        sudo apt update
        sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
        sudo usermod -aG docker $USER
        echo "✓ Docker installed"
    else
        echo "✓ Docker already installed"
    fi
    echo ""

    # -------------------------------------------------------------------------
    # Step 3: Install kubectl
    # -------------------------------------------------------------------------
    if ! command -v kubectl &> /dev/null; then
        echo "ℹ Step 3: Installing kubectl..."
        curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
        sudo install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl
        rm kubectl
        echo "✓ kubectl installed"
    else
        echo "✓ kubectl already installed"
    fi
    echo ""

    # -------------------------------------------------------------------------
    # Step 4: Install Helm
    # -------------------------------------------------------------------------
    if ! command -v helm &> /dev/null; then
        echo "ℹ Step 4: Installing Helm..."
        curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
        echo "✓ Helm installed"
    else
        echo "✓ Helm already installed"
    fi
    echo ""

    # -------------------------------------------------------------------------
    # Step 5: Install Kind
    # -------------------------------------------------------------------------
    if ! command -v kind &> /dev/null; then
        echo "ℹ Step 5: Installing Kind..."
        curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.25.0/kind-linux-amd64
        chmod +x ./kind
        sudo mv ./kind /usr/local/bin/kind
        echo "✓ Kind installed"
    else
        echo "✓ Kind already installed"
    fi
    echo ""

    # -------------------------------------------------------------------------
    # Step 6: Install Development Tools
    # -------------------------------------------------------------------------
    echo "ℹ Step 6: Installing development tools..."
    sudo apt install -y git jq htop nfs-kernel-server yq
    echo "✓ Development tools installed"
    echo ""

    # -------------------------------------------------------------------------
    # Step 7: Setup NFS Server
    # -------------------------------------------------------------------------
    echo "ℹ Step 7: Setting up NFS server..."
    sudo mkdir -p /srv/nfs/kubedata
    sudo chown nobody:nogroup /srv/nfs/kubedata
    sudo chmod 777 /srv/nfs/kubedata
    if ! grep -q "/srv/nfs/kubedata" /etc/exports 2>/dev/null; then
        echo "/srv/nfs/kubedata *(rw,sync,no_subtree_check,no_root_squash)" | sudo tee -a /etc/exports
    fi
    sudo exportfs -ra
    sudo systemctl enable --now nfs-kernel-server
    echo "✓ NFS server configured at /srv/nfs/kubedata"
    echo ""

    # -------------------------------------------------------------------------
    # Step 8: Increase inotify limits
    # -------------------------------------------------------------------------
    echo "ℹ Step 8: Configuring system limits..."
    if ! grep -q "fs.inotify.max_user_watches" /etc/sysctl.conf 2>/dev/null; then
        cat << 'SYSCTL_EOF' | sudo tee -a /etc/sysctl.conf
# NVIDIA Config Manager - Increase inotify limits
fs.inotify.max_user_watches=1048576
fs.inotify.max_user_instances=8192
fs.file-max=2097152
SYSCTL_EOF
        sudo sysctl -p
    fi
    echo "✓ System limits configured"
    echo ""

fi # end SKIP_PREREQS

# Check if we need to refresh docker group
if ! docker info &>/dev/null; then
    echo "⚠ Docker group not active. Please run: newgrp docker"
    echo "  Then re-run this script with --skip-prereqs"
    exit 1
fi

# =============================================================================
# CLUSTER SETUP
# =============================================================================
if [[ "$SKIP_CLUSTER" != "true" ]]; then

    # -------------------------------------------------------------------------
    # Step 9: Create Kind Cluster
    # -------------------------------------------------------------------------
    echo "ℹ Step 9: Creating Kind cluster '$CLUSTER_NAME'..."

    # Delete existing cluster if present
    if kind get clusters 2>/dev/null | grep -q "^${CLUSTER_NAME}$"; then
        echo "  Deleting existing cluster..."
        kind delete cluster --name "$CLUSTER_NAME"
    fi

    if [[ -n "$KIND_CONFIG" && -f "$KIND_CONFIG" ]]; then
        kind create cluster --name "$CLUSTER_NAME" --config "$KIND_CONFIG" --wait 5m
    else
        # Use inline config for 4-node cluster (1 control-plane + 3 workers)
        # NodePorts 30443/30080 are mapped to 443/80 for Envoy Gateway v1.6+ NodePort mode
        # Port 8443 is mapped directly for Keycloak Gateway (uses hostPort mode)
        cat << 'KIND_EOF' | kind create cluster --name "$CLUSTER_NAME" --config - --wait 5m
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
- role: control-plane
  extraPortMappings:
  # Envoy Gateway HTTPS (NodePort 30443 → host 443)
  - containerPort: 30443
    hostPort: 443
    listenAddress: "0.0.0.0"
    protocol: TCP
  # Envoy Gateway HTTP (NodePort 30080 → host 80)
  - containerPort: 30080
    hostPort: 80
    listenAddress: "0.0.0.0"
    protocol: TCP
  # Keycloak Gateway HTTPS (uses hostPort mode on 8443)
  - containerPort: 8443
    hostPort: 8443
    listenAddress: "0.0.0.0"
    protocol: TCP
  extraMounts:
  - hostPath: /srv/nfs/kubedata
    containerPath: /srv/nfs/kubedata
  kubeadmConfigPatches:
  - |
    kind: InitConfiguration
    nodeRegistration:
      kubeletExtraArgs:
        system-reserved: memory=2Gi
        kube-reserved: memory=2Gi
        eviction-hard: memory.available<1Gi
- role: worker
  extraMounts:
  - hostPath: /srv/nfs/kubedata
    containerPath: /srv/nfs/kubedata
  kubeadmConfigPatches:
  - |
    kind: JoinConfiguration
    nodeRegistration:
      kubeletExtraArgs:
        system-reserved: memory=2Gi
        kube-reserved: memory=2Gi
        eviction-hard: memory.available<1Gi
- role: worker
  extraMounts:
  - hostPath: /srv/nfs/kubedata
    containerPath: /srv/nfs/kubedata
  kubeadmConfigPatches:
  - |
    kind: JoinConfiguration
    nodeRegistration:
      kubeletExtraArgs:
        system-reserved: memory=2Gi
        kube-reserved: memory=2Gi
        eviction-hard: memory.available<1Gi
- role: worker
  extraMounts:
  - hostPath: /srv/nfs/kubedata
    containerPath: /srv/nfs/kubedata
  kubeadmConfigPatches:
  - |
    kind: JoinConfiguration
    nodeRegistration:
      kubeletExtraArgs:
        system-reserved: memory=2Gi
        kube-reserved: memory=2Gi
        eviction-hard: memory.available<1Gi
KIND_EOF
    fi
    echo "✓ Kind cluster created"
    echo ""

    # -------------------------------------------------------------------------
    # Step 9b: Fix DNS search domains on Kind nodes
    # -------------------------------------------------------------------------
    # Corporate environments often have complex /etc/resolv.conf that Docker
    # mangles when passing to Kind nodes, resulting in corrupted search domains
    # (e.g., nvidia.com032awsad.nvidia.com... where 032 is a corrupted space).
    # This fixes the node's /etc/resolv.conf to have clean search domains.
    echo "ℹ Step 9b: Fixing DNS configuration on Kind nodes..."
    
    # Get all Kind nodes for this cluster
    KIND_NODES=$(kind get nodes --name "$CLUSTER_NAME" 2>/dev/null || echo "")
    
    if [[ -n "$KIND_NODES" ]]; then
        for node in $KIND_NODES; do
            echo "  Fixing DNS on node: $node"
            # Fix the node's /etc/resolv.conf with clean search domains
            # Kubelet reads this at startup and passes search domains to pods
            docker exec "$node" sh -c 'cat > /etc/resolv.conf << EOF
# Fixed by setup-vm-prereqs.sh to remove corrupted search domains
nameserver 172.18.0.1
search cluster.local
options ndots:5
EOF'
        done
        
        # Restart kubelet on all nodes to pick up the fixed resolv.conf
        echo "  Restarting kubelet on all nodes..."
        for node in $KIND_NODES; do
            docker exec "$node" systemctl restart kubelet 2>/dev/null || \
            docker exec "$node" sh -c 'kill -HUP $(pidof kubelet)' 2>/dev/null || true
        done
        
        # Wait for nodes to be ready again
        echo "  Waiting for nodes to be ready..."
        sleep 5
        kubectl wait --for=condition=Ready nodes --all --timeout=120s
        echo "✓ DNS configuration fixed on all nodes"
    else
        echo "  Warning: Could not get Kind nodes, skipping DNS fix"
    fi
    echo ""

    # -------------------------------------------------------------------------
    # Step 10: Fix CoreDNS for Air-Gapped (optional)
    # -------------------------------------------------------------------------
    if [[ "$AIRGAPPED" == "true" ]]; then
        echo "ℹ Step 10: Fixing CoreDNS for air-gapped environment..."
        kubectl apply -f - << 'COREDNS_EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: coredns
  namespace: kube-system
data:
  Corefile: |
    .:53 {
        errors
        health {
           lameduck 5s
        }
        ready
        kubernetes cluster.local in-addr.arpa ip6.arpa {
           pods insecure
           fallthrough in-addr.arpa ip6.arpa
           ttl 30
        }
        prometheus :9153
        cache 30
        loop
        reload
        loadbalance
    }
COREDNS_EOF
        kubectl rollout restart -n kube-system deployment/coredns
        kubectl wait --for=condition=ready pod -n kube-system -l k8s-app=kube-dns --timeout=60s
        echo "✓ CoreDNS configured for air-gapped"
        echo ""
    fi

    # -------------------------------------------------------------------------
    # Step 11: Install NFS CSI Driver
    # -------------------------------------------------------------------------
    echo "ℹ Step 11: Installing NFS CSI driver..."
    helm repo add csi-driver-nfs https://raw.githubusercontent.com/kubernetes-csi/csi-driver-nfs/master/charts 2>/dev/null || true
    helm repo update
    helm upgrade --install csi-driver-nfs csi-driver-nfs/csi-driver-nfs \
        --namespace kube-system \
        --set driver.mountPermissions=0777 \
        --wait
    echo "✓ NFS CSI driver installed"
    echo ""

    # -------------------------------------------------------------------------
    # Step 12: Create NFS Storage Class
    # -------------------------------------------------------------------------
    echo "ℹ Step 12: Creating NFS storage class..."
    NFS_SERVER=$(docker network inspect kind | jq -r '.[0].IPAM.Config[] | select(.Subnet | test("^[0-9]")) | .Gateway')
    
    kubectl apply -f - << STORAGE_EOF
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: nfs-csi
  annotations:
    storageclass.kubernetes.io/is-default-class: "true"
provisioner: nfs.csi.k8s.io
parameters:
  server: "${NFS_SERVER}"
  share: /srv/nfs/kubedata
reclaimPolicy: Delete
volumeBindingMode: Immediate
mountOptions:
  - nfsvers=4.1
STORAGE_EOF

    # Remove default from standard class
    kubectl patch storageclass standard -p '{"metadata": {"annotations":{"storageclass.kubernetes.io/is-default-class":"false"}}}' 2>/dev/null || true
    echo "✓ NFS storage class created (default)"
    echo ""

    # -------------------------------------------------------------------------
    # Step 13: Install MetalLB
    # -------------------------------------------------------------------------
    echo "ℹ Step 13: Installing MetalLB..."
    helm repo add metallb https://metallb.github.io/metallb 2>/dev/null || true
    helm repo update
    helm upgrade --install metallb metallb/metallb \
        --namespace metallb-system \
        --create-namespace \
        --wait
    echo "✓ MetalLB installed"
    echo ""

    # -------------------------------------------------------------------------
    # Step 14: Configure MetalLB IP Pool
    # -------------------------------------------------------------------------
    echo "ℹ Step 14: Configuring MetalLB IP pool..."
    KIND_NET_CIDR=$(docker network inspect kind | jq -r '.[0].IPAM.Config[] | select(.Subnet | test("^[0-9]")) | .Subnet')
    METALLB_RANGE=$(echo $KIND_NET_CIDR | sed -E 's|([0-9]+\.[0-9]+)\..*|\1.255.200-\1.255.250|')

    # Wait for MetalLB webhook to be ready
    kubectl wait --for=condition=ready pod -n metallb-system -l app.kubernetes.io/component=controller --timeout=120s

    kubectl apply -f - << METALLB_EOF
apiVersion: metallb.io/v1beta1
kind: IPAddressPool
metadata:
  name: kind-pool
  namespace: metallb-system
spec:
  addresses:
  - ${METALLB_RANGE}
---
apiVersion: metallb.io/v1beta1
kind: L2Advertisement
metadata:
  name: kind-l2
  namespace: metallb-system
spec:
  ipAddressPools:
  - kind-pool
METALLB_EOF
    echo "✓ MetalLB configured with range: $METALLB_RANGE"
    echo ""

    # -------------------------------------------------------------------------
    # Step 15: Remove Control-Plane Taint (allow scheduling on all nodes)
    # -------------------------------------------------------------------------
    echo "ℹ Step 15: Configuring node scheduling..."
    
    # Get control-plane node name
    CONTROL_PLANE=$(kubectl get nodes -l node-role.kubernetes.io/control-plane -o jsonpath='{.items[0].metadata.name}')

    # Remove control-plane taint to allow scheduling on all nodes
    kubectl taint nodes "$CONTROL_PLANE" node-role.kubernetes.io/control-plane:NoSchedule- 2>/dev/null || true

    echo "✓ Control-plane taint removed - all nodes available for scheduling"
    echo ""
    
    # Note: Node labels (nv-config-manager.nvidia.com/node-type) are optional.
    # Uncomment nodeSelector in values.yaml if you want node placement control.
    # Example labels for production:
    #   kubectl label node <node> nv-config-manager.nvidia.com/node-type=control-plane
    #   kubectl label node <node> nv-config-manager.nvidia.com/node-type=worker
    #   kubectl label node <node> nv-config-manager.nvidia.com/node-type=database

fi # end SKIP_CLUSTER

# =============================================================================
# INFRASTRUCTURE COMPONENTS
# =============================================================================
# These components should be installed BEFORE entering air-gap mode.
# They require network access to pull Helm charts and container images.
# =============================================================================

# -----------------------------------------------------------------------------
# cert-manager Installation
# -----------------------------------------------------------------------------
if [[ "$INSTALL_CERT_MANAGER" == "true" ]]; then
    echo "ℹ Installing cert-manager..."
    
    # Add Jetstack Helm repo
    helm repo add jetstack https://charts.jetstack.io 2>/dev/null || true
    helm repo update
    
    # Check if already installed
    if helm status cert-manager -n cert-manager >/dev/null 2>&1; then
        echo "✓ cert-manager is already installed"
    else
        echo "  Installing cert-manager ${CERT_MANAGER_VERSION}..."
        
        # Install CRDs first
        kubectl apply -f "https://github.com/cert-manager/cert-manager/releases/download/${CERT_MANAGER_VERSION}/cert-manager.crds.yaml"
        
        # Install cert-manager
        helm upgrade --install cert-manager jetstack/cert-manager \
            --namespace cert-manager \
            --create-namespace \
            --version "${CERT_MANAGER_VERSION}" \
            --wait --timeout 5m
        
        echo "✓ cert-manager installed"
    fi
    
    # Wait for cert-manager to be ready
    echo "  Waiting for cert-manager components..."
    kubectl wait --timeout=2m -n cert-manager deployment/cert-manager --for=condition=Available
    kubectl wait --timeout=2m -n cert-manager deployment/cert-manager-webhook --for=condition=Available
    kubectl wait --timeout=2m -n cert-manager deployment/cert-manager-cainjector --for=condition=Available
    
    # Create a self-signed ClusterIssuer for the cluster
    echo "  Creating self-signed ClusterIssuer..."
    kubectl apply -f - << ISSUER_EOF
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: selfsigned-issuer
spec:
  selfSigned: {}
---
# CA Certificate for issuing other certs
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: selfsigned-ca
  namespace: cert-manager
spec:
  isCA: true
  commonName: "NVIDIA Config Manager CA"
  secretName: selfsigned-ca-secret
  duration: 8760h  # 1 year
  renewBefore: 720h  # 30 days
  privateKey:
    algorithm: RSA
    size: 2048
  issuerRef:
    name: selfsigned-issuer
    kind: ClusterIssuer
---
# CA Issuer that uses the self-signed CA
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: nv-config-manager-ca-issuer
spec:
  ca:
    secretName: selfsigned-ca-secret
ISSUER_EOF
    
    # Wait for CA certificate to be ready
    echo "  Waiting for CA certificate..."
    kubectl wait --timeout=60s -n cert-manager certificate/selfsigned-ca --for=condition=Ready 2>/dev/null || true
    
    echo "✓ cert-manager ready with self-signed CA issuer"
    echo "  ClusterIssuer: nv-config-manager-ca-issuer (use for TLS certificates)"
    echo ""
fi

# -----------------------------------------------------------------------------
# Envoy Gateway Installation
# -----------------------------------------------------------------------------
if [[ "$INSTALL_ENVOY_GATEWAY" == "true" ]]; then
    echo "ℹ Installing Envoy Gateway..."
    
    # Install Gateway API CRDs
    echo "  Installing Gateway API CRDs (${GATEWAY_API_VERSION})..."
    kubectl apply --server-side --force-conflicts \
        -f "https://github.com/kubernetes-sigs/gateway-api/releases/download/${GATEWAY_API_VERSION}/standard-install.yaml"
    echo "✓ Gateway API CRDs installed"
    
    # Install Envoy Gateway via Helm
    # v1.5.0+ required for jwt.optional feature (combine JWT + OIDC auth)
    echo "  Installing Envoy Gateway (${ENVOY_GATEWAY_VERSION}) via Helm..."
    
    # Check if already installed
    if helm status eg -n envoy-gateway-system &>/dev/null; then
        echo "  Upgrading existing Envoy Gateway installation..."
        helm upgrade eg oci://docker.io/envoyproxy/gateway-helm \
            --version "${ENVOY_GATEWAY_VERSION}" \
            -n envoy-gateway-system \
            --wait --timeout 5m
    else
        echo "  Installing Envoy Gateway..."
        helm install eg oci://docker.io/envoyproxy/gateway-helm \
            --version "${ENVOY_GATEWAY_VERSION}" \
            -n envoy-gateway-system \
            --create-namespace \
            --wait --timeout 5m
    fi
    
    echo "✓ Envoy Gateway is ready"
    echo ""
    
    # Create a shared GatewayClass and Gateway for the cluster
    echo "  Creating shared Gateway for ${BASE_HOSTNAME}..."
    kubectl apply -f - << GATEWAY_EOF
apiVersion: gateway.networking.k8s.io/v1
kind: GatewayClass
metadata:
  name: envoy-gateway
spec:
  controllerName: gateway.envoyproxy.io/gatewayclass-controller
---
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: shared-gateway
  namespace: envoy-gateway-system
spec:
  gatewayClassName: envoy-gateway
  listeners:
    - name: http
      protocol: HTTP
      port: 80
      allowedRoutes:
        namespaces:
          from: All
    - name: https
      protocol: HTTPS
      port: 443
      tls:
        mode: Terminate
        certificateRefs:
          - kind: Secret
            name: shared-gateway-tls
      allowedRoutes:
        namespaces:
          from: All
GATEWAY_EOF
    
    # Create TLS certificate for the gateway using cert-manager
    echo "  Creating TLS certificate via cert-manager..."
    
    kubectl apply -f - << CERT_EOF
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: shared-gateway-tls
  namespace: envoy-gateway-system
spec:
  secretName: shared-gateway-tls
  duration: 8760h  # 1 year
  renewBefore: 720h  # 30 days
  commonName: "*.${BASE_HOSTNAME}"
  dnsNames:
    - "${BASE_HOSTNAME}"
    - "*.${BASE_HOSTNAME}"
  issuerRef:
    name: nv-config-manager-ca-issuer
    kind: ClusterIssuer
CERT_EOF
    
    # Wait for certificate to be ready
    echo "  Waiting for certificate to be issued..."
    kubectl wait --timeout=60s -n envoy-gateway-system certificate/shared-gateway-tls --for=condition=Ready 2>/dev/null || true
    
    echo "✓ Shared Gateway created"
    echo "  Gateway: shared-gateway (envoy-gateway-system)"
    echo "  Listeners: HTTP (80), HTTPS (443)"
    echo ""
fi

# =============================================================================
# SECURITY STACK INSTALLATION
# =============================================================================

# -----------------------------------------------------------------------------
# SPIRE Installation (SPIFFE Identity Provider)
# -----------------------------------------------------------------------------
if [[ "$INSTALL_SPIRE" == "true" ]]; then
    echo "ℹ Installing SPIRE for SPIFFE workload identity..."
    
    # Add SPIFFE Helm repo
    helm repo add spiffe https://spiffe.github.io/helm-charts-hardened 2>/dev/null || true
    helm repo update
    
    # Step 1: Install SPIRE CRDs first (required before controller-manager can create ClusterSPIFFEID resources)
    echo "  Installing SPIRE CRDs..."
    helm upgrade --install spire-crds spiffe/spire-crds \
        --namespace spire-system \
        --create-namespace \
        --wait --timeout 2m
    
    echo "✓ SPIRE CRDs installed"
    
    # Step 2: Install SPIRE with NVIDIA Config Manager-compatible configuration
    echo "  Installing SPIRE components..."
    helm upgrade --install spire spiffe/spire \
        --namespace spire-system \
        --set global.spire.trustDomain="$SPIFFE_TRUST_DOMAIN" \
        --set global.spire.clusterName="$CLUSTER_NAME" \
        --set spire-server.controllerManager.enabled=true \
        --set spire-server.controllerManager.identities.clusterSPIFFEIDs.default.enabled=true \
        --set spiffe-csi-driver.enabled=true \
        --set spiffe-oidc-discovery-provider.enabled=true \
        --wait --timeout 5m
    
    echo "✓ SPIRE installed"
    echo "  Trust domain: $SPIFFE_TRUST_DOMAIN"
    echo "  CSI driver: enabled (pods can mount SPIFFE workload API)"
    echo "  OIDC discovery: enabled (for JWT-SVID validation)"
    echo ""
    
    # Wait for all SPIRE components to be ready
    echo "  Waiting for SPIRE components..."
    kubectl wait --for=condition=ready pod -n spire-system -l app.kubernetes.io/name=server --timeout=120s 2>/dev/null || true
    kubectl wait --for=condition=ready pod -n spire-system -l app.kubernetes.io/name=agent --timeout=120s 2>/dev/null || true
    echo "✓ SPIRE components ready"
    echo ""
fi

# -----------------------------------------------------------------------------
# Generate Shared Secrets (used by multiple components)
# -----------------------------------------------------------------------------
# Generate OIDC client secret that will be used by KeyCloak and stored in OpenBao
if [[ "$INSTALL_KEYCLOAK" == "true" || "$INSTALL_OPENBAO" == "true" ]]; then
    echo "ℹ Setting up shared OIDC secret..."
    
    # Check if shared secret already exists (from previous run)
    if kubectl get secret shared-oidc-secret -n keycloak -o jsonpath='{.data.client-secret}' 2>/dev/null | base64 -d | grep -q .; then
        OIDC_CLIENT_SECRET=$(kubectl get secret shared-oidc-secret -n keycloak -o jsonpath='{.data.client-secret}' | base64 -d)
        echo "✓ Using existing OIDC client secret from shared-oidc-secret"
    else
        # Generate new secret and store it
        OIDC_CLIENT_SECRET=$(openssl rand -base64 32 | tr -d '/+=' | head -c 32)
        kubectl create namespace keycloak 2>/dev/null || true
        kubectl create secret generic shared-oidc-secret \
            --namespace keycloak \
            --from-literal=client-secret="$OIDC_CLIENT_SECRET" \
            2>/dev/null || true
        echo "✓ Generated and stored new OIDC client secret"
    fi
    echo ""
fi

# -----------------------------------------------------------------------------
# KeyCloak Installation (Local OIDC/SSO Provider)
# -----------------------------------------------------------------------------
# Uses official Keycloak image from quay.io (not Bitnami which requires subscription)
# Configured with in-memory H2 database for demo purposes (data not persisted)
# For production, use PostgreSQL backend
# -----------------------------------------------------------------------------
if [[ "$INSTALL_KEYCLOAK" == "true" ]]; then
    echo "ℹ Installing KeyCloak for local SSO/OIDC..."
    
    # Derive KeyCloak hostname from base hostname
    KEYCLOAK_HOSTNAME="keycloak.${BASE_HOSTNAME}"
    
    # Create namespace
    kubectl create namespace keycloak 2>/dev/null || true
    
    # Check if admin credentials secret already exists
    if kubectl get secret keycloak-admin -n keycloak >/dev/null 2>&1; then
        echo "  Using existing keycloak-admin secret"
        # Read the password from existing secret
        KEYCLOAK_ADMIN_PASSWORD=$(kubectl get secret keycloak-admin -n keycloak -o jsonpath='{.data.KEYCLOAK_ADMIN_PASSWORD}' | base64 -d)
    else
        # Generate admin password if not set via CLI
        if [[ -z "$KEYCLOAK_ADMIN_PASSWORD" ]]; then
            KEYCLOAK_ADMIN_PASSWORD=$(openssl rand -base64 16 | tr -d '/+=' | head -c 16)
        fi
        # Create admin credentials secret
        echo "  Creating keycloak-admin secret..."
        kubectl create secret generic keycloak-admin \
            --from-literal=KEYCLOAK_ADMIN=admin \
            --from-literal=KEYCLOAK_ADMIN_PASSWORD="$KEYCLOAK_ADMIN_PASSWORD" \
            -n keycloak
    fi
    
    echo "  Deploying KeyCloak using official image (quay.io/keycloak/keycloak)..."
    
    # Derive Keycloak external URL (on port 8443 for separate gateway)
    KEYCLOAK_EXTERNAL_HOSTNAME="keycloak.${BASE_HOSTNAME#*.}"  # e.g., keycloak.demo from nv_config_manager.demo
    KEYCLOAK_EXTERNAL_URL="https://${KEYCLOAK_EXTERNAL_HOSTNAME}:8443"
    
    # Deploy Keycloak using official image
    kubectl apply -f - << KEYCLOAK_DEPLOY_EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: keycloak
  namespace: keycloak
  labels:
    app: keycloak
spec:
  replicas: 1
  selector:
    matchLabels:
      app: keycloak
  template:
    metadata:
      labels:
        app: keycloak
    spec:
      containers:
      - name: keycloak
        image: quay.io/keycloak/keycloak:26.0
        args:
        - "start-dev"
        - "--hostname=${KEYCLOAK_EXTERNAL_URL}"
        env:
        # Admin credentials (KC_BOOTSTRAP_ADMIN_* for Keycloak 26+)
        - name: KC_BOOTSTRAP_ADMIN_USERNAME
          valueFrom:
            secretKeyRef:
              name: keycloak-admin
              key: KEYCLOAK_ADMIN
        - name: KC_BOOTSTRAP_ADMIN_PASSWORD
          valueFrom:
            secretKeyRef:
              name: keycloak-admin
              key: KEYCLOAK_ADMIN_PASSWORD
        # Proxy configuration
        - name: KC_PROXY_HEADERS
          value: "xforwarded"
        - name: KC_HTTP_ENABLED
          value: "true"
        - name: KC_HEALTH_ENABLED
          value: "true"
        # JVM memory settings - must be less than container limit
        - name: JAVA_OPTS_APPEND
          value: "-Xms512m -Xmx1536m"
        ports:
        - name: http
          containerPort: 8080
        - name: health
          containerPort: 9000
        # Startup probe with high threshold for slow JVM startup
        startupProbe:
          httpGet:
            path: /health/started
            port: 9000
          periodSeconds: 1
          failureThreshold: 600
        readinessProbe:
          httpGet:
            path: /health/ready
            port: 9000
          periodSeconds: 10
          failureThreshold: 3
        livenessProbe:
          httpGet:
            path: /health/live
            port: 9000
          periodSeconds: 10
          failureThreshold: 3
        resources:
          requests:
            memory: 1700Mi
            cpu: 500m
          limits:
            memory: 2000Mi
            cpu: 2
---
apiVersion: v1
kind: Service
metadata:
  name: keycloak
  namespace: keycloak
  labels:
    app: keycloak
spec:
  ports:
  - name: http
    port: 80
    targetPort: 8080
  selector:
    app: keycloak
KEYCLOAK_DEPLOY_EOF

    echo "✓ KeyCloak deployment created"
    echo "  Namespace: keycloak"
    echo "  Admin user: admin"
    echo "  Admin password: $KEYCLOAK_ADMIN_PASSWORD"
    echo ""
    
    # Wait for KeyCloak to be ready
    echo "  Waiting for KeyCloak pod to be ready (this may take 2-3 minutes)..."
    
    # Wait for deployment rollout
    if ! kubectl rollout status deployment/keycloak -n keycloak --timeout=300s 2>/dev/null; then
        echo "  ⚠ KeyCloak deployment not ready yet. It may still be starting."
        echo "  Check status with: kubectl get pods -n keycloak"
        echo "  View logs with: kubectl logs -n keycloak -l app=keycloak"
        echo ""
        echo "  Skipping realm configuration - run this script again after KeyCloak is ready."
        echo ""
    else
        echo "✓ KeyCloak is ready"
        
        # Get the pod name
        KEYCLOAK_POD=$(kubectl get pods -n keycloak -l app=keycloak -o jsonpath='{.items[0].metadata.name}')
        
        echo "  Configuring NVIDIA Config Manager realm and OIDC client via kubectl exec..."
        
        # Wait a bit more for Keycloak to fully initialize
        sleep 5
        
        # Use a curl pod to configure Keycloak (official Keycloak image doesn't have curl)
        echo "  Running configuration job..."
        
        # Delete any existing config job
        kubectl delete job keycloak-config -n keycloak 2>/dev/null || true
        
        # Create a job to configure Keycloak using curl image
        kubectl apply -f - << KEYCLOAK_CONFIG_EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: keycloak-config
  namespace: keycloak
spec:
  ttlSecondsAfterFinished: 300
  backoffLimit: 3
  template:
    spec:
      restartPolicy: OnFailure
      containers:
      - name: config
        image: curlimages/curl:latest
        command: ["/bin/sh", "-c"]
        args:
        - |
          set -e
          
          echo "Waiting for Keycloak to be ready..."
          until curl -sf http://keycloak:80/realms/master >/dev/null 2>&1; do
            echo "  Keycloak not ready yet, waiting..."
            sleep 5
          done
          echo "Keycloak is ready!"
          
          echo "Getting admin token..."
          TOKEN=\$(curl -sf -X POST "http://keycloak:80/realms/master/protocol/openid-connect/token" \
            -H "Content-Type: application/x-www-form-urlencoded" \
            -d "username=admin" \
            -d "password=${KEYCLOAK_ADMIN_PASSWORD}" \
            -d "grant_type=password" \
            -d "client_id=admin-cli" | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p')
          
          if [ -z "\$TOKEN" ]; then
            echo "Failed to get admin token"
            exit 1
          fi
          echo "Got admin token"
          
          # Check if realm exists
          if curl -sf -H "Authorization: Bearer \$TOKEN" "http://keycloak:80/admin/realms/nv-config-manager" >/dev/null 2>&1; then
            echo "Realm nv-config-manager already exists, skipping creation"
          else
            echo "Creating nv-config-manager realm..."
            curl -sf -X POST "http://keycloak:80/admin/realms" \
              -H "Authorization: Bearer \$TOKEN" \
              -H "Content-Type: application/json" \
              -d '{
                "realm": "nv-config-manager",
                "enabled": true,
                "sslRequired": "none",
                "registrationAllowed": false,
                "loginWithEmailAllowed": true,
                "duplicateEmailsAllowed": false,
                "resetPasswordAllowed": true,
                "bruteForceProtected": true,
                "accessTokenLifespan": 3600,
                "ssoSessionIdleTimeout": 1800,
                "ssoSessionMaxLifespan": 36000
              }'
            echo "Realm created"
          fi
          
          # Check if client exists
          if curl -sf -H "Authorization: Bearer \$TOKEN" "http://keycloak:80/admin/realms/nv-config-manager/clients?clientId=nv-config-manager" | grep -q "nv-config-manager"; then
            echo "Client nv-config-manager already exists, skipping creation"
          else
            echo "Creating nv-config-manager client..."
            curl -sf -X POST "http://keycloak:80/admin/realms/nv-config-manager/clients" \
              -H "Authorization: Bearer \$TOKEN" \
              -H "Content-Type: application/json" \
              -d '{
                "clientId": "nv-config-manager",
                "name": "NVIDIA Config Manager",
                "enabled": true,
                "publicClient": false,
                "secret": "${OIDC_CLIENT_SECRET}",
                "directAccessGrantsEnabled": true,
                "standardFlowEnabled": true,
                "implicitFlowEnabled": true,
                "serviceAccountsEnabled": true,
                "protocol": "openid-connect",
                "redirectUris": ["*", "http://127.0.0.1:*"],
                "webOrigins": ["*"],
                "attributes": {
                  "access.token.lifespan": "3600"
                }
              }'
            echo "Client created"
          fi

          # Check if public CLI client exists
          if curl -sf -H "Authorization: Bearer \$TOKEN" "http://keycloak:80/admin/realms/nv-config-manager/clients?clientId=nv-config-manager-cli" | grep -q "nv-config-manager-cli"; then
            echo "Client nv-config-manager-cli already exists, skipping creation"
          else
            echo "Creating nv-config-manager-cli public client..."
            curl -sf -X POST "http://keycloak:80/admin/realms/nv-config-manager/clients" \
              -H "Authorization: Bearer \$TOKEN" \
              -H "Content-Type: application/json" \
              -d '{
                "clientId": "nv-config-manager-cli",
                "name": "NVIDIA Config Manager CLI",
                "enabled": true,
                "publicClient": true,
                "directAccessGrantsEnabled": false,
                "standardFlowEnabled": true,
                "implicitFlowEnabled": false,
                "serviceAccountsEnabled": false,
                "protocol": "openid-connect",
                "redirectUris": ["http://localhost:*", "http://127.0.0.1:*"],
                "webOrigins": ["http://localhost:*", "http://127.0.0.1:*"],
                "attributes": {
                  "access.token.lifespan": "3600",
                  "pkce.code.challenge.method": "S256"
                }
              }'
            echo "CLI client created"
          fi
          
          # Check if demo user exists
          if curl -sf -H "Authorization: Bearer \$TOKEN" "http://keycloak:80/admin/realms/nv-config-manager/users?username=demo" | grep -q "demo"; then
            echo "User demo already exists, skipping creation"
          else
            echo "Creating demo user..."
            curl -sf -X POST "http://keycloak:80/admin/realms/nv-config-manager/users" \
              -H "Authorization: Bearer \$TOKEN" \
              -H "Content-Type: application/json" \
              -d '{
                "username": "demo",
                "email": "demo@config-manager.demo",
                "firstName": "Demo",
                "lastName": "User",
                "enabled": true,
                "emailVerified": true,
                "credentials": [{
                  "type": "password",
                  "value": "demo",
                  "temporary": false
                }]
              }'
            echo "Demo user created"
          fi
          
          echo "KeyCloak configuration complete!"
KEYCLOAK_CONFIG_EOF

        # Wait for the config job to complete
        echo "  Waiting for configuration job to complete..."
        if kubectl wait --for=condition=complete job/keycloak-config -n keycloak --timeout=120s 2>/dev/null; then
            echo "  Configuration job completed successfully"
            kubectl logs job/keycloak-config -n keycloak
        else
            echo "  ⚠ Configuration job may have failed. Check logs:"
            echo "    kubectl logs job/keycloak-config -n keycloak"
        fi
        
        echo "✓ KeyCloak configured with NVIDIA Config Manager realm"
    fi
    
    # Create separate Gateway for Keycloak on port 8443
    # This keeps Keycloak isolated from the main NVIDIA Config Manager gateway (which has OIDC auth)
    if [[ "$INSTALL_ENVOY_GATEWAY" == "true" ]] || kubectl get gatewayclass envoy-gateway &>/dev/null; then
        echo "  Creating Keycloak Gateway on port 8443..."
        
        # Create TLS certificate for Keycloak via cert-manager
        kubectl apply -f - << KEYCLOAK_CERT_EOF
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: keycloak-tls
  namespace: keycloak
spec:
  secretName: keycloak-tls
  duration: 8760h
  renewBefore: 720h
  commonName: "${KEYCLOAK_EXTERNAL_HOSTNAME}"
  dnsNames:
    - "${KEYCLOAK_EXTERNAL_HOSTNAME}"
  issuerRef:
    name: nv-config-manager-ca-issuer
    kind: ClusterIssuer
KEYCLOAK_CERT_EOF
        
        echo "  Waiting for Keycloak TLS certificate..."
        kubectl wait --timeout=60s -n keycloak certificate/keycloak-tls --for=condition=Ready 2>/dev/null || true
        
        # Create EnvoyProxy with hostPort for port 8443
        # Must run on control-plane node since that's the only node with Kind port mappings
        kubectl apply -f - << KEYCLOAK_ENVOYPROXY_EOF
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: EnvoyProxy
metadata:
  name: keycloak-gateway-proxy
  namespace: keycloak
spec:
  provider:
    type: Kubernetes
    kubernetes:
      envoyDeployment:
        pod:
          annotations:
            nv-config-manager.nvidia.com/component: keycloak-gateway
        container:
          resources:
            requests:
              cpu: 100m
              memory: 128Mi
        patch:
          type: StrategicMerge
          value:
            spec:
              template:
                spec:
                  nodeSelector:
                    node-role.kubernetes.io/control-plane: ""
                  tolerations:
                  - key: node-role.kubernetes.io/control-plane
                    operator: Exists
                    effect: NoSchedule
                  containers:
                  - name: envoy
                    ports:
                    - containerPort: 8443
                      hostPort: 8443
                      name: https-8443
                      protocol: TCP
KEYCLOAK_ENVOYPROXY_EOF
        
        # Create Keycloak Gateway (delete first if exists to avoid resourceVersion issues)
        kubectl delete gateway keycloak-gateway -n keycloak --ignore-not-found 2>/dev/null || true
        kubectl apply -f - << KEYCLOAK_GW_EOF
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: keycloak-gateway
  namespace: keycloak
  annotations:
    gateway.envoyproxy.io/envoy-proxy: keycloak-gateway-proxy
spec:
  gatewayClassName: envoy-gateway
  listeners:
    - name: https-8443
      protocol: HTTPS
      port: 8443
      hostname: "${KEYCLOAK_EXTERNAL_HOSTNAME}"
      tls:
        mode: Terminate
        certificateRefs:
          - kind: Secret
            name: keycloak-tls
      allowedRoutes:
        namespaces:
          from: Same
KEYCLOAK_GW_EOF
        
        # Create HTTPRoute for Keycloak
        kubectl apply -f - << KEYCLOAK_ROUTE_EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: keycloak
  namespace: keycloak
spec:
  parentRefs:
    - name: keycloak-gateway
      namespace: keycloak
  hostnames:
    - "${KEYCLOAK_EXTERNAL_HOSTNAME}"
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
      backendRefs:
        - name: keycloak
          port: 80
KEYCLOAK_ROUTE_EOF
        
        echo "  Waiting for Keycloak Gateway to be programmed..."
        kubectl wait --timeout=120s -n keycloak gateway/keycloak-gateway --for=condition=Programmed 2>/dev/null || true
        
        # Patch the Envoy deployment to ensure hostPort and nodeSelector are applied
        # (Envoy Gateway's strategic merge patch doesn't always apply these correctly)
        echo "  Patching Keycloak Gateway Envoy deployment for hostPort and control-plane scheduling..."
        sleep 5  # Wait for deployment to be created
        KEYCLOAK_ENVOY_DEPLOY=$(kubectl get deployment -n envoy-gateway-system -l gateway.envoyproxy.io/owning-gateway-name=keycloak-gateway -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
        if [[ -n "$KEYCLOAK_ENVOY_DEPLOY" ]]; then
            # Patch for hostPort
            kubectl patch deployment "$KEYCLOAK_ENVOY_DEPLOY" -n envoy-gateway-system --type=json -p '[
              {
                "op": "replace",
                "path": "/spec/template/spec/containers/0/ports/0",
                "value": {
                  "containerPort": 8443,
                  "hostPort": 8443,
                  "name": "https-8443",
                  "protocol": "TCP"
                }
              }
            ]' 2>/dev/null || true
            
            # Patch for nodeSelector and tolerations to run on control-plane
            kubectl patch deployment "$KEYCLOAK_ENVOY_DEPLOY" -n envoy-gateway-system --type=strategic -p '{
              "spec": {
                "template": {
                  "spec": {
                    "nodeSelector": {
                      "node-role.kubernetes.io/control-plane": ""
                    },
                    "tolerations": [
                      {
                        "key": "node-role.kubernetes.io/control-plane",
                        "operator": "Exists",
                        "effect": "NoSchedule"
                      }
                    ]
                  }
                }
              }
            }' 2>/dev/null || true
            
            echo "  Waiting for Keycloak Gateway Envoy to be ready on control-plane..."
            kubectl rollout status deployment "$KEYCLOAK_ENVOY_DEPLOY" -n envoy-gateway-system --timeout=60s 2>/dev/null || true
        fi
        
        echo "✓ Keycloak Gateway created on port 8443"
    else
        echo "  ⚠ Envoy Gateway not installed - skipping Keycloak Gateway creation"
        echo "    Access Keycloak via port-forward: kubectl port-forward -n keycloak svc/keycloak 8080:80"
    fi
    
    echo ""
    echo "  KeyCloak Access:"
    if [[ "$INSTALL_ENVOY_GATEWAY" == "true" ]] || kubectl get gatewayclass envoy-gateway &>/dev/null; then
        echo "    Via Gateway: ${KEYCLOAK_EXTERNAL_URL}"
        echo "    (Add to /etc/hosts: <host-ip> ${KEYCLOAK_EXTERNAL_HOSTNAME})"
    else
        echo "    Via port-forward: kubectl port-forward -n keycloak svc/keycloak 8080:80"
        echo "    URL: http://localhost:8080"
    fi
    echo "    Admin: admin / $KEYCLOAK_ADMIN_PASSWORD"
    echo ""
    echo "  NVIDIA Config Manager installer SSO configuration values:"
    echo "    --sso-provider keycloak \\"
    echo "    --sso-issuer ${KEYCLOAK_EXTERNAL_URL}/realms/nv-config-manager \\"
    echo "    --sso-client-id nv-config-manager \\"
    echo "    --sso-client-secret ${OIDC_CLIENT_SECRET} \\"
    echo "    --sso-jwks-uri http://keycloak.keycloak.svc.cluster.local:80/realms/nv-config-manager/protocol/openid-connect/certs"
    echo ""
    echo "    Demo User: demo / demo"
    echo ""
fi

# -----------------------------------------------------------------------------
# OpenBao Installation (Secrets Management)
# -----------------------------------------------------------------------------
if [[ "$INSTALL_OPENBAO" == "true" ]]; then
    echo "ℹ Installing OpenBao for secrets management..."
    
    # Add OpenBao Helm repo
    helm repo add openbao https://openbao.github.io/openbao-helm 2>/dev/null || true
    helm repo update
    
    # Install OpenBao in dev mode (unsealed, root token = "root")
    helm upgrade --install openbao openbao/openbao \
        --version "$OPENBAO_CHART_VERSION" \
        --namespace openbao \
        --create-namespace \
        --set server.dev.enabled=true \
        --set server.dev.devRootToken=root \
        --set server.dataStorage.enabled=false \
        --set server.standalone.enabled=false \
        --set injector.enabled=false \
        --wait --timeout 5m
    
    echo "✓ OpenBao installed (dev mode)"
    echo "  Namespace: openbao"
    echo "  Root token: root"
    echo "  Address: http://openbao.openbao.svc.cluster.local:8200"
    echo ""
    
    # Wait for OpenBao to be ready
    kubectl wait --for=condition=ready pod -n openbao openbao-0 --timeout=120s
    
    # Enable KV secrets engines and create demo secrets
    echo "  Configuring OpenBao secrets..."
    
    # Enable kv-v2 secrets engines
    kubectl -n openbao exec openbao-0 -- bao secrets enable -path=nv-config-manager kv-v2 2>/dev/null || \
        echo "  nv-config-manager KV engine already enabled"
    kubectl -n openbao exec openbao-0 -- bao secrets enable -path=secrets kv-v2 2>/dev/null || \
        echo "  secrets KV engine already enabled"
    
    # Create demo secrets for NVIDIA Config Manager deployment
    echo "  Creating demo secrets..."
    
    # Helper function to write secrets
    write_bao_secret() {
        local path="$1"
        shift
        kubectl -n openbao exec openbao-0 -- env BAO_TOKEN=root bao kv put "nv-config-manager/${path}" "$@" >/dev/null 2>&1 || return $?
        return 0
    }
    
    # Nautobot API token - must match superuser_api_token in demo/nautobot-app
    # This token is used by nv-config-manager services to authenticate to Nautobot
    write_bao_secret "demo/nautobot" \
        token="0123456789abcdef0123456789abcdef01234567" \
        nats_password="demo-nats-nv-config-manager-password" \
        nats_sys_password="demo-nats-sys-password" \
        nats_nautobot_password="demo-nats-nautobot-password"
    
    # Redis
    write_bao_secret "demo/redis" \
        password="demo-redis-password"
    
    # PostgreSQL (CNPG)
    write_bao_secret "demo/postgres" \
        temporal_user="temporal" \
        temporal_password="demo-temporal-pass" \
        temporal_visibility_user="temporal_visibility" \
        temporal_visibility_password="demo-visibility-pass" \
        config_store_user="config_store" \
        config_store_password="demo-configstore-pass" \
        dhcp_user="dhcp" \
        dhcp_password="demo-dhcp-pass" \
        nautobot_user="nautobot" \
        nautobot_password="demo-nautobot-db-pass"
    
    # Network credentials
    write_bao_secret "demo/network" \
        user="admin" \
        password="demo-network-password"
    
    # Nautobot App
    write_bao_secret "demo/nautobot-app" \
        admin_password="admin" \
        django_secret_key="demo-django-secret-key-that-is-long-enough-for-django" \
        superuser_api_token="0123456789abcdef0123456789abcdef01234567"
    
    # Redfish/BMC credentials
    write_bao_secret "demo/redfish" \
        lenovo_default_user="USERID" \
        lenovo_default_password="PASSW0RD" \
        lenovo_config_manager_password="demo-lenovo-nv-config-manager" \
        bluefield_default_user="admin" \
        bluefield_default_password="admin" \
        bluefield_config_manager_password="demo-bluefield-nv-config-manager"
    
    # BMC credentials JSON
    write_bao_secret "demo/bmc" \
        'bmc-creds.json={"default": {"username": "admin", "password": "admin"}}'
    
    # Slack
    write_bao_secret "demo/slack" \
        token="xoxb-demo-slack-token"
    
    # DHCP
    write_bao_secret "demo/dhcp" \
        password="demo-dhcp-password"
    
    # UFM
    write_bao_secret "demo/ufm" \
        ufm_api_user="admin" \
        ufm_api_token_r1="demo-ufm-token"
    
    # OIDC/SSO credentials (for KeyCloak integration)
    # Use the secrets generated earlier (or generate if not set)
    if [[ -z "$OIDC_CLIENT_SECRET" ]]; then
        OIDC_CLIENT_SECRET=$(openssl rand -base64 32 | tr -d '/+=' | head -c 32)
    fi

    # oauth2-proxy base64url-decodes the cookie secret and expects the result
    # to be 16, 24, or 32 bytes. Upstream recommends
    # `openssl rand -base64 32 | tr '+/' '-_' | tr -d '='` — 32 bytes of
    # entropy in unpadded base64url (43 chars) → 32 decoded bytes (AES-256).
    # A plain `openssl rand -base64 32` would produce 44 chars whose `+`/`/`
    # fail the base64url decode, so oauth2-proxy CrashLoopBackOffs at boot
    # (cookie_secret must be 16/24/32 bytes).
    EXISTING_COOKIE_SECRET=$(kubectl -n openbao exec openbao-0 -- env BAO_TOKEN=root bao kv get -field=cookie_secret "nv-config-manager/demo/oidc" 2>/dev/null || true)
    if [[ -n "$EXISTING_COOKIE_SECRET" ]]; then
        OIDC_COOKIE_SECRET="$EXISTING_COOKIE_SECRET"
    else
        OIDC_COOKIE_SECRET=$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '=')
    fi
    
    write_bao_secret "demo/oidc" \
        client_secret="$OIDC_CLIENT_SECRET" \
        cookie_secret="$OIDC_COOKIE_SECRET"
    
    echo "✓ OpenBao configured with demo secrets"
    
    # Create the vault-token secret in the nv-config-manager namespace for ESO
    # This uses token auth which is simpler for local development
    echo ""
    echo "  Creating vault-token secret in nv-config-manager namespace..."
    
    # Ensure nv-config-manager namespace exists
    kubectl create namespace nv-config-manager 2>/dev/null || true
    
    # Create or update the vault-token secret
    kubectl create secret generic vault-token \
        --from-literal=token=root \
        -n nv-config-manager 2>/dev/null || \
    kubectl patch secret vault-token \
        --type='json' -p='[{"op":"replace","path":"/data/token","value":"'$(echo -n "root" | base64)'"}]' \
        -n nv-config-manager
    
    echo "✓ vault-token secret created in nv-config-manager namespace"
    echo "    Use token auth in the installer App Secrets section"
    
    # Create site config secrets if sites were specified
    if [[ ${#CONFIG_SECRETS_SITES[@]} -gt 0 ]]; then
        echo ""
        echo "  Creating site config secrets..."
        
        # Generate random passwords for site secrets
        generate_password() {
            local length="$1"
            openssl rand -base64 16 | tr -d '/+=' | head -c "$length" || return $?
            return 0
        }
        
        MOCK_ROOT_PASSWORD=$(generate_password 16)
        MOCK_API_USER_KEY=$(generate_password 16)
        MOCK_BGP_PASSWORD=$(generate_password 16)
        MOCK_ISIS_PASSWORD=$(generate_password 16)
        MOCK_TACACS_KEY=$(generate_password 16)
        
        for site in "${CONFIG_SECRETS_SITES[@]}"; do
            # Convert site name to slug (lowercase, spaces to dashes)
            site_slug=$(echo "$site" | tr '[:upper:]' '[:lower:]' | tr ' ' '-')
            
            write_bao_secret "demo/site/${site_slug}/config_secrets" \
                root_password_r1="$MOCK_ROOT_PASSWORD" \
                api_user_key_r1="$MOCK_API_USER_KEY" \
                bgp_password_r1="$MOCK_BGP_PASSWORD" \
                isis_password_r1="$MOCK_ISIS_PASSWORD" \
                tacacs_key_r1="$MOCK_TACACS_KEY"
            
            echo "    ✓ Site: ${site_slug}"
        done
        
        echo "✓ Site config secrets created"
    fi
    
    # -------------------------------------------------------------------------
    # Generate ESO Config File
    # -------------------------------------------------------------------------
    # Auto-generate the eso-config.yaml file that maps to the secrets we created
    ESO_CONFIG_FILE="${ESO_CONFIG_OUTPUT:-$HOME/eso-config.yaml}"
    echo "  Generating ESO config file: $ESO_CONFIG_FILE"
    
    cat > "$ESO_CONFIG_FILE" << ESO_CONFIG_EOF
# =============================================================================
# NVIDIA Config Manager ESO Configuration
# =============================================================================
# Auto-generated by setup-vm-prereqs.sh
# This file configures ESO to fetch secrets from OpenBao for NVIDIA Config Manager.
#
# Usage with installer ESO settings:
#   --vault --vault-server http://openbao.openbao.svc.cluster.local:8200 \\
#   --vault-token-auth --eso-config $ESO_CONFIG_FILE
# =============================================================================

# Base path: KV v2 secrets engine mount path in OpenBao
basePath: nv-config-manager

# Environment path: prefix for all secrets within the secrets engine
environmentPath: ${ENVIRONMENT_PATH}

secrets:
  # OIDC/SSO credentials
  oidc:
    path: ${ENVIRONMENT_PATH}/oidc
    keys:
      clientSecret: client_secret
      cookieSecret: cookie_secret

  # Nautobot API credentials
  nautobot:
    path: ${ENVIRONMENT_PATH}/nautobot
    keys:
      token: "token"
      natsPassword: "nats_password"
      natsSysPassword: "nats_sys_password"
      natsNautobotPassword: "nats_nautobot_password"

  # Redis credentials
  redis:
    path: ${ENVIRONMENT_PATH}/redis
    keys:
      password: "password"

  # PostgreSQL credentials (all CNPG clusters)
  postgres:
    path: ${ENVIRONMENT_PATH}/postgres
    keys:
      temporalUser: "temporal_user"
      temporalPassword: "temporal_password"
      temporalVisibilityUser: "temporal_visibility_user"
      temporalVisibilityPassword: "temporal_visibility_password"
      configStoreUser: "config_store_user"
      configStorePassword: "config_store_password"
      dhcpUser: "dhcp_user"
      dhcpPassword: "dhcp_password"
      nautobotUser: "nautobot_user"
      nautobotPassword: "nautobot_password"

  # Network device credentials
  network:
    path: ${ENVIRONMENT_PATH}/network
    keys:
      user: "user"
      password: "password"

  # Nautobot app secrets
  nautobotApp:
    path: ${ENVIRONMENT_PATH}/nautobot-app
    keys:
      adminPassword: "admin_password"
      djangoSecretKey: "django_secret_key"
      superuserApiToken: "superuser_api_token"

  # BMC credentials JSON
  bmc:
    path: ${ENVIRONMENT_PATH}/bmc
    keys:
      credsJson: "bmc-creds.json"

  # Redfish/BMC credentials
  redfish:
    path: ${ENVIRONMENT_PATH}/redfish
    keys:
      lenovoDefaultUser: "lenovo_default_user"
      lenovoDefaultPassword: "lenovo_default_password"
      lenovoConfigManagerPassword: "lenovo_config_manager_password"
      bluefieldDefaultUser: "bluefield_default_user"
      bluefieldDefaultPassword: "bluefield_default_password"
      bluefieldConfigManagerPassword: "bluefield_config_manager_password"

  # Slack integration
  slack:
    path: ${ENVIRONMENT_PATH}/slack
    keys:
      token: "token"

  # DHCP service credentials
  dhcp:
    path: ${ENVIRONMENT_PATH}/dhcp
    keys:
      password: "password"

  # UFM API credentials
  ufm:
    path: ${ENVIRONMENT_PATH}/ufm
    keys:
      apiUser: "ufm_api_user"
      apiTokenR1: "ufm_api_token_r1"

ESO_CONFIG_EOF

    # Add configSecrets section if sites were specified
    if [[ ${#CONFIG_SECRETS_SITES[@]} -gt 0 ]]; then
        cat >> "$ESO_CONFIG_FILE" << 'ESO_CONFIG_HEADER'
# Site/region config secrets for render service
configSecrets:
  enabled: true
  sites:
ESO_CONFIG_HEADER
        
        for site in "${CONFIG_SECRETS_SITES[@]}"; do
            site_slug=$(echo "$site" | tr '[:upper:]' '[:lower:]' | tr ' ' '-')
            cat >> "$ESO_CONFIG_FILE" << ESO_CONFIG_SITE
    - name: ${site_slug}
      path: "${ENVIRONMENT_PATH}/site/${site_slug}/config_secrets"
ESO_CONFIG_SITE
        done
    fi
    
    echo "✓ ESO config generated: $ESO_CONFIG_FILE"
    echo ""
fi

# -----------------------------------------------------------------------------
# External Secrets Operator Installation
# -----------------------------------------------------------------------------
if [[ "$INSTALL_ESO" == "true" ]]; then
    echo "ℹ Installing External Secrets Operator..."
    
    # Add ESO Helm repo
    helm repo add external-secrets https://charts.external-secrets.io 2>/dev/null || true
    helm repo update
    
    # Install ESO
    helm upgrade --install external-secrets external-secrets/external-secrets \
        --namespace external-secrets \
        --create-namespace \
        --set installCRDs=true \
        --wait --timeout 5m
    
    echo "✓ External Secrets Operator installed"
    echo "  Namespace: external-secrets"
    echo ""
    
    # Wait for ESO to be ready
    kubectl wait --for=condition=ready pod -n external-secrets -l app.kubernetes.io/name=external-secrets --timeout=120s
    
    # Wait for CRDs to be established
    echo "  Waiting for ESO CRDs to be ready..."
    kubectl wait --for=condition=established crd/clustersecretstores.external-secrets.io --timeout=60s 2>/dev/null || true
    kubectl wait --for=condition=established crd/externalsecrets.external-secrets.io --timeout=60s 2>/dev/null || true
    
    # Note: We don't create a ClusterSecretStore here.
    # The Helm chart creates its own SecretStore that uses token auth
    # to authenticate to OpenBao. The vault-token secret was created above
    # in the OpenBao installation section.
    #
    # Configure installer App Secrets with:
    #   --vault --vault-server http://openbao.openbao.svc.cluster.local:8200
    #   --vault-token-auth --eso-config <config-file>
    
    if [[ "$INSTALL_OPENBAO" == "true" ]]; then
        echo "  OpenBao configured with token auth"
        echo "  Use these installer settings:"
        echo "    --vault --vault-server http://openbao.openbao.svc.cluster.local:8200 --vault-token-auth"
        echo ""
    fi
fi

# =============================================================================
# SUMMARY
# =============================================================================
echo "================================================"
echo "  Setup Complete!"
echo "================================================"
echo ""
echo "Cluster: $CLUSTER_NAME"
echo "Nodes:"
kubectl get nodes -o wide
echo ""
echo "Storage Classes:"
kubectl get storageclass
echo ""
echo "MetalLB IP Range: $(kubectl get ipaddresspool -n metallb-system kind-pool -o jsonpath='{.spec.addresses[0]}' 2>/dev/null || echo 'configured')"
echo ""

# Infrastructure & Security Stack Summary
if [[ "$INSTALL_CERT_MANAGER" == "true" || "$INSTALL_ENVOY_GATEWAY" == "true" ]]; then
    echo "Infrastructure:"
    
    if [[ "$INSTALL_CERT_MANAGER" == "true" ]]; then
        CM_STATUS=$(kubectl get pods -n cert-manager -l app=cert-manager -o jsonpath='{.items[0].status.phase}' 2>/dev/null || echo "Unknown")
        echo "  cert-manager: $CM_STATUS"
        echo "    ClusterIssuer: nv-config-manager-ca-issuer"
    fi
    
    if [[ "$INSTALL_ENVOY_GATEWAY" == "true" ]]; then
        EG_STATUS=$(kubectl get pods -n envoy-gateway-system -l control-plane=envoy-gateway -o jsonpath='{.items[0].status.phase}' 2>/dev/null || echo "Unknown")
        echo "  Envoy Gateway: $EG_STATUS"
        GATEWAY_IP=$(kubectl get gateway shared-gateway -n envoy-gateway-system -o jsonpath='{.status.addresses[0].value}' 2>/dev/null || echo "pending")
        echo "    Shared Gateway IP: $GATEWAY_IP"
        echo "    Add to /etc/hosts: <host-ip> ${BASE_HOSTNAME} auth.${BASE_HOSTNAME} keycloak.${BASE_HOSTNAME#*.}"
    fi
    echo ""
fi

if [[ "$INSTALL_SPIRE" == "true" || "$INSTALL_KEYCLOAK" == "true" || "$INSTALL_OPENBAO" == "true" || "$INSTALL_ESO" == "true" ]]; then
    echo "Security Stack:"
    
    if [[ "$INSTALL_SPIRE" == "true" ]]; then
        SPIRE_STATUS=$(kubectl get pods -n spire-system -l app.kubernetes.io/name=server -o jsonpath='{.items[0].status.phase}' 2>/dev/null || echo "Unknown")
        echo "  SPIRE: $SPIRE_STATUS (trust domain: $SPIFFE_TRUST_DOMAIN)"
    fi
    
    if [[ "$INSTALL_KEYCLOAK" == "true" ]]; then
        KC_STATUS=$(kubectl get pods -n keycloak -l app=keycloak -o jsonpath='{.items[0].status.phase}' 2>/dev/null || echo "Unknown")
        echo "  KeyCloak: $KC_STATUS"
        echo "    Gateway: https://keycloak.${BASE_HOSTNAME#*.}:8443"
        echo "    Client ID: nv-config-manager"
    fi
    
    if [[ "$INSTALL_OPENBAO" == "true" ]]; then
        BAO_STATUS=$(kubectl get pods -n openbao openbao-0 -o jsonpath='{.status.phase}' 2>/dev/null || echo "Unknown")
        echo "  OpenBao: $BAO_STATUS (dev mode, token: root)"
        ESO_CONFIG_FILE="${ESO_CONFIG_OUTPUT:-$HOME/eso-config.yaml}"
        if [[ -f "$ESO_CONFIG_FILE" ]]; then
            echo "    ESO Config: $ESO_CONFIG_FILE"
        fi
    fi
    
    if [[ "$INSTALL_ESO" == "true" ]]; then
        ESO_STATUS=$(kubectl get pods -n external-secrets -l app.kubernetes.io/name=external-secrets -o jsonpath='{.items[0].status.phase}' 2>/dev/null || echo "Unknown")
        echo "  ESO: $ESO_STATUS"
        if [[ "$INSTALL_OPENBAO" == "true" ]]; then
            echo "    ClusterSecretStore: openbao (cluster-scoped, shared by all namespaces)"
        fi
    fi
    echo ""
fi

echo "Next steps:"
echo "  1. Load air-gapped images (if applicable)"
echo "  2. Run the NVIDIA Config Manager installer with your configuration"
if [[ "$INSTALL_KEYCLOAK" == "true" ]]; then
    KEYCLOAK_EXT_HOST="keycloak.${BASE_HOSTNAME#*.}"
    echo ""
    echo "For KeyCloak SSO, use these installer config values:"
    echo "  --sso --sso-provider keycloak \\"
    echo "  --sso-issuer https://${KEYCLOAK_EXT_HOST}:8443/realms/nv-config-manager \\"
    echo "  --sso-client-id nv-config-manager \\"
    echo "  --sso-client-secret \$(kubectl get secret -n openbao openbao-0 -o jsonpath='{.data.root-token}' | base64 -d && bao kv get -field=client_secret nv-config-manager/demo/oidc) \\"
    echo "  --sso-jwks-uri http://keycloak.keycloak.svc.cluster.local:80/realms/nv-config-manager/protocol/openid-connect/certs"
    echo ""
    echo "  Or with explicit client secret:"
    echo "  --sso --sso-provider keycloak \\"
    echo "  --sso-issuer https://${KEYCLOAK_EXT_HOST}:8443/realms/nv-config-manager \\"
    echo "  --sso-client-id nv-config-manager \\"
    echo "  --sso-client-secret <your-client-secret> \\"
    echo "  --sso-jwks-uri http://keycloak.keycloak.svc.cluster.local:80/realms/nv-config-manager/protocol/openid-connect/certs"
fi
if [[ "$INSTALL_SPIRE" == "true" ]]; then
    echo ""
    echo "For SPIFFE/SPIRE mTLS, use:"
    echo "  --spiffe --spiffe-provider spire --spiffe-trust-domain $SPIFFE_TRUST_DOMAIN"
fi
if [[ "$INSTALL_OPENBAO" == "true" && "$INSTALL_ESO" == "true" ]]; then
    ESO_CONFIG_FILE="${ESO_CONFIG_OUTPUT:-$HOME/eso-config.yaml}"
    echo ""
    echo "For OpenBao/ESO secrets management, use:"
    echo "  --vault --vault-server http://openbao.openbao.svc.cluster.local:8200 \\"
    echo "  --vault-token-auth --eso-config $ESO_CONFIG_FILE"
fi
echo ""
