# Changelog

Notable user-facing changes for NVIDIA Config Manager should be recorded here.

This project uses release tags for published versions. Add entries under
`Unreleased` while changes are in flight, then finalize them under the release
version before the selected release candidate is promoted.

## Unreleased

### Fixed

- Render consumers on bundled NATS now re-create their stream when it is
  missing, instead of retrying `stream not found` until restarted. A NATS pod
  rescheduled after nats-box started came back without JetStream state, and
  nothing re-ran stream setup. Runtime-created streams now use the configured
  names, subjects, and the same limits as nats-ready.
- nats-ready no longer logs NATS passwords or token-only credentials in the server address.
- Nautobot upgrades no longer wipe nv-config-manager's Redis data. The Nautobot
  Django cache shared Redis database 0 with nv-config-manager, and
  `nautobot-server post_upgrade` clears that cache with `FLUSHDB`. Every
  Nautobot web pod runs it on start, so each deploy deleted the published KEA
  DHCP config several times, and DHCP sidecars waited up to one refresh
  interval for it to come back. The Nautobot cache now uses its own database,
  set by `externalServices.redis.nautobotCacheDb` (default 2). On a shared
  external Redis, set it to a database nothing else uses. The chart rejects a
  value equal to `externalServices.redis.db` or `lockDb` when it deploys
  Nautobot. After upgrading, delete the leftover `:1:nautobot*` keys from
  database 0; see "Upgrading From an Earlier Release" in the Nautobot
  integration docs.

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
