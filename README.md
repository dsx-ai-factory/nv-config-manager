# NVIDIA Config Manager

[![Latest stable release](https://img.shields.io/github/v/release/dsx-ai-factory/nv-config-manager?display_name=tag&label=stable&sort=semver)](https://github.com/dsx-ai-factory/nv-config-manager/releases/latest)
[![Latest release candidate](https://img.shields.io/github/v/tag/dsx-ai-factory/nv-config-manager?filter=*-rc.*&label=rc&sort=date&color=orange)](https://github.com/dsx-ai-factory/nv-config-manager/tags)

NVIDIA Config Manager (NVCM) is an open-source network automation and configuration management platform for large-scale datacenter operations. It combines a pluggable DCIM provider, event-driven rendering, ZTP, DHCP, workflow automation, and configuration storage behind a single Helm deployment. Nautobot is the bundled reference provider and default deployment, not a core-service dependency.

NVCM is currently in Developer Preview and is not recommended for production use.

## Overview

| Service | Description |
| :------ | :---------- |
| **[ZTP](https://docs.nvidia.com/switch-infrastructure/config-manager/services/network-ztp/overview)** | Zero Touch Provisioning, boot scripts, OS image delivery, and provisioning status updates |
| **[DHCP](https://docs.nvidia.com/switch-infrastructure/config-manager/services/dhcp/overview)** | Kea DHCP configuration generation from selected DCIM-provider data |
| **[Temporal](https://docs.nvidia.com/switch-infrastructure/config-manager/services/temporal/overview)** | Long-running network operations and approval workflows |
| **[Render](https://docs.nvidia.com/switch-infrastructure/config-manager/services/render/overview)** | Template rendering from provider-neutral render data and provider-owned change events |
| **[Config Store](https://docs.nvidia.com/switch-infrastructure/config-manager/services/config-store/overview)** | PostgreSQL-backed rendered, intended, and backup configuration storage |
| **[UI](https://docs.nvidia.com/switch-infrastructure/config-manager/getting-started/which-interface-should-i-use)** | React/Next.js interface for workflows and configuration browsing |
| **[Nautobot](https://docs.nvidia.com/switch-infrastructure/config-manager/config-manager/nautobot)** | Bundled DCIM provider, custom jobs, and event publication |

## Installer

The [NVIDIA Config Manager Installer](installer/README.md) is the supported deployment entry point. It provides an interactive TUI and a headless CLI that both use the same `nv-config-manager-install.yaml` configuration file.

```bash
cd installer
uv sync
uv run nv-config-manager-installer init
```

Common non-interactive commands:

```bash
cd installer
uv run nv-config-manager-installer validate ../deploy/configs/local-superpod.yaml
uv run nv-config-manager-installer generate-values ../deploy/configs/local-superpod.yaml --output-dir ../generated
uv run nv-config-manager-installer deploy ../deploy/configs/local-superpod.yaml \
  --image-source local \
  --build-images \
  --load-kind \
  --kind-cluster nv-config-manager \
  --install-envoy-gateway \
  --install-cnpg-operator \
  --install-cert-manager
```

The installer handles Helm values generation, Kubernetes secrets, optional operator installation, image builds, Kind image loading, content PVC staging, ZTP OS image staging, post-deploy Nautobot jobs, and endpoint reporting.

## Local Development Setup

Install project tools and dependencies before running tests or local deployments.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync --dev
./scripts/install-hooks.sh
```

### Git Hooks

Install the repository hooks after cloning and whenever hook scripts change:

```bash
./scripts/install-hooks.sh
```

Installed hooks:

- `pre-commit`: checks opted-in OpenPGP, SSH, or X.509/S/MIME signing, formats
  all staged Python files outside ignored/generated directories with
  `uv run ruff format`, re-stages those files, and checks SPDX license headers
  for supported source files (`.py`, `.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`,
  `.cjs`, and `.go`) under `src/`, `ui/src/`, `ui/tests/`, `components/`,
  `db/migrations/`, `scripts/`, `development/`, `installer/src/`,
  `installer/tests/`, and `installer/scripts/`.
- `commit-msg`: rejects commits that do not include a valid DCO
  `Signed-off-by: Name <email>` trailer. Use `git commit -s` or
  `git commit --amend -s` to add the trailer automatically.

The hook installer reports signing readiness only when repository-local signing
settings are present. Trustees need GitHub-verified commits for automatic sync
and should follow the internal signing setup guidance. Other contributors and
draft pull requests instead need an authorized maintainer to approve the exact
commit with `/ok to test <sha>`; they are not prompted to configure a signing key.

See [Cryptographically Signing Commits](CONTRIBUTING.md#cryptographically-signing-commits)
for the trustee and non-trustee workflows.

The local signing check is advisory. GitHub and copy-pr-bot make the authoritative
decision about signature verification and protected CI branch creation.

Local hooks can be skipped with `git commit --no-verify`; maintainers must still
ensure every commit satisfies the DCO before accepting a contribution.

For UI work:

```bash
cd ui
npm install
```

Validation commands:

```bash
uv run pytest
uv run ruff check src/
uv run mypy src/
uv run ruff format src/
```

## Kubernetes Deployment

Prerequisites:

- Docker or a compatible container runtime
- Kind for local clusters, or a reachable Kubernetes cluster
- Helm 3.x
- kubectl configured for the target cluster
- Python 3.11 or newer, managed through `uv`
- Node.js 23 or newer for UI development

### Resource Profiles

Resource sizing is selected in installer config at `cluster.size`.

| Size | Environment | Suggested Capacity | Notes |
| ---- | ----------- | ------------------ | ----- |
| `small` | Local laptop or CI | 8 vCPU, 24 GB RAM | Single replica development profile |
| `medium` | Remote VM or staging | 16 vCPU, 64 GB RAM | Larger requests for a shared development VM |
| `large` | Production | 96 vCPU, 256 GB RAM, 3+ nodes | HA-oriented replica counts and resource requests |

The bundled profiles in `deploy/configs/` are ready-to-run examples. Override the selected profile with `cluster.size` in a copied installer config.

### Local Kind Deployment

```bash
make kind-up
```

The default `make kind-up` target creates the Kind cluster if needed, builds local images, loads them into Kind, installs required operators, and deploys using `deploy/configs/local-superpod.yaml`.

Populate or refresh mock topology data:

```bash
make topology
```

### Local Hostnames

The local SuperPOD profile uses `config-manager.local` as the base hostname. Add these entries when accessing the Envoy Gateway directly from your workstation:

```text
127.0.0.1 config-manager.local
127.0.0.1 nautobot.config-manager.local
127.0.0.1 render.config-manager.local
127.0.0.1 ztp.config-manager.local
127.0.0.1 dhcp.config-manager.local
127.0.0.1 workflow.config-manager.local
127.0.0.1 config-store.config-manager.local
127.0.0.1 temporal.config-manager.local
127.0.0.1 mcp.config-manager.local
127.0.0.1 svc-mcp.config-manager.local
127.0.0.1 svc-workflow.config-manager.local
127.0.0.1 svc-config-store.config-manager.local
127.0.0.1 svc-render.config-manager.local
127.0.0.1 svc-ztp.config-manager.local
127.0.0.1 svc-dhcp.config-manager.local
127.0.0.1 svc-nautobot.config-manager.local
```

Local endpoints:

- UI: <https://config-manager.local>
- Nautobot: <https://nautobot.config-manager.local>
- Workflow API: <https://workflow.config-manager.local>
- Config Store API: <https://config-store.config-manager.local>
- MCP endpoint: <https://mcp.config-manager.local/mcp>

For the local SuperPOD profile, Nautobot login is `admin` / `admin`. For generated credentials:

```bash
kubectl get secret nautobot-admin -n nv-config-manager -o jsonpath='{.data.password}' | base64 -d && echo
kubectl get secret nautobot-admin -n nv-config-manager -o jsonpath='{.data.api_token}' | base64 -d && echo
```

### Connecting Claude Code MCP (`kind-up-sec`)

The `kind-up-sec` environment includes the MCP server. To connect Claude Code:

1. Install the local gateway CA.

   ```bash
   make install-cert
   ```

   The command is restricted to the current local Kind cluster. It trusts the CA in the system and Chrome/Chromium stores, and saves a PEM copy for Node.js at `~/.config/nv-config-manager/certs/config-manager.local-ca.crt`.

2. Add the MCP server.

   ```bash
   DISCOVERY=$(curl -s https://config-manager.local/auth/discovery)
   CLIENT_ID=$(printf '%s' "$DISCOVERY" | jq -r '.clientId')
   MCP_URL=$(printf '%s' "$DISCOVERY" | jq -r '.services.mcp')

   claude mcp add --transport http --client-id "$CLIENT_ID" nv-config-manager "$MCP_URL"
   ```

   Using `--client-id` uses the pre-registered `nv-config-manager-cli` public client and avoids OAuth Dynamic Client Registration, which is disabled in local Keycloak.

3. Authenticate.

   ```bash
   NODE_EXTRA_CA_CERTS="$HOME/.config/nv-config-manager/certs/config-manager.local-ca.crt" \
     claude mcp login nv-config-manager
   ```

Then, log in with any pre-configured local Keycloak account: `nvcm-admin` / `nvcm-admin`, `nvcm-network` / `nvcm-network`, or `demo` / `demo`.

## Makefile Commands

```bash
make kind-up                  # Create a Kind cluster and deploy the platform
make kind-up-with-topology    # Alias for kind-up when the config runs topology jobs
make topology                 # Run configured mock topology jobs against an existing deployment
make kind-down                # Delete the Kind cluster
make local-up                 # Deploy to the current Kubernetes context
make local-down               # Remove the Helm release and namespace
make local-status             # Show pods, services, and gateway state
make local-logs               # Tail deployment logs
make port-forward             # Forward Nautobot and Temporal UI locally
make docker-build             # Build all local container images
make test                     # Run Python tests
make lint                     # Run Python linters and type checks
make openapi                  # Regenerate OpenAPI specs
make openapi-check            # Check OpenAPI specs are current
make go-bindings              # Regenerate Go clients from committed OpenAPI specs
make api-generate             # Regenerate OpenAPI specs and Go clients together
make docs-lint                # Lint documentation markdown
make docs-lint-fern           # Validate Fern docs configuration
```

## Air-Gapped Deployment

Air-gapped bundles are built from `deploy/airgapped/create-airgapped.sh`. The bundle contains the Helm chart, container images, dependency charts and manifests, image loader manifests, operator version pins, the offline installer package, and an OCI registry upload helper.

On an internet-connected build host:

```bash
cd deploy/airgapped
export NGC_API_KEY="your-ngc-api-key"
./create-airgapped.sh --version v1.0.0 --arch amd64
```

On the target environment, upload the images and packaged chart to an OCI-compliant registry first:

```bash
tar -xzf nv-config-manager-airgapped-v1.0.0-amd64.tar.gz
cd nv-config-manager-airgapped-v1.0.0-amd64
./upload-to-registry.sh \
  --registry registry.example.com/nv-config-manager \
  --chart-registry registry.example.com/nv-config-manager/charts \
  --username '<user>' \
  --password-stdin
```

Then install from the bundled chart and offline installer:

```bash
./installer/install.sh
./installer/nv-config-manager-installer init --config install.yaml
./installer/nv-config-manager-installer deploy install.yaml \
  --chart-dir helm \
  --image-source registry \
  --install-envoy-gateway \
  --install-cert-manager \
  --install-cnpg-operator
```

For demos or clusters without a registry, preload image tarballs onto the target nodes instead:

```bash
./manifests/load-airgapped-images.sh ./images --daemonset
```

See [Air-Gapped Installation](docs/install/install-airgapped.mdx) for the full offline workflow.

## Repository Structure

```text
nv-config-manager/
├── src/nv_config_manager/       # Python services and shared libraries
├── src/tests/                   # Python test suites
├── ui/                          # React/Next.js UI
├── components/                  # Standalone DCIM SDK/providers, template library, and service assets
├── development/mock_topology/   # Local development topology job data
├── installer/                   # Interactive and headless installer package
├── deploy/helm/                 # Helm chart and values overlays
├── deploy/airgapped/            # Offline bundle tooling
├── docs/                        # Fern documentation site and generated API specs
├── build/                       # Dockerfiles
├── db/                          # Alembic migrations
├── Makefile                     # Development commands
└── pyproject.toml               # Python project configuration
```

## Service Architecture

```text
Envoy Gateway / Ingress
  |-- UI
  |-- Nautobot
  |-- Workflow API and Temporal UI
  |-- Render API
  |-- Config Store API
  |-- ZTP and DHCP device-facing services

Selected DCIM provider -- provider-owned events --> NATS JetStream --> Render consumers
Render --> Config Store
ZTP and DHCP --> selected DCIM provider and Config Store
Temporal workers --> selected DCIM provider, Render, Config Store, and managed devices
```

## Testing

```bash
make test
make test-cov
uv run pytest src/tests/ztp/
uv run pytest src/tests/temporal/
uv run pytest src/tests/render/
make lint
make format
```

Integration tests require a running deployment:

```bash
make test-integration
uv run pytest src/tests/integration/ -v \
  --nv-config-manager-namespace nv-config-manager \
  --base-hostname config-manager.local
```

With real OIDC authentication:

```bash
uv run pytest src/tests/integration/ -v \
  --base-hostname qa.config-manager.example.com \
  --sso
```

## Configuration

Runtime service configuration is delivered through the `nv-config-manager-ini` Kubernetes secret. The installer generates the secret content from `nv-config-manager-install.yaml`, selected size profile overlays, and generated or user-supplied secrets.

The selected DCIM provider is configured with `dcim.provider`; its package is
discovered through the `nv_config_manager.dcim` Python entry-point group. NVCM
parses deployment configuration and passes a provider-owned settings mapping to
the SDK. See [Contribute a DCIM Provider](docs/development/contributing-dcim-provider.mdx) for the provider contract and [Configuration Samples](docs/install/configuration-samples.mdx#external-dcim-provider) for deployment wiring.

OpenAPI specs live in [docs/api-specs](docs/api-specs/README.md). Run `make openapi-check` before changing API handlers.

## Go API Bindings

Generated Go clients for the Temporal, Config Store, ZTP, Render, and DHCP APIs live in
[`bindings/go`](bindings/go/README.md). Install a specific platform release with:

```bash
go get github.com/nvidia/nv-config-manager/bindings/go@v1.3.0
```

Each service is a separate package. For example, the Temporal client uses the generated request
builder and bearer-token context:

```go
import (
    "context"

    "github.com/nvidia/nv-config-manager/bindings/go/temporal"
)

ctx := context.WithValue(context.Background(), temporal.ContextAccessToken, accessToken)
configuration := temporal.NewConfiguration()
client := temporal.NewAPIClient(configuration)
request := client.WorkflowAPI.GetWorkflowsV1WorkflowGet(ctx)
response, httpResponse, err := request.Execute()
```

CLI and machine clients use a bearer JWT by default. Explicit health, readiness, metrics, and
Temporal codec endpoints remain public; ZTP device endpoints also support device-IP authorization.
Deployments can disable authentication enforcement with `[auth] required = false`.

Run `make api-generate` after changing API handlers. Public CI runs the same command and fails with
a PR comment when committed specifications or bindings are stale.

## Releases and Roadmap

- Release notes are tracked in [CHANGELOG.md](CHANGELOG.md).
- Stable releases are published from release tags through protected workflows
  in `.github/workflows/`. Release candidates remain available as Git tags.
- Roadmap and planning details are tracked through project issues and maintainer
  planning until a public roadmap is published.

## Authentication

The shared `nv_config_manager.common.auth.OIDCAuth` helper implements browser-based OIDC PKCE for CLIs, tests, scripts, and notebooks.

```python
from nv_config_manager.common.auth import OIDCAuth

auth = OIDCAuth.discover_from_gateway(
    "https://workflow.qa.config-manager.example.com/whoami",
    verify=False,
)
token = auth.get_access_token()
```

The `svc-*` hostnames, such as `svc-workflow.<base-hostname>`, accept bearer tokens directly and are intended for CLI and machine access.

## Component Documentation

- [Fern docs source](docs/README.md)
- [Installer](installer/README.md)
- [Architecture](docs/overview/architecture.mdx)
- [ZTP](docs/network-ztp/index.mdx)
- [DHCP](docs/dhcp/index.mdx)
- [Temporal](docs/temporal/index.mdx)
- [Render](docs/render/index.mdx)
- [Config Store](docs/config-store/index.mdx)
- [Remote MCP](docs/overview/mcp.mdx)
- [UI and API interfaces](docs/getting-started/interfaces.mdx)
- [Nautobot](docs/nautobot/index.mdx)
- [Device Authentication](docs/overview/device-authentication.mdx)
- [Observability](docs/overview/observability.mdx)
- [Local Development Quick Start](docs/getting-started/local-development-quick-start.mdx)
- [Air-Gapped Installation](docs/install/install-airgapped.mdx)
- [Contribute a DCIM Provider](docs/development/contributing-dcim-provider.mdx)

## Separately Installable Components

These components are consumed from this repository by Git or sibling checkout
until the publishing story is finalized:

- `nv-config-manager-dcim`: Provider-neutral SDK for DCIM integrations
- `nv-config-manager-dcim-nautobot-2x`: Nautobot reference implementation
- `nv-config-manager-templates`: Network configuration Jinja2 templates
- `nautobot-plugin-nv-config-manager`: Nautobot plugin for NVIDIA Config Manager integration
- `nautobot-broker-nats`: NATS event broker for Nautobot

## Contributing

Start with [CONTRIBUTING.md](CONTRIBUTING.md). Contributions must follow the
Developer Certificate of Origin sign-off process described there.

1. Fork the repository.
2. Create a feature branch.
3. Make changes.
4. Install local hooks with `./scripts/install-hooks.sh`.
5. Run tests and linting with `make test lint`.
6. Submit a pull request using the repository PR template.

Please also follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Governance and Maintainers

- Governance: [GOVERNANCE.md](GOVERNANCE.md)
- Maintainers: [MAINTAINERS.md](MAINTAINERS.md)
- Support: [SUPPORT.md](SUPPORT.md)

## Security

Do not report security vulnerabilities through public GitHub issues. Follow the
private disclosure process in [SECURITY.md](SECURITY.md).

## Citation

Citation guidance is available in [CITATION.md](CITATION.md).

## License

NVIDIA Config Manager is licensed under [Apache 2.0](LICENSE). See
[NOTICE](NOTICE) for attribution notices.
