# Changelog

Notable user-facing changes for NVIDIA Config Manager should be recorded here.

This project uses release tags for published versions. Add entries under
`Unreleased` while changes are in flight, then finalize them under the release
version before the selected release candidate is promoted.

## Unreleased

### Added

- DHCP container probes (liveness, readiness, startup) are now configurable per
  container (`kea`, `healthcheck`, `api`, `confgen`) through
  `networkDhcp.probes` in the Helm values. Any Kubernetes probe field can be
  set, probes can be disabled with `enabled: false`, and off-by-default probes
  can be enabled. Defaults render exactly as before.

### Fixed

- Installer content hashes no longer depend on wall-clock time, preventing
  unchanged jobs and templates from being detected as changed across runs.
- The AIR Hyper-reach fixture now installs its LLDP host in a chassis DeviceBay
  and advertises the chassis name, enabling parent-chassis validation in simulation.

## 1.4.0

### Added

- Added a pluggable DCIM interface with a bundled Nautobot provider, typed
  location references, and pluggable template render data. Provider authors can
  use the DCIM contribution guide without modifying the platform services.
- Extracted reusable device and infrastructure clients, logging, workflow
  contracts, activities, and built-in workflows into installable workspace
  packages. Workflow plugins register through the
  `nv_config_manager.workflows` entry-point group.
- Added plugin scheduler registration and a shared scheduler host. Select
  schedulers explicitly with `temporal.scheduler.schedulers`; the default
  `builtin.backup` entry reconciles backups for the Nautobot provider.
- Added Juniper Junos device support for configuration operations, cable
  validation, password rotation, diagnostics, and OS upgrades.
- Expanded Arista EOS support with running-image discovery, technical-support
  archives, and environment, transceiver, interface-counter, and interface-MAC
  diagnostics.
- Added Redis exporter support and DHCP reconciliation metrics and structured
  logs, including a UI indication when DHCP configuration becomes stale.
- Added `GET /v1/workflow/{form_id}/form`, keyed by each form-enabled workflow's
  explicit, stable `workflow_form_id`, which returns a version 1 form envelope:
  a form projection of its input schema (`schema`), a
  validated RJSF `ui_schema`, `ui_schema_version`, the UI capabilities the form
  `requires`.
- Added the opt-in `GET /v1/workflow/metadata?include=form` representation with
  each workflow's launcher `group`, stable `form_id`, and `has_form` status. The
  request without `include=form` retains its previous response shape for
  compatibility with existing generated clients.
- Workflow plugins can declare launcher forms on their input models with an
  `rjsf_ui_schema` class variable, the `api_options`, `device_field`, and
  `location_field` core-field helpers, the `variant_rows` composite row helper,
  and the `ServerOwned`, `FormExcluded`, and `FormSchema` field markers from
  `nv_config_manager_workflows.ui`. See the workflows package README.
- Form declarations can preserve mutually exclusive input modes and numeric
  cross-field checks with `exclusiveGroups` and `fieldComparisons`; shipped
  multi-select links may retain a server-owned legacy query separator.
- The workflow API validates form declarations in a form catalog separate from
  the execution registry. An invalid built-in form fails API startup and CI form
  validation without preventing Temporal workers, schedulers, MCP, or the CLI
  from starting. An invalid third-party plugin form keeps the workflow and its
  execution endpoint available: `/form` returns HTTP 503 with error code
  `workflow_form_unavailable`, and the launcher shows a generic unavailable
  message. The sanitized validation detail remains available in API startup
  logs, the form catalog, and the plugin-validation CLI.

### Changed

- Corrected Go module paths and imports to use the repository's
  `github.com/dsx-ai-factory/nv-config-manager` location. Go bindings consumers
  must update imports and select a release containing the corrected module path.
- Scheduled and syslog-triggered backups fail after activity retries instead
  of waiting indefinitely for a manual stage retry. Interactive and child
  backups retain their retry controls unless `terminate_on_failure` is set.
- Backups for backup-enabled devices with rendering disabled skip the intended
  configuration drift check. Render-enabled devices still need an intended
  configuration in the Config Store.
- Render updates select enabled devices according to aggregate ownership;
  disabled devices are excluded from template-triggered updates. VLAN
  relationship changes also trigger configuration rendering.
- Cumulus ZTP retrieves rendered startup configuration over SFTP. Allow TCP
  2222 from device subnets to the ZTP service in addition to the HTTP or HTTPS
  bootstrap path.
- Spectrum-X overlay assignment can remove selected port assignments without
  adding a replacement and cleans up unused device-level VRF associations.
- Gateway monitoring covers configured browser and JWT-only service routes;
  bundled NATS is monitored through its in-cluster HTTP endpoint.
