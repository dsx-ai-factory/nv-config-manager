# Air-Gapped Bundle Tooling

This directory builds offline installation bundles for NVIDIA Config Manager. For production installation steps, see [Air-Gapped Installation](../../docs/install/install-airgapped.mdx).

## Build

```bash
cd deploy/airgapped
export NGC_API_KEY="your-ngc-api-key"
./create-airgapped.sh --version v1.0.0 --arch amd64
```

Build hosts need `helm`, `jq`, `curl`, a container runtime (`docker` or `containerd`/`ctr`), and `skopeo`. Skopeo is required as the image archive fallback when Docker cannot export an architecture-specific image directly.

Useful options:

| Option | Description |
| ------ | ----------- |
| `--arch amd64|arm64|both` | Select target architecture |
| `--output DIR` | Write bundles to a custom directory |
| `--runtime auto|docker|containerd` | Select image pull/save runtime |
| `--include-skopeo` | Include a local Skopeo binary under `tools/skopeo/` |
| `--skopeo-binary PATH` | Select the Skopeo binary to include |
| `--include-agpl-observability` | Include AGPL Grafana/Loki/Tempo observability charts and related images |
| `--skip-images` | Package chart and installer without image tarballs |
| `--local-image-fallback` | For pre-release E2E tests, save a locally tagged image when its source registry pull fails |
| `--allow-missing-images` | Continue after missing images; use only for diagnostics because the bundle may not install offline |
| `--skip-chart` | Skip Helm chart packaging |
| `--skip-docs` | Skip copying documentation source |

Default bundles exclude Grafana, Loki, and Tempo charts/images because their OSS distributions are AGPLv3. Pass `--include-agpl-observability` only when that license is acceptable for the target environment.

## Bundle Contents

```text
helm/                    # NVIDIA Config Manager Helm chart and packaged chart
charts/                  # Dependency charts
images/                  # Image tarballs and image-list.txt
manifests/               # Image loader and operator manifests
installer/               # Offline installer package and install.sh
docs/                    # Optional documentation source
tools/skopeo/            # Optional bundled Skopeo binary
upload-to-registry.sh    # OCI registry image and chart upload helper
operator-versions.env    # Dependency version pins
manifest.json            # Bundle metadata
```

## Target Usage

```bash
tar -xzf nv-config-manager-airgapped-v1.0.0-amd64.tar.gz
cd nv-config-manager-airgapped-v1.0.0-amd64
./upload-to-registry.sh \
  --registry registry.example.com/nv-config-manager \
  --chart-registry registry.example.com/nv-config-manager/charts \
  --username '<user>' \
  --password-stdin
./installer/install.sh
./installer/nvcm-installer init --config install.yaml
./installer/nvcm-installer deploy install.yaml --chart-dir helm --image-source registry
```

The upload helper uses bundled Skopeo when present, then system Skopeo, then Docker in `--mode auto`. It uploads the packaged chart with `helm push` and writes `image-map.tsv` for image source-to-target mapping. Use `--plain-http` only for local HTTP registries such as `registry:2` test containers. When using Docker mode with an architecture-specific bundle, pass `--platform linux/amd64` or `--platform linux/arm64` so Docker pushes a single-platform manifest. The installer uses local dependency charts and manifests when `cluster.airgapped` is enabled in the config.

When `cluster.airgapped: true` and `images.registry` points at the same namespace used by `upload-to-registry.sh`, the installer derives the uploader-style paths automatically. This expanded sample shows the equivalent explicit overrides for auditability or for custom image maps:

