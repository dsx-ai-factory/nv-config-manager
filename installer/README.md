# NVIDIA Config Manager Installer

A full-screen TUI (Terminal User Interface) wizard for configuring, deploying, and
managing NVIDIA Config Manager installations. It generates a repeatable `nv-config-manager-install.yaml`
configuration file, produces Helm values and Kubernetes secrets, and orchestrates the
full deployment lifecycle — all from a single interactive tool.

The installer is packaged separately from the main NVIDIA Config Manager codebase so that its TUI
dependencies (`textual`) are not pulled into runtime containers.

## Table of Contents

- [Quick Start](#quick-start)
- [Installation](#installation)
- [CLI Commands](#cli-commands)
- [TUI Wizard](#tui-wizard)
- [Configuration Reference](#configuration-reference)
- [Deployment Steps](#deployment-steps)
- [Template Secret Scanner](#template-secret-scanner)
- [Development](#development)

---

## Quick Start

> **macOS users:** The default Terminal.app does not support text selection or
> copy/paste within Textual TUI applications. For the best experience, use
> [iTerm2](https://iterm2.com/) (use **Ctrl-C** to copy selected text in iTerm2).

```bash
# From the repository root
cd installer

# Install dependencies
uv sync

# Launch the interactive wizard
uv run nvcm-installer init

# Re-open an existing config
uv run nvcm-installer init --config nv-config-manager-install.yaml

# Validate a config file
uv run nvcm-installer validate nv-config-manager-install.yaml

# Generate Helm values without deploying
uv run nvcm-installer generate-values nv-config-manager-install.yaml -o ./generated

# Update content in GitOps-managed PVCs after the NVCM application is healthy
uv run nvcm-installer pvc-updater jobs --namespace nv-config-manager \
  --release-name nv-config-manager --source ./custom-jobs
```

---

## Installation

### Development (recommended)

```bash
cd installer
uv sync
uv run nvcm-installer --help
```

### As a standalone tool

```bash
cd installer
uv tool install .
nvcm-installer --help
```

`nvcm-installer` is the short command name. The longer `nv-config-manager-installer`
command remains available for compatibility and accepts the same subcommands and flags.

---

## CLI Commands

### `nvcm-installer init`

Launch the interactive TUI wizard.

| Flag | Default | Description |
|------|---------|-------------|
| `--config`, `-c` | `nv-config-manager-install.yaml` | Path to create or load an existing config |

The wizard walks through all configuration sections, saves to `nv-config-manager-install.yaml`
(with `0600` permissions), and can deploy directly from within the TUI.

### `nvcm-installer validate`

Validate a configuration file without deploying.

```bash
nvcm-installer validate nv-config-manager-install.yaml
```

Checks include:
- Pydantic schema validation (types, enums, required fields)
- `cluster.hostname` is non-empty
- At least one site defined
- `sso.issuer_url` required when SSO is enabled
- Custom jobs require local Nautobot (`services.nautobot: true`)

### `nvcm-installer generate-values`

Generate deployment artifacts without running a deployment.

```bash
nvcm-installer generate-values nv-config-manager-install.yaml --output-dir ./generated
```

| Flag | Default | Description |
|------|---------|-------------|
| `--output-dir`, `-o` | `.` | Directory for generated files |
| `--local-images` | `false` | Use local image tags instead of registry |
| `--chart-dir` | auto-detect | Helm chart directory used for size profile overlays |

Produces:
- `values-generated.yaml` — Combined override file with TUI-generated values and the selected `cluster.size` profile
- `config-secrets.ini` — Site-specific credential INI files

### `nvcm-installer deploy`

Run a headless (non-interactive) deployment.

```bash
nvcm-installer deploy nv-config-manager-install.yaml \
  --build-images \
  --load-kind \
  --install-envoy-gateway \
  --install-cert-manager \
  --install-cnpg-operator
```

| Flag | Default | Description |
|------|---------|-------------|
| `--chart-dir` | `deploy/helm` | Path to the Helm chart |
| `--image-source` | `local` | `local` or `registry` |
| `--ngc-api-key` | `""` | NGC API key for NVCR authentication |
| `--build-images` | `false` | Build Docker images locally |
| `--load-kind` | `false` | Load images into Kind cluster |
| `--kind-cluster` | `nv-config-manager` | Kind cluster name |
| `--install-envoy-gateway` | `false` | Install Envoy Gateway CRDs and operator |
| `--install-cert-manager` | `false` | Install cert-manager |
| `--install-cnpg-operator` | `false` | Install CloudNativePG operator |
| `--helm-timeout` | `15m` | Helm install/upgrade timeout |
| `--recreate-secrets` | `false` | Force-recreate Kubernetes secrets |
| `--vault-token-file` | unset | File containing a provisioning token when ESO/Vault is selected |
| `--populate-vault` / `--skip-vault-population` | populate | Populate missing ESO values, or use pre-provisioned Vault paths |
| `--dry-run` | `false` | Generate values but skip Helm install |

Prerequisite operator versions are read from `deploy/operator-versions.env`.
When `cluster.airgapped` is true, or the chart directory sits inside an
airgapped bundle with sibling `charts/` and `manifests/` directories, the
installer uses the local bundled artifacts.

When ESO is selected during a normal `deploy`, the installer creates configured
KV v2 mounts when missing and fills absent keys in application, Git-token, and
per-site Vault paths. Existing values are preserved, so rerunning a deployment
does not rotate credentials. The provisioning token is resolved in this order:

1. `--vault-token-file`
2. `VAULT_TOKEN` or `OPENBAO_TOKEN`
3. The Kubernetes Secret named by token authentication, using its `token` key

The token must be allowed to inspect/create the configured mounts and read/write
their KV v2 paths. External credentials such as OIDC, Slack, Jira, and Git
tokens must be supplied in the installer configuration or already exist at the
configured Vault path; the installer will not invent integration credentials.
Use `--skip-vault-population` when those paths are managed separately; this also
avoids requiring a provisioning token for the deployment command.

### `nvcm-installer pvc-updater`

Use this command from automation after the NVCM GitOps application is healthy.
GitOps owns the PVC definitions; the updater only replaces their mutable
content. Jobs and template updates stage and transactionally replace the stored
files, then rollout-restart their consumers so the new content is loaded. ZTP
image updates instead publish image files before an atomic manifest replacement
and leave Network ZTP running. The updater never creates, resizes, or changes a
PVC. A missing PVC is an error.

```bash
# Custom jobs: restart Nautobot, Celery, and Celery Beat when content changed.
nvcm-installer pvc-updater jobs --namespace nv-config-manager \
  --release-name nv-config-manager --source ./custom-jobs

# Optionally run a custom job after its content is available and the workers are ready.
# The job runs even if the staged jobs content is unchanged.
nvcm-installer pvc-updater jobs --namespace nv-config-manager \
  --release-name nv-config-manager --source ./mock_topology \
  --run-job custom.mock_topology.jobs.mock_topology_design.MockTopologyDesign \
  --job-input '{"blueprint":"superpod","deployment_name":"site-1"}'

# Re-run a bundled or previously installed job without changing the jobs PVC.
nvcm-installer pvc-updater jobs --namespace nv-config-manager \
  --release-name nv-config-manager \
  --run-job nv_config_manager_jobs.bootstrap.load_bootstrap_data.LoadBootstrapData \
  --job-input '{}'

# Template plugins: restart the Render Service deployments when content changed.
nvcm-installer pvc-updater templates --namespace nv-config-manager \
  --release-name nv-config-manager --source ./template-plugins

# ZTP OS images: publish images and manifest.json without restarting Network ZTP.
nvcm-installer pvc-updater ztp --namespace nv-config-manager \
  --image cumulus-linux 5.13.0 ./cumulus-linux-5.13.0.bin
```

`templates` accepts one or more `--source` directories or tar archives, and
`ztp` accepts one or more `--image PLATFORM VERSION PATH` values. `jobs`
requires either one or more `--source` values, `--run-job MODULE.CLASS`, or
both. `--run-job` and `--job-input JSON_OBJECT` invoke one Nautobot job after
any requested jobs PVC update, or by themselves to re-run a bundled or
previously installed job. Use `--job-timeout` to override the 1,800-second
completion timeout. The PVC is mounted as the `custom` package to keep bundled
bootstrap jobs available from the image, so job classes supplied by a source
directory are named `custom.<source-package>...`.
Every `pvc-updater` subcommand accepts `--kubeconfig PATH`. When the flag is
omitted, the updater reads `KUBECONFIG` at runtime, including path-separated
multi-file values, before falling back to the standard kubeconfig location.
The default PVC names are `nautobot-custom-jobs`,
`render-service-template-plugins`, and `ztp-os-images`; each can be overridden
with `--pvc-name`. The updater calculates a content checksum, so unchanged
sources leave the PVC and workloads untouched.

---

## TUI Wizard

The wizard presents a sidebar navigation with the following sections. Use the mouse
or keyboard to navigate between them.

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| **F2** | Save configuration to disk |
| **F5** | Jump to Values Preview |
| **F9** | Jump to Deploy |
| **F10** | Save and exit |
| **Ctrl+C** | Quit (with confirmation dialog) |

### Navigation Sections

The sidebar uses visual indicators for section status:
- **Bold text** — currently selected section
- **Italic text with `*`** — section has incomplete required fields
- **Green tint** — section is complete
- **Yellow tint** — section needs attention
- Hover over a section for a tooltip with status details

#### 1. Cluster

![Cluster](../docs/assets/images/installer/01-cluster.svg)

Basic deployment settings and site definitions.

| Field | Description |
|-------|-------------|
| Hostname | Public DNS base domain for service endpoints (e.g., `config-manager.example.com`) |
| Environment | Environment label (`local`, `test`, `prod`, etc.) |
| Namespace | Kubernetes namespace for the deployment |
| Release Name | Helm release name |
| NVIDIA Config Manager Device Username | Device login username for NVIDIA Config Manager service account (default: `nv-config-manager`) |
| Deploy Size | Resource profile: `small`, `medium`, or `large` (see below) |
| Mock Device Interaction | Use mock network connections instead of real SSH/API calls (for Kind/CI) |

**Deploy size profiles:**

| Profile | Use Case | vCPU | RAM | Replicas |
|---------|----------|------|-----|----------|
| `small` | Local laptop / Kind | 8+ | 24 GB | 1 |
| `medium` | Remote VM / staging | 16+ | 64 GB | 1 |
| `large` | Production / HA | 96+ | 256 GB+ | 3+ |

Each profile selects a matching Helm values overlay (`values-local-small.yaml`,
etc.) that tunes CPU/memory requests and replica counts across all services.
See [Environment Sizing](../README.md#environment-sizing) in the main README
for full details.

**Sites** — Data centers managed by this NVIDIA Config Manager deployment. Each name must match
the slug of the corresponding Nautobot Location. Sites scope per-site network
secrets (device login credentials, BGP passwords, etc.) and each produces a
separate `config-secrets.ini` section. Per-site Vault paths (ESO mode) are
configured in [App Secrets](#4-app-secrets).

| Field | Description |
|-------|-------------|
| Name | Site slug — must match the Nautobot Location slug (e.g., `dc01`, `rno1`) |

#### 2. Services

![Services](../docs/assets/images/installer/02-services.svg)

Toggle individual NVIDIA Config Manager services on or off.

| Service | Description |
|---------|-------------|
| Render | Network configuration render service |
| ZTP | Zero Touch Provisioning |
| DHCP | DHCP server |
| Temporal | Temporal workflow engine |
| Config Store | Configuration storage API |
| Nautobot | Local Nautobot DCIM stack |

When Nautobot is disabled, configure an external Nautobot URL in the
[External Services](#3-external-services) section.

For an independently packaged DCIM provider, set `dcim.provider`,
`dcim.server`, and `services.nautobot: false` in the installer YAML. Provider
wheel images are configured with `dcim.provider_packages`, and
provider-specific non-secret settings are supplied through `dcim.options`.
The bundled TUI remains Nautobot-focused; a provider installer replaces that
screen while inheriting the rest of the wizard. See the external-provider
example in [Configuration Samples](../docs/install/configuration-samples.mdx).
Core Temporal workflows remain available for every selected DCIM provider; the
provider package supplies their normalized data operations.

See [Contribute a DCIM Provider](../docs/development/contributing-dcim-provider.mdx)
for provider packaging, provider-owned event handlers, render data, and test
expectations.

### Derived DCIM provider installers

An external provider should package its own installer and inherit the common
deployment and TUI implementations instead of copying their orchestration:

- Subclass `NVConfigManagerInstallConfig` for provider deployment fields, then
  select it with `CONFIG_MODEL` on both `Deployer` and the TUI application.
- Replace provider-owned panels through the TUI application's `SCREEN_CLASSES`.
  The deploy screen exposes `deployer_class()` / `create_deployer()` factories.
- Extend `Deployer.create_steps()`, `_local_image_builds()`,
  `_run_provider_pre_helm_install()`, and `_run_provider_post_helm_install()` to
  build, install, and bootstrap provider-owned pods around the common Helm
  release. Pass `--project-root` when provider image contexts live in a sibling
  repository.
- For DSX Air, subclass `SimConfig` and `SimOrchestrator`. The orchestrator has
  hooks for generated install YAML, simulation-manager construction, provider
  pre/post-deploy work, rendered-config readiness, deploy commands, and
  provider UI hostnames. A derived Air TUI replaces its topology/launch screens
  through `SCREEN_CLASSES`; the launch screen exposes `create_orchestrator()`.

Provider deployment belongs in these lifecycle hooks, not in progress
callbacks. This keeps the CLI and both TUIs on the same orchestration path.

#### 3. External Services

![External Services](../docs/assets/images/installer/03-external-services.svg)

Override in-cluster services with external instances. Leave disabled to use the
default in-cluster deployments.

**DCIM provider**

| Field | Description |
|-------|-------------|
| Provider name | `nautobot-2x` for the built-in provider, or the installed external provider's entry-point name |
| Use external DCIM | Toggle — disables the in-cluster Nautobot stack |
| DCIM endpoint | URL of the external provider; required when the provider is not the bundled `nautobot-2x` implementation |

**Redis**

| Field | Description |
|-------|-------------|
| Use external Redis | Toggle — disables the in-cluster Redis deployment |
| Host | Redis hostname |
| Port | Redis port (default: `6379`) |
| TLS / SSL | Enable TLS for the Redis connection |
| Password auth | Enable password authentication |

**PostgreSQL**

| Field | Description |
|-------|-------------|
| Use external PostgreSQL | Toggle — disables the in-cluster CNPG cluster |
| Port | PostgreSQL port (default: `5432`) |
| Temporal Host | Hostname for the Temporal database |
| Temporal Visibility Host | Hostname for the Temporal Visibility database |
| Config Store Host | Hostname for the Config Store database |
| DHCP Host | Hostname for the DHCP database |
| Nautobot Host | Hostname for the Nautobot database |

#### 4. App Secrets

![App Secrets](../docs/assets/images/installer/04-app-secrets.svg)

Secrets management backend, Git repository tokens, and data center sites.

**Secrets Method**

| Field | Description |
|-------|-------------|
| Method | `kubernetes` (in-cluster secrets) or `eso` (External Secrets Operator with Vault) |

When **ESO** is selected, additional Vault fields appear:

| Field | Description |
|-------|-------------|
| Vault Server | Vault URL |
| Vault Namespace | Enterprise Vault namespace |
| Secrets Path | Vault secrets engine path |
| Config Secrets Path | Separate path for config secrets (optional) |
| Auth Method | `jwt` or `token` |
| Mount Path | JWT auth mount path (e.g., `auth/kubernetes/prod`) |
| Role | JWT auth role name |
| Token Secret Name | K8s secret name for token auth |

**Vault Paths** — Each secret group maps to a Vault path. Toggle groups on/off and
customize paths to match your Vault layout. Application-secret rows show the logical
field, editable Vault key, and initial value in columns. Supported groups: Nautobot,
Redis, PostgreSQL, Network/Device Creds, Nautobot App, OIDC, Redfish, BMC, Slack, Jira,
CNPG Backup.

With `kubernetes`, values are edited in grouped Secret Values cards. With `eso`, values
are edited beside their Vault key mappings. Enter a password or token to use it as
the initial value. Empty generated credentials are created during installation, while
external integration credentials must be supplied explicitly. Vault population only
fills missing keys and never replaces an existing value.

Per-site Vault paths are optional. When omitted, the installer uses
`<environment>/site/<site>/config_secrets` under the configured config-secrets
mount.

**Git Tokens** — Add/remove Git repository tokens for Nautobot Git sync:

| Field | Description |
|-------|-------------|
| Name | Token identifier → creates `git-token-<name>` K8s secret |
| Token | Personal access token value |
| Username | Git username (optional) |
| Vault Path | Vault path for ESO (shown when ESO is selected) |

#### 5. Network Secrets

![Network Secrets](../docs/assets/images/installer/05-network-secrets.svg)

Free-form network protocol and workflow secrets written to `config-secrets.ini`.
Common entries (`hash_salt`, `bgp_password`, `root_password`, `api_user_key`) and
optional UFM credentials (`ufm_api_user`, `ufm_api_token_r1`) are pre-seeded on
first run. Remove the UFM entries for deployments without InfiniBand workflows.
Additional secrets are auto-discovered by scanning the bundled config contexts in
`config_contexts.yaml`, and can also be added manually or via template scanning.

| Field | Description |
|-------|-------------|
| Name | Human-readable label |
| Description | How the secret is used (auto-populated for discovered keys) |
| Secret Key | INI field name (and Vault key when using ESO) |
| Value | Secret value — leave empty to auto-generate |
| Rotation | Rotation suffix |
| Required | Whether the secret is mandatory |

The **Scan Plugin Templates** button inspects Jinja2 templates from Content plugin
paths to discover additional required secrets.

#### 6. Ingest Data

![Ingest Data](../docs/assets/images/installer/06-ingest-data.svg)

Custom Nautobot jobs and post-deploy job execution.
Path fields open an interactive directory picker (see below).

![Ingest Data File Picker](../docs/assets/images/installer/16-ingest-data-file-picker.svg)

| Field | Description |
|-------|-------------|
| Custom Jobs | Paths to job directories or tarballs (use `...` to browse) |
| Jobs PVC Storage Class | Kubernetes storage class for the Nautobot jobs PVC (optional, uses cluster default) |
| Jobs PVC Access Mode | `ReadWriteOnce` or `ReadWriteMany` for the Nautobot jobs PVC |
| Jobs Node Selector | Node label selector for loader and Nautobot pods that mount the jobs PVC |
| Post-Deploy Jobs | Nautobot jobs to run after deployment (class name + JSON input; local Nautobot only) |

> **Note:** Bundled bootstrap jobs are always available from the Nautobot image.
> Custom and post-deploy jobs require local Nautobot (`services.nautobot: true`). If
> Nautobot is set to remote, configure and run those jobs directly on that instance.

#### 7. Template Plugins

![Template Plugins](../docs/assets/images/installer/07-template-plugins.svg)

Jinja2 template plugin directories used by the Render service.
The node browser lets you pick a Kubernetes node label to pin plugin PVCs to a
specific node when running on a multi-node cluster without shared RWX storage.

![Template Plugins Node Browser](../docs/assets/images/installer/17-template-plugins-node-browser.svg)

| Field | Description |
|-------|-------------|
| Template Plugins | Paths to template plugin directories (use `...` to browse) |
| Node Selector | `key=value` label(s) to pin the plugin PVC to a specific node |

> **Note:** If a PVC is created as ReadWriteOnce, it can only mount on the node where
> it was first bound. Pin the pod to that node on multi-node clusters without shared
> NFS/RWX storage, or leave empty on single-node or shared-storage clusters.

#### 8. OS Images

![OS Images](../docs/assets/images/installer/08-os-images.svg)

ZTP OS image storage configuration.

**ZTP Storage type:**

| Type | Description |
|------|-------------|
| **File** (default) | Images stored in a PersistentVolumeClaim with a `manifest.json` |
| **S3** | Images stored in an S3/Ceph bucket; no PVC required |

When **File** storage is selected:

| Field | Description |
|-------|-------------|
| PVC Name | Name for the images PVC (default: `ztp-os-images`) |
| PVC Size | Storage request (default: `10Gi`) |
| Storage Class | Kubernetes storage class (optional, uses cluster default) |
| Access Mode | `ReadWriteOnce` (single-node, no NFS required) or `ReadWriteMany` (multi-node, requires NFS/RWX) |
| OS Images | Images to upload at deploy time (platform, version, file path) |

Each OS image entry has three fields:

| Column | Description |
|--------|-------------|
| Platform | Dropdown: Cumulus Linux, Arista EOS, NV-OS, or MLNX-OS |
| Version | Firmware version string (e.g., `5.14.0`) |
| File Path | Local path to the image binary (use `...` to browse) |

During deployment, the installer computes SHA256 checksums, creates the directory
structure (`{platform}/{version}/{filename}`), and writes a `manifest.json` to the
PVC root — matching the layout expected by the ZTP `FileStoreClient`. Images can
also be uploaded later via the ZTP `/v1/files` API.

#### 9. Workflows

![Workflows](../docs/assets/images/installer/09-workflows.svg)

Managed Temporal server sizing and workflow RBAC configuration.

Per-workflow overrides are selected from a dropdown of known workflows:

![Workflows Dropdown](../docs/assets/images/installer/18-workflows-dropdown.svg)

**Managed Temporal Server**

| Field | Description |
|-------|-------------|
| History Replicas | Optional History service pod count; blank uses the deployment size profile and chart default |
| History Shards | Optional persisted shard count; blank derives it from the History replica count |

> **Warning:** Blank History Shards follows History Replicas. After Temporal
> initializes its database, pin the current shard count before changing replicas.
> Temporal ignores shard-count changes after the first run; changing the persisted
> count requires migration to a separate Temporal cluster.

**RBAC**

| Field | Description |
|-------|-------------|
| Admin Roles | Roles with full admin access to all workflows |
| Default Read Roles | Default read access for all workflows |
| Default Execute Roles | Default execute access for all workflows |
| Per-Workflow Overrides | Override read/execute roles for specific workflows |

The workflow list is dynamically generated from the Helm RBAC values file. Per-workflow
overrides can be added via the "Add Override" button with a dropdown of available workflows.

#### 10. Container Images

![Container Images](../docs/assets/images/installer/10-container-images.svg)

Container image source and registry configuration.

| Field | Description |
|-------|-------------|
| Image Source | `local` (build locally) or `registry` (pull from registry) |
| Registry | Image registry prefix (default: `nvcr.io/nvidian/cfa`) |
| Tag | Default image tag (can be selected from registry or entered manually) |
| Pull Policy | Kubernetes pull policy (`IfNotPresent`, `Always`, etc.) |
| Registry Key | Registry credentials for authenticated registries (optional) |

When **Registry** is selected, you can fetch available tags from the Docker V2 API.
Tags are sorted with semver releases at the top, followed by release candidates, then
others.

Per-image overrides allow setting custom repository and/or tag for individual
components (`nv-config-manager`, `nv-config-manager-ui`, `nv-config-manager-kea`, `nv-config-manager-kea-admin`, `nv-config-manager-nautobot`,
`nv-config-manager-nats-ready`).

**Content-addressed local tags:** When building images locally, each image is
tagged with a short content hash derived from the Docker image ID (e.g.,
`nv-config-manager:a1b2c3d4e5f6`). This means Helm only triggers pod restarts when image
content actually changed, avoiding unnecessary restarts on re-deploy.

#### 11. SSO

![SSO](../docs/assets/images/installer/11-sso.svg)

OIDC Single Sign-On configuration. Provider-specific endpoint URLs are
auto-calculated based on the selected provider.

| Field | Description |
|-------|-------------|
| Enable SSO | Master toggle |
| Provider | `keycloak`, `azure`, or `generic` |
| Issuer URL | OIDC issuer URL |
| Client ID | OIDC client identifier |
| Client Secret | OIDC client secret |
| JWKS URI | Override for JWKS endpoint (auto-derived if empty) |
| Audiences | Comma-separated audience list (auto-populated per provider) |
| Scopes | Comma-separated scope list (auto-populated per provider) |

**Azure-specific behavior:** When Azure is selected, the installer auto-configures
`api://{client_id}/access` scope and dual audiences to force v2 access token issuance.

#### 12. SPIFFE

![SPIFFE](../docs/assets/images/installer/12-spiffe.svg)

SPIFFE identity framework configuration for service-to-service authentication.

| Field | Description |
|-------|-------------|
| Enable SPIFFE | Master toggle |
| Provider | `spire` or `teleport` |
| Auth Mode | `jwt` (JWT-SVID) or `mtls` |
| Trust Domain | SPIFFE trust domain (e.g., `example.com`) |
| Socket Mount Path | Volume mount path in pods |
| Socket File | Agent socket filename |
| Host Path | Host socket path (Teleport only) |

**Group Prefix Mappings** — Map SPIFFE ID prefixes to authorization groups.
Use "Auto-generate Default" to populate from the current namespace.

#### 13. Infrastructure

![Infrastructure](../docs/assets/images/installer/13-infrastructure.svg)

Gateway, TLS, database backups, monitoring, and load balancer configuration.

| Field | Description |
|-------|-------------|
| Create GatewayClass | Create the cluster-scoped `envoy-gateway` GatewayClass; disable when reusing a shared Envoy Gateway installation |
| Enable TLS | Self-signed TLS certificates for public endpoints |
| CNPG S3 Backup | Enable CloudNativePG Postgres backups to S3 |
| Monitoring | Enable PodMonitors and monitoring resources |
| Redis metrics | Run the exporter for bundled Redis; unavailable when external Redis is configured |

`infrastructure.monitoring.redis_metrics_enabled` stores the explicit Redis
metrics preference. For bundled Redis, it enables the Redis exporter _even when
monitoring is disabled_. When monitoring is enabled, the installer also enables
the Redis PodMonitor. The local observability stack automatically enables both
without changing the explicit preference. External Redis suppresses both
generated settings.
For a headless local deployment that runs only the exporter:

```yaml
cluster:
  environment: local
infrastructure:
  monitoring:
    enabled: false
    redis_metrics_enabled: true
```

**Load Balancer** — Select the provider for ZTP/DHCP device-facing services:

| Provider | Fields |
|----------|--------|
| **None** | Gateway uses NodePort (local dev) |
| **MetalLB** | Static IP per service, DNS name, allowed source prefixes |
| **Cilium** | Static IP per service, DNS name, allowed source prefixes |
| **AWS NLB** | Separate config for Gateway, ZTP, and DHCP NLBs |

**MetalLB / Cilium fields:**

| Field | Description |
|-------|-------------|
| ZTP LoadBalancer IP | Static IP for the ZTP service |
| ZTP DNS Name | External DNS hostname for ZTP (optional) |
| DHCP LoadBalancer IP | Static IP for the DHCP service |
| DHCP DNS Name | External DNS hostname for DHCP (optional) |
| Allowed Source Prefixes | CIDR list restricting LB ingress (add/remove entries) |

**AWS NLB fields** (configured independently for Gateway, ZTP, and DHCP):

| Field | Description |
|-------|-------------|
| LB Type | `external` or `internal` |
| Target Type | `ip` or `instance` |
| Name | AWS load balancer name |
| Security Groups | Comma-separated security group IDs |
| Subnets | Comma-separated subnet IDs |
| Static IPs | Comma-separated private IPs (optional) |
| DNS Name | External DNS hostname (optional) |

#### 14. Values Preview

![Values Preview](../docs/assets/images/installer/14-values-preview.svg)

Generate and inspect the complete Helm values YAML before deploying.

![Values Preview Generated](../docs/assets/images/installer/19-values-preview-generated.svg)

| Button | Action |
|--------|--------|
| Generate | Build values from current config |
| Write to File | Save generated values to the specified output path |

#### 15. Deploy

![Deploy](../docs/assets/images/installer/15-deploy.svg)

Full deployment orchestration with live monitoring.

**Deploy Options:**

| Option | Description |
|--------|-------------|
| Vault Token File | Provisioning token file used to create KV v2 mounts and fill ESO paths |
| Populate Vault Secrets | Disable when ESO paths are provisioned outside the installer |
| Build Images | Build Docker images locally (content-addressed tags) |
| Load Kind | Load built images into a Kind cluster |
| Kind Cluster | Kind cluster name (default: `nv-config-manager`) |
| Install Envoy Gateway | Install Gateway API CRDs and Envoy Gateway |
| Install cert-manager | Install cert-manager CRDs and Helm chart |
| Install CNPG | Install CloudNativePG operator |
| Helm Timeout | Helm install/upgrade timeout (default: `15m`) |
| Recreate Secrets | Force-recreate Kubernetes secrets |
| Run Integration Tests | Run integration tests after deployment |

**Dashboard panels:**

- **Step List** — Deployment progress with status indicators (pending/running/done/failed/skipped)
- **Pod Status** — Live pod status table with kubectl-style age display
- **Log Viewer** — Scrolling deployment log with tab buttons for switching to individual pod/container log streams; integration test output is streamed in real-time

---

## Configuration Reference

The configuration is stored in `nv-config-manager-install.yaml` with owner-only permissions
(`0600`). The file is added to `.gitignore` by default since it commonly contains
secrets.

### Minimal Example (local development)

```yaml
version: "1"
cluster:
  hostname: config-manager.local
  environment: local
  namespace: nv-config-manager
  airgapped: false
  mock_devices: true
  size: small
secrets:
  method: kubernetes
  config_manager_service_username: nv-config-manager
network_secrets:
  - name: Hash Salt
    secret_key: hash_salt
    required: true
  - name: BGP Password
    secret_key: bgp_password
    rotation: r1
    required: true
sites:
  - name: dc01
services:
  nautobot: true
```

### Managed Temporal Sizing

Both fields are optional. Omit them or set them to `0` to use the deployment
size profile and Helm chart behavior:

```yaml
services:
  temporal: true
temporal:
  history_replicas: 16
  num_history_shards: 128
```

`history_replicas` controls History service pods and can be adjusted with
capacity. `num_history_shards` initializes immutable cluster metadata and is
ignored after Temporal's first run. If an initialized deployment still derives
shards from replicas, confirm and pin its persisted shard count before changing
`history_replicas`. Changing the persisted count requires a separate-cluster
migration.

### File Storage Example (on-prem with OS images)

```yaml
version: "1"
cluster:
  hostname: config-manager.local
  environment: local
  namespace: nv-config-manager
  size: small
infrastructure:
  ztp_storage:
    type: file
    pvc_name: ztp-os-images
    pvc_size: 20Gi
    os_images:
      - platform: cumulus-linux
        version: "5.14.0"
        path: /images/cumulus-linux-5.14.0-mlx-amd64.bin
      - platform: mlnx-os
        version: "3.10.4000"
        path: /images/mlnx-os-3.10.4000.bin
secrets:
  method: kubernetes
sites:
  - name: dc01
services:
  nautobot: true
```

### Full Example (AWS with SSO)

```yaml
version: "1"
cluster:
  hostname: platform.config-manager.example.com
  environment: production
  namespace: nv-config-manager-prod
  release_name: nv-config-manager
  size: large
secrets:
  method: eso
  config_manager_service_username: nv-config-manager
  vault:
    server: https://vault.example.com
    namespace: engineering
    secrets_path: nv-config-manager/secrets
    mount_path: auth/kubernetes/prod
    role: nv-config-manager-vault-agent
    auth:
      method: jwt
sso:
  enabled: true
  provider: azure
  issuer_url: https://login.microsoftonline.com/{tenant}/v2.0
  client_id: your-client-id
  client_secret: your-client-secret
spiffe:
  enabled: true
  provider: spire
  auth_mode: jwt
  trust_domain: example.com
  group_prefixes:
    - spiffe://example.com/ns/nv-config-manager-prod=nv-config-manager-admin
infrastructure:
  load_balancer:
    provider: nlb
    nlb_gateway:
      type: external
      target_type: ip
      subnets: "subnet-abc123, subnet-def456"
      dns_name: "platform.config-manager.example.com,*.platform.config-manager.example.com"
    nlb_ztp:
      type: external
      target_type: ip
      name: nv-config-manager-prod-ztp-lb
      sg: "sg-111222, sg-333444"
      subnets: "subnet-abc123, subnet-def456"
      ips: "10.0.1.10, 10.0.1.20"
      dns_name: ztp-ext.platform.config-manager.example.com
    nlb_dhcp:
      type: external
      target_type: ip
      name: nv-config-manager-prod-dhcp-lb
      sg: "sg-111222, sg-555666"
      subnets: "subnet-abc123, subnet-def456"
      ips: "10.0.1.30, 10.0.1.40"
      dns_name: dhcp-ext.platform.config-manager.example.com
  cnpg_s3_backup:
    enabled: true
    bucket: nv-config-manager-postgres-backups
    path: production
    endpoint: https://s3.us-west-2.amazonaws.com
images:
  source: registry
  registry: nvcr.io/nvidian/cfa
  tag: v1.2.1
network_secrets:
  - name: Hash Salt
    secret_key: hash_salt
    source: vault
    required: true
  - name: BGP Password
    secret_key: bgp_password
    source: vault
    rotation: r1
    required: true
sites:
  - name: dc01
    vault_path: secrets/nv-config-manager/site/dc01/config_secrets
  - name: dc02
    vault_path: secrets/nv-config-manager/site/dc02/config_secrets
```

---

## Deployment Steps

When deploying (either via TUI or `nvcm-installer deploy`), the following steps
execute in order. Steps are automatically skipped when not applicable.

| # | Step | Description |
|---|------|-------------|
| 1 | **Prerequisites** | Verify `kubectl`, `helm`, cluster connectivity; optionally `docker`, `kind` |
| 2 | **Build Images** | `docker build` for all NVIDIA Config Manager images, tagged with content-addressed digests (skip if not requested) |
| 3 | **Load Kind** | `kind load docker-image` for each built image (skip if not requested) |
| 4 | **Install CRDs** | Gateway API CRDs, Envoy Gateway, cert-manager, CNPG operator (skip if not requested) |
| 5 | **Create Namespace** | Ensure the Kubernetes namespace exists |
| 6 | **Create Secrets** | Apply K8s secrets for database, Redis, Nautobot, NATS, devices, Git, registry, OIDC (skip if using ESO) |
| 7 | **Populate Vault** | Ensure configured KV v2 mounts exist and fill missing ESO secret values (skip unless ESO is selected) |
| 8 | **Setup Jobs PVC** | Create PVC and load custom Nautobot jobs (skip if none configured) |
| 9 | **Setup Templates PVC** | Create PVC and load template plugins (skip if none configured) |
| 10 | **Setup ZTP Images PVC** | Create PVC, upload OS images with proper directory structure and `manifest.json` (skip if storage type is S3 or no images configured; GitOps runs can use `pvc-updater ztp` for initial image publication without restarting Network ZTP) |
| 11 | **Generate Values** | Produce the combined Helm override YAML from config, secrets, and the selected size profile |
| 12 | **Helm Install** | `helm upgrade --install` with generated values |
| 13 | **Patch Gateway** | HostPort patch on Envoy Gateway for NodePort access (skip if LB is configured) |
| 14 | **Restart Nautobot** | Rolling restart of Nautobot workloads on re-run when jobs changed |
| 15 | **Restart Render** | Rolling restart of render service on re-run when templates changed |
| 16 | **Run Jobs** | Execute post-deploy Nautobot jobs via API (skip if none configured) |
| 17 | **Refresh Caches** | Restart config-store-cache and dhcp-refresh pods |
| 18 | **Run Tests** | Run integration tests from a ZTP pod with streamed output (skip if not requested or SSO enabled) |
| 19 | **Endpoints** | Display service URLs for all enabled services |

**Re-run intelligence:** The deployer detects existing deployments and content
checksums. On re-runs, it only restarts services when associated jobs or template
PVC content has actually changed, rather than blindly restarting everything.
For GitOps-managed file-backed ZTP images, `pvc-updater ztp` publishes initial
image content without restarting Network ZTP. Use the ZTP upload API for Day 2
image changes.

**INI checksum annotations:** The `nv-config-manager.ini` config secret includes a content
checksum in pod annotations, triggering automatic rolling restarts when INI
configuration changes.

---

## Template Secret Scanner

The installer can scan Jinja2 template plugins to discover required config secrets.
This is triggered from the **Network Secrets** screen via the "Scan Plugin Templates"
button.

The scanner performs static analysis of `.j2` files looking for:

| Pattern | Detection |
|---------|-----------|
| `"key"\|load_secret(...)` | Literal secret key references |
| `\|encrypt('ciscot7')` | Indicates `hash_salt_t7` is needed |
| `\|encrypt('...')` | Indicates `hash_salt` is needed |

Discovered secrets are merged with the existing network secrets list, avoiding
duplicates.

**Default secrets** (always pre-seeded by the installer):

| Secret Key | Required | Description |
|------------|----------|-------------|
| `hash_salt` | Yes | Password hashing salt for device accounts |
| `bgp_password` | Yes | BGP session authentication |
| `root_password` | Yes | Admin/root password for managed devices |
| `api_user_key` | Yes | NVIDIA Config Manager service account device credential |
| `ufm_api_user` | No | InfiniBand UFM API username |
| `ufm_api_token_r1` | No | InfiniBand UFM API token |

Additional secrets are auto-discovered from the bundled `config_contexts.yaml`
bootstrap data.

---

## Development

```bash
cd installer

# Install dev dependencies
uv sync

# Run tests
uv run pytest tests/ -v

# Lint
uv run ruff check src/
uv run ruff format --check src/

# Format
uv run ruff format src/

# Generate TUI screenshots
# Writes to ../docs/assets/images/installer by default
uv run python scripts/screenshot_tui.py

# Add SPDX license headers (from repo root)
cd ..
uv run python scripts/add_spdx_headers.py
```

### Project Structure

```text
installer/
├── src/nv_config_manager_installer/
│   ├── cli.py                 # Click CLI entry points
│   ├── schema.py              # Pydantic config models
│   ├── helm_values.py         # Helm values generation
│   ├── secrets.py             # Secret generation and ESO config
│   ├── accounts.py            # Bootstrap secret scanner and INI generation
│   ├── content.py             # Job/template PVC management
│   ├── deployer.py            # Deployment orchestration engine
│   ├── k8s.py                 # Kubernetes Python client helpers
│   ├── registry_client.py     # Docker V2 API for tag fetching
│   ├── template_scanner.py    # Jinja2 template secret discovery
│   └── tui/
│       ├── app.py             # Main Textual application
│       ├── app.tcss           # Textual CSS styles
│       ├── widgets/           # Custom widgets (LabeledSwitch)
│       └── screens/           # One screen per config section
│           ├── cluster.py
│           ├── services.py
│           ├── external_services.py
│           ├── vault.py       # App Secrets + Git tokens + Sites
│           ├── network_secrets.py
│           ├── content.py     # Ingest Data (jobs + post-deploy)
│           ├── render.py      # Template Plugins + node selector
│           ├── ztp.py         # OS Images (ZTP storage)
│           ├── workflow_rbac.py
│           ├── images.py
│           ├── sso.py
│           ├── spiffe.py
│           ├── infrastructure.py
│           ├── values_preview.py
│           ├── deploy.py
│           └── node_picker.py # NodePickerModal + NodeSelectorPanel
├── scripts/
│   └── screenshot_tui.py      # Headless TUI screenshot capture
├── tests/                     # pytest test suite
├── pyproject.toml
└── uv.lock
```