- Workflow launcher forms are rendered with RJSF from the `/form` envelope.
  The UI shows a "needs a newer UI" state for a form version or capability it
  does not support, instead of rendering a partial form. Existing workflow
  request schema shapes and Pydantic validation rules are unchanged: form
  declarations do not change API request models, MCP tool schemas, or the
  corresponding generated-client models.
- Deploy the workflow API before the UI when upgrading to the schema-driven
  browser forms. A new UI paired with an older API intentionally disables
  browser form links and shows an API-upgrade message without probing form
  endpoints; API and CLI workflow execution remain available.
- The workflow HTTP API now treats fields marked `ServerOwned` as authoritative
  request identity: it replaces submitted `user` values with values derived
  from the authenticated request. Built-in backup inputs keep `user_domain`
  `FormExcluded`: the API preserves a non-empty caller value and derives the
  legacy fallback when the value is missing, null, or empty. The Port LLDP Info
  endpoint also rejects incomplete or mixed lookup methods, and the SpX Overlay
  Creation endpoint rejects a route-distinguisher range unless
  `rd_min < rd_max`. These checks run after request-model validation and before
  a workflow starts; they do not change the request schema or Temporal replay
  deserialization.
- Canonicalization failures from workflow HTTP endpoints now return HTTP 422
  responses using the documented FastAPI `HTTPValidationError` envelope. The
  `detail` value is a validation-error list rather than the previous string.

### Fixed

- DHCP sync and refresh processes mount the INI directory so ConfigMap updates
  remain visible. Lease summaries read active leases from the shared database,
  and reconciliation tolerates reads cancelled by a Nautobot replica.
- UI, Nautobot, and Temporal UI health probes bypass the OIDC login redirect.
- Fixed Arista confirmed-commit sequencing, connected-host discovery without a
  tenant, and the Nautobot alias field used by host metadata workflows.
- Fixed Config Store comparison statistics and diff spacing, and ZTP serial
  validation when no matching device exists.
- Improved installer recovery from transient Air setup timeouts, local-source
  upload, and inventory-only device handling.
- Services with tracing enabled no longer leak memory on every outbound aiohttp
  session. With no OpenTelemetry meter provider configured, the aiohttp
  instrumentation registered a new meter on the global proxy provider for each
  `ClientSession`, and the proxy provider never releases them. The DHCP API
  opens a session for every healthcheck, so it grew about 20MiB a day and was
  OOM-killed every one to three days. The HTTP client instrumentors now use a
  no-op meter provider.
- The DHCP `api` container's default memory request and limit
  (`networkDhcp.resources.api`) go from 128Mi to 256Mi. A fresh pod already
  used about 105Mi of the old limit.
- Render consumers on bundled NATS now re-create their stream when it is
  missing, instead of retrying `stream not found` until restarted. A NATS pod
  rescheduled after nats-box started came back without JetStream state, and
  nothing re-ran stream setup. Runtime-created streams now use the configured
  names, subjects, and the same limits as nats-ready.
- nats-ready no longer logs NATS passwords or token-only credentials in the
  server address.
- Nautobot upgrades no longer wipe nv-config-manager's Redis data. The Nautobot
  Django cache shared Redis database 0 with nv-config-manager, and
  `nautobot-server post_upgrade` clears that cache with `FLUSHDB`. Every
  Nautobot web pod runs it on start, so each deploy deleted the published KEA
  DHCP config several times, and DHCP sidecars waited up to one refresh
  interval for it to come back. The Nautobot cache now uses its own database,
  set by `externalServices.redis.nautobotCacheDb` (default 1, Nautobot's
  upstream cache database). On a shared
  external Redis, set it to a database nothing else uses. The chart rejects a
  value equal to `externalServices.redis.db` or `lockDb` when it deploys
  Nautobot. After upgrading, delete the leftover `:1:nautobot*` keys from
  database 0; see "Upgrading From an Earlier Release" in the Nautobot
  integration docs.

### Security

- Updated Python, UI, Temporal, and container dependencies, and restricted
  internal service authentication to the SPIFFE identity path.

## 1.3.1

### Added

- Added a DHCP lease API and dashboard with searchable, paginated views of
  leases, reservations, pools, and subnet data.
- Added site configuration backup and live configuration-diff workflows,
  including bulk device selection, child-workflow result reporting, and
  workflow action auditing.
- Added YAML-driven Nautobot group-to-role mappings for JWT-authenticated users
  and gated integration coverage for the RBAC configuration.
- Added OpenTelemetry tracing and metrics across services and Temporal
  workflows, plus optional NATS exporter monitoring for Nautobot messaging.