```yaml
cluster:
  hostname: config-manager.example.com
  namespace: nv-config-manager
  environment: prod
  airgapped: true

images:
  source: registry
  registry: registry.example.com/nv-config-manager
  tag: "1.3.0"
  pull_secret:
    name: registry-credentials
    server: registry.example.com
    username: "<user>"
    password: "<token>"
  overrides:
    nvConfigManager:
      repository: registry.example.com/nv-config-manager/nvidian/cfa/nv-config-manager
    nvConfigManagerUi:
      repository: registry.example.com/nv-config-manager/nvidian/cfa/nv-config-manager-ui
    kea:
      repository: registry.example.com/nv-config-manager/nvidian/cfa/nv-config-manager-kea
    keaAdmin:
      repository: registry.example.com/nv-config-manager/nvidian/cfa/nv-config-manager-kea-admin
    nautobot:
      repository: registry.example.com/nv-config-manager/nvidian/cfa/nv-config-manager-nautobot
    natsReady:
      repository: registry.example.com/nv-config-manager/nvidian/cfa/nv-config-manager-nats-ready
    httpEcho:
      repository: registry.example.com/nv-config-manager/hashicorp/http-echo
      tag: "1.0"
    kubectl:
      repository: registry.example.com/nv-config-manager/alpine/kubectl
      tag: "1.35.4"
    busybox:
      repository: registry.example.com/nv-config-manager/library/busybox
      tag: "1.36"
    redis:
      repository: registry.example.com/nv-config-manager/library/redis
      tag: "7-alpine"
    nats:
      repository: registry.example.com/nv-config-manager/library/nats
      tag: "2.14-alpine"
    natsBox:
      repository: registry.example.com/nv-config-manager/natsio/nats-box
      tag: "0.14.3"
    temporalServer:
      repository: registry.example.com/nv-config-manager/nvidian/cfa/nv-config-manager-temporal
    temporalUi:
      repository: registry.example.com/nv-config-manager/nvidian/cfa/nv-config-manager-temporal-ui
    nautobotNginx:
      repository: registry.example.com/nv-config-manager/nginxinc/nginx-unprivileged
      tag: "1.27"
    spiffeHelper:
      repository: registry.example.com/nv-config-manager/spiffe/spiffe-helper
      tag: "0.8.0"
    oidcProxy:
      repository: registry.example.com/nv-config-manager/oauth2-proxy/oauth2-proxy
      tag: "v7.15.4"
    templatePluginInstaller:
      repository: registry.example.com/nv-config-manager/library/python
      tag: "3.13-alpine"
    envoyGateway:
      repository: registry.example.com/nv-config-manager/envoyproxy/gateway
      tag: "v1.6.5"
    envoyRatelimit:
      repository: registry.example.com/nv-config-manager/envoyproxy/ratelimit
      tag: "c8765e89"
    envoyProxy:
      repository: registry.example.com/nv-config-manager/envoyproxy/envoy
      tag: "distroless-v1.36.5"
    certManagerController:
      repository: registry.example.com/nv-config-manager/jetstack/cert-manager-controller
      tag: "v1.20.2"
    certManagerWebhook:
      repository: registry.example.com/nv-config-manager/jetstack/cert-manager-webhook
      tag: "v1.20.2"
    certManagerCainjector:
      repository: registry.example.com/nv-config-manager/jetstack/cert-manager-cainjector
      tag: "v1.20.2"
    certManagerStartupApiCheck:
      repository: registry.example.com/nv-config-manager/jetstack/cert-manager-startupapicheck
      tag: "v1.20.2"
    certManagerAcmesolver:
      repository: registry.example.com/nv-config-manager/jetstack/cert-manager-acmesolver
      tag: "v1.20.2"
    cnpgOperator:
      repository: registry.example.com/nv-config-manager/cloudnative-pg/cloudnative-pg
      tag: "1.29.0"
    postgresql:
      repository: registry.example.com/nv-config-manager/cloudnative-pg/postgresql
      tag: "18.0-system-trixie"
    pgbouncer:
      repository: registry.example.com/nv-config-manager/cloudnative-pg/pgbouncer
      tag: "1.22.1"
    prometheusServer:
      repository: registry.example.com/nv-config-manager/prometheus/prometheus
      tag: "v3.11.3"
    prometheusConfigReloader:
      repository: registry.example.com/nv-config-manager/prometheus-operator/prometheus-config-reloader
      tag: "v0.90.1"
    alloy:
      repository: registry.example.com/nv-config-manager/grafana/alloy
      tag: "v1.16.0"
    alloyConfigReloader:
      repository: registry.example.com/nv-config-manager/prometheus-operator/prometheus-config-reloader
      tag: "v0.90.1"
```

## Image Loading Alternative

Use direct node loading only for demos or clusters where an OCI registry is not available.

```bash
# Sequential loading, useful for Kind and small clusters
./manifests/load-airgapped-images.sh ./images

# Parallel loading through a DaemonSet
./manifests/load-airgapped-images.sh /shared/nv-config-manager/images --daemonset

# SSH loading for real nodes
./manifests/load-airgapped-images.sh /shared/nv-config-manager/images --ssh --ssh-user admin --ssh-key ~/.ssh/id_rsa
```

## Related Documentation

- [Air-Gapped Installation](../../docs/install/install-airgapped.mdx)
- [Local Development Quick Start](../../docs/getting-started/local-development-quick-start.mdx)
- [Installer](../../installer/README.md)