- Added support for kgateway, external Temporal deployments, image digests,
  Ceph-backed ZTP storage, and installer-managed Vault/OpenBao and PVC content.

### Changed

- Improved workflow and device UI filtering, sorting, site context, forms,
  status details, accessibility, and error presentation.
- Expanded the Spectrum-X AIR topology, templates, examples, and end-to-end
  switch-management documentation.
- Improved installer behavior for local images, secrets, storage staging,
  deployment updates, and Python 3.13 environments.

### Fixed

- Fixed a Nautobot uWSGI self-deadlock on `/metrics/` by running
  `prometheus_client` in multiprocess mode, and scrape Nautobot via `/metrics/`
  instead of `/metrics` to avoid recurring 404s. Nautobot metrics now aggregate
  across all uWSGI workers instead of reporting only the worker that answered
  the scrape. The multiprocess metrics directory is an `emptyDir` with a size
  limit so it starts empty on every pod start and cannot grow without bound,
  and the Celery probes no longer leave metric files behind on each check.
- Fixed multi-deploy result duplication, backup persistence reporting,
  intended-configuration validation, workflow locking, and several workflow
  replay and child-workflow edge cases.
- Fixed ZTP streaming and storage handling, including download hardening and an
  ONIE provisioning race condition.
- Fixed authentication and authorization edge cases involving JWKS caching,
  SPIFFE path matching, in-cluster Nautobot requests, and Temporal codec CORS.
- Fixed Helm deployment issues involving Temporal image credentials, Redis
  update strategy, monitoring namespaces, and Nautobot readiness checks.

### Security

- Added CodeQL and secret scanning, a secret-free build stage for untrusted pull
  requests, and repository guidance and hooks for cryptographically signed
  commits.
- Hardened installer archive extraction, ZTP download streaming, shell tooling,
  and container images, and updated vulnerable application and UI dependencies.

## 1.3.0

### Added

- Added an MCP server for NVIDIA Config Manager, including workflow tools,
  standard OAuth discovery endpoints, and a Microsoft Entra ID compatibility
  proxy for native MCP clients.
- Added an installer-driven NVIDIA AIR simulation workflow with interactive and
  headless operation, topology setup, image selection, launch progress, and
  troubleshooting documentation.
- Added InfiniBand PKey creation and membership workflows and a port GUID
  discovery workflow, with UI forms, UFM integration, GUID normalization, drift
  handling, and user guides.
- Added SpX Overlay creation, assignment, deletion, and tenant-change workflows
  with UI forms, VRF-to-VXLAN associations, namespace-aware behavior,
  route-distinguisher allocation, and user guides.
- Added generated Go clients for the config-store, DHCP, render, Temporal, and
  ZTP APIs, with generation and contract-drift checks in CI.
- Added an optional local observability stack with Prometheus, Alloy,
  PodMonitors, probes, alerting rules, and an NVIDIA Config Manager dashboard.
- Added Nautobot bootstrap support for custom fields, including NICo interface
  identifiers and related status metadata.

### Changed

- Refactored the workflow UI with server-side filtering, pagination, clearer
  labels and authentication errors, and updated workflow forms.
- Expanded the UI authentication controls with a logout option and clearer
  unauthorized-user states.
- Expanded the SuperPOD and AIR example topologies, Cumulus Linux 5.16.1
  templates, and UFM development fixtures.
- Published the documentation site from the public repository and expanded the
  installation, API, authentication, observability, MCP, workflow, and AIR
  simulation guides.
- Improved air-gapped packaging and registry upload support, including
  architecture-specific bundles and bundled dependency metadata.

### Fixed

- Fixed workflow replay and state handling, including nullable fields, newly
  added search attributes, and preservation of pending tenant-deploy state.
- Fixed Helm deployment edge cases involving shared GatewayClasses, monitoring
  namespaces and ports, DHCP liveness checks, OIDC secrets, and cluster-local
  Temporal service names.
- Fixed authentication handling for Temporal codec endpoints.

### Security

- Updated application dependencies, Go tooling, Stork, and distroless runtime
  images to incorporate current security fixes.
- Hardened CI pipelines and improved static-analysis accuracy and coverage.

## 1.2.3

### Security

- Fixed a regression in default authentication behavior introduced when consolidating
  service authentication into the shared mono-repo auth library. When SSO is enabled,
  service endpoints now require authentication by default, with health checks and
  metrics as explicit unauthenticated exceptions.
- Added Envoy header removal for spoofable identity headers, including
  `ssl-client-cert` and `X-Auth-*` headers, so legacy mTLS auth paths cannot be
  reached by forging client-supplied headers.

## 1.2.2

- Official OSS release.
