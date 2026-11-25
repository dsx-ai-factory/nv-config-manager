# Changelog

Notable user-facing changes for NVIDIA Config Manager should be recorded here.

This project uses release tags for published versions. Add entries under
`Unreleased` while changes are in flight, then finalize them under the release
version before creating the release candidate tag selected for promotion.
The promotion workflow extracts GitHub release notes from that commit.

## Unreleased

### Changed

- Long-lived services now exit, and Kubernetes restarts them, when a setting in
  `nv-config-manager.ini` changes, so a rotated credential or renamed NATS
  stream takes effect without a manual rollout. Each pod restarts between 30
  seconds and two and a half minutes after the file changes, at a moment
  shared by its containers so the pod goes unready once and drawn afresh for
  each change, and the log line names the settings that changed, never their
  values. Expect container restarts a few minutes after the mounted Secret
  updates. A change
  to comments, whitespace, or ordering alone restarts nothing, and neither
  does a value that changes back before the delay ends. For a file that
  cannot be parsed, the restart watcher logs an error and does not restart;
  services that re-read the file themselves still fail until it is fixed. A
  process still running 30 seconds after the restart request is stopped
  outright. This covers the DHCP configuration refresh and sync loops as well
  as the API, consumer, worker, and scheduler services. The DHCP API reads
  everything except `dhcp.cors_origins` from the file on each request, so it
  restarts only when that setting changes.
- The API services now wait at most 25 seconds for open requests to finish
  after SIGTERM, so a long request can no longer hold a shutdown past the
  pod's grace period.
- The DHCP configuration refresh and sync loops and the Config Store cache
  refresh service now stop promptly on SIGTERM. Before, the loops ignored the
  signal and the cache refresh service did not act on it until its next
  refresh, so each was killed at the end of the pod's grace period.
- The Config Manager Temporal worker now handles SIGTERM: it stops taking new
  tasks and gives running activities 25 seconds to finish before cancelling
  them. Before, it ignored the signal and was killed at the end of the pod's
  grace period while still polling.
- A workflow whose result cannot be published to NATS after three attempts now
  logs a warning with the cause and completes, instead of failing after its
  work is done.

## 1.4.3

Unreleased. Changes since 1.4.2, prepared for release from `main`.

### Added

- Added a standalone [integration-test command](docs/install/install-airgapped.mdx#run-integration-tests-after-deployment)
  for an existing deployment: `nvcm-installer test install.yaml`. It runs the
  tests bundled in the deployed image without redeploying. The runner requires
  bundled Nautobot and a ready ZTP SFTP container. SSO deployments can
  use Keycloak client credentials or a supplied access-token file.
- Added [Kea DHCPv4 thread and queue controls](docs/dhcp/index.mdx#kea-multi-threading)
  through `networkDhcp.kea.multiThreading` and installer
  `dhcp.kea_multi_threading`. CPU-based sizing rounds the DHCP container's CPU
  request up to at least one thread. Explicit thread and queue sizes are also
  supported. Defaults retain Kea's automatic thread count and 64-packet queue;
  CPU-based sizing and an explicit thread count cannot be combined.
- Added [`oidc.proxy.cookieRefresh`](docs/install/gateway-api.mdx#oidc-session-refresh)
  to configure request-driven OIDC session refresh. The default remains `"1h"`.
  Set an interval shorter than the forwarded ID token's lifetime; refresh also
  depends on the identity provider and does not extend token lifetimes.

### Changed

- Cable-status persistence now passes each device's full change set to bulk-capable
  DCIM providers, which control their write strategy. The Nautobot plugin uses
  two concurrent updates and one GraphQL interface-to-cable lookup per device
  filtered to the requested names,
  replacing per-batch REST interface lookups. Cable statuses are read live before
  deciding to skip or write each update. Malformed records do not prevent valid
  matches from being saved. The full-device activity timeout increases from
  fifteen minutes to one hour per attempt, with up to three attempts in total;
  single-update providers remain supported.
  Persistence sends background heartbeats with a two-minute heartbeat timeout
  to detect lost workers promptly. Single-update providers keep ten concurrent
  writes; bulk-capable providers control their own concurrency.
  REST GETs retry transient connection failures, timeouts, and HTTP 502/503/504
  once with jittered backoff, excluding TLS verification failures. New standalone
  device cable validations allow a manual retry of transient persistence failures
  without collecting device tables again; site children still fail promptly, and
  released workflow histories retain their previous failure behavior on replay.
- Bundled Nautobot now assigns the MCP read-only token to the dedicated
  `nvcm-mcp-read-only` inventory account. Before upgrading, ensure
  `NAUTOBOT_READ_ONLY_API_TOKEN` differs from the admin token. If its key already
  belongs to another user, the `run-migrations` init container stops. Prefer a
  fresh token of exactly 40 lowercase hexadecimal characters in the
  [configured Nautobot secret](docs/overview/mcp.mdx#nautobot-inventory-with-sign-in-disabled).
  Wait for secret synchronization, then rerun the migration init container.
  Registration deletes other tokens owned by `nvcm-mcp-read-only`; MCP clients
  using those Nautobot tokens lose access. Update every affected consumer to use
  the configured token, then reload the MCP server configuration.
  Reassign an existing token only if it is dedicated to MCP, since initialization
  disables writes and restricts its access to inventory. Create `nvcm-mcp-read-only`
  if needed before reassigning.
- Cable validation now allows five minutes per ARP collection attempt, up from
  two, to accommodate switches with large interface inventories and slow NVUE
  responses. New site validations allow thirty minutes per device, up from ten,
  so the ARP retries and remaining validation stages fit within the child
  workflow deadline. Already-started child workflows keep their existing deadline.
- A render consumer whose NATS fetches keep failing now exits, and Kubernetes
  restarts the container, once no round trip to NATS has completed for
  `CONSUMER_LIVENESS_TIMEOUT_SECONDS` (default 600). Before, it kept retrying
  on a connection that was not coming back while the pod stayed `Running`.
  Expect a container restart where a consumer used to stall. The render
  consumers also export progress metrics for stalls the exit cannot catch,
  such as a handler that never returns. Refer to
  [Render service](docs/render/render-service.mdx).
- The Temporal archive service now exits, and Kubernetes restarts the pod, once
  its durable consumer has made no progress for
  `CONSUMER_LIVENESS_TIMEOUT_SECONDS` (default 600). Before, a subscription that
  stopped receiving left the pod `Running` while archiving nothing. A pod that
  is still receiving and retrying messages during an archive backend outage is
  not restarted. A durable deleted while the service runs is re-created after
  its last acknowledged message, so events published in between are still
  archived. The archive pod also serves progress metrics on
  `temporal.archive.metricsPort` (default 8000), scraped by a new PodMonitor when
  `monitoring.podMonitors.enabled` and `temporal.archive.enabled` are set. See
  [Render service](docs/render/render-service.mdx).
- Retryable failed workflow stages now wait for a manual retry for a configurable
  window (three hours by default) after automatic activity or child retries are
  exhausted, instead of indefinitely. An unanswered retry wait closes the
  workflow as **Failed** with a non-retryable `StageRetryTimeout` cause. The durable
  timer survives worker restarts and does not limit healthy work or approval
  waits. Existing histories that already took the indefinite failure-wait path
  retain it for replay compatibility.
- Helm `temporal.configManagerWorker.failedStageRetryTimeoutSeconds` configures
  the failed-stage manual retry window in positive integer seconds (default
  10,800, maximum 31,536,000, or 365 days). The chart renders this setting for
  every secrets method. New workflow histories and schedule actions record the
  value so replay and child runs preserve it across restarts and configuration
  changes. Recreate existing schedules to apply a changed retry window. An unset
  key, such as after `helm upgrade
  --reuse-values` from an earlier release, renders the default. Installer
  deployments set it with `temporal.failed_stage_retry_timeout_seconds` in
  `nv-config-manager-install.yaml`. Refer to
  [Failed-stage retry window](docs/temporal/temporal-deployment.mdx#failed-stage-retry-window).
- The Config Manager Temporal worker now serves its Temporal SDK metrics on a
  Prometheus endpoint, scraped by a new PodMonitor, whenever
  `monitoring.enabled` and `monitoring.podMonitors.enabled` are set. Series now
  carry the pod's Kubernetes `namespace` label, so workflow results can be
  separated per deployment. The SDK's Temporal `namespace` label becomes
  `exported_namespace`;
  update selectors and grouping that identify a Temporal namespace. These
  metrics no longer use OTLP. Their `job` label changes from
  `nv-config-manager-temporal-worker` to the PodMonitor's
  `<namespace>/<worker deployment name>`. Update dashboards and alerts that
  match the old `job`. The scraped endpoint adds no unit suffix. If your OTLP
  collector adds one, as bundled Alloy does, the `_milliseconds` part disappears
  from the corresponding metric names, such as
  `temporal_request_latency_milliseconds_*`. Duration values remain in
  milliseconds. Refer to
  [Temporal worker metrics](docs/overview/observability.mdx#temporal-worker-metrics).
- CNPG clusters now accept `cnpg.<cluster>.affinity.podAntiAffinityType`
  (`preferred` or `required`). The chart defaults now set
  `enablePodAntiAffinity: true` and `podAntiAffinityType: preferred`, which
  match the scheduling CloudNativePG already applied when they were unset.
- The Config Manager Temporal worker's sticky workflow cache now defaults to 200
  entries, compared with the Temporal SDK default of 1,000. It runs at most 10
  workflow tasks concurrently, compared with the SDK default of 100. These
  limits bound the memory retained by cached runs and queue workflow bursts to
  reduce workflow task timeouts. Both limits are set in the new
  `[temporal.worker]` INI section (`max_cached_workflows`,
  `max_concurrent_workflow_tasks`), which the Helm chart renders from
  `temporal.configManagerWorker.maxCachedWorkflows` and
  `maxConcurrentWorkflowTasks`. Invalid values fail worker startup.
- `nv_config_manager_nautobot_change_message_end_to_end_time` gains buckets at
  900, 1,200, 1,800, 2,700, 3,600, and 7,200 seconds. The metric includes queue
  wait, which can exceed ten minutes when one change affects many devices. The
  previous 600-second top bucket grouped longer waits in `+Inf`. Existing
  bucket boundaries are unchanged, so queries on them keep working.
- `nv_config_manager_workflows.workflows` now imports each exported name from
  its defining module when the name is accessed. Importing one workflow module
  no longer imports all of them, so each workflow run's sandbox re-imports only
  the modules that run uses instead of all 29 workflow modules. The package's
  public names are unchanged.
- The bundled Nautobot image no longer includes `nautobot_firewall_models`.
  Deployments that used the app must review their firewall data and plan its
  retention or cleanup before upgrading. Removing the app does not provide a
  migration to a replacement firewall model.
- New render consumers use a default delivery limit of 20. Retryable handler
  failures and device-lock contention count toward that limit; a message that
  still fails on its final allowed delivery is logged and terminated. Existing
  NATS durables retain their own
  `max_deliver`, including `-1` for unlimited retries. Review externally managed
  consumers and the [delivery-limit guidance](docs/render/render-service.mdx#external-nats-administration).
- Workflow archive writes now retain their backlog in JetStream while the
  backend is unavailable, with at most two concurrent writes. New archive
  consumers default to 288 deliveries with five-minute retry delays. Permanently
  rejected records and records still failing at the durable's delivery limit
  are logged and discarded. Existing durables retain their configured limits;
  review the [archive consumer settings](docs/render/render-service.mdx#external-nats-administration).

### Fixed

- The DHCP config refresh no longer restarts when the DCIM times out, cannot be
  reached, or returns a server error, as it does for a minute or two while its
  database fails over. It skips that cycle, keeps Kea on its last good
  configuration, and tries again after the refresh interval. Previously the
  container exited and could enter `CrashLoopBackOff`. Skips are counted under
  `nv_config_manager_dhcp_query_errors_total{error_type="unavailable"}`, and a
  sustained outage still stops `cache_last_refresh_timestamp` from advancing.
  Client errors and data that fails validation still restart the container, and
  one-shot refreshes still fail.
- Backup stages in deployment, tenant deployment, password rotation, OS upgrade,
  NVLink firmware upgrade, and reprovisioning now await the backup child's result
  so failures and timeouts reach stage failure handling. Previously a failed or
  timed-out backup was reported as backed up and the workflow continued. These
  workflows now stop at the backup stage even if the configuration change
  already took effect. Retry the stage to start a new backup. Without a retry,
  the workflow closes as **Failed** after the failed-stage retry window. A backup
  child can retry its stage until the failed-stage retry window or its 10-minute
  run timeout expires, whichever comes first. The parent then reports a failure
  or timeout. Refer to the backup workflow's page for the underlying error.
  Histories that already used the old child-completion check retain that path
  for replay compatibility.
- Workflow API approve, reject, and retry requests now return `409 Conflict`
  with the status if the workflow closes before the signal arrives. They return
  `404` if the workflow no longer exists. Previously, an unhandled signal
  error could return `500` when a workflow closed before the signal arrived.
  The UI hides **Retry** after a failed stage's workflow closes. On a `409`
  response, it reports that the workflow is no longer running and refreshes
  the workflow.
- `cnpg.<cluster>.affinity.enablePodAntiAffinity: false` now disables pod
  anti-affinity on the CNPG `Cluster`. The chart previously dropped `false`,
  and CloudNativePG then enabled preferred anti-affinity by default.
  Deployments that set `false`, including the `values-local-small.yaml` and
  `values-local-medium.yaml` profiles, will see their database pods' scheduling
  rules change on upgrade
  ([#406](https://github.com/dsx-ai-factory/nv-config-manager/issues/406)).
- `localDev.mockDevices` now takes effect with `secrets.method: eso` and
  `secrets.method: vault-agent`. Before, only `secrets.method: kubernetes`
  rendered `mock = true` in the `[device]` section, so the other methods
  silently kept real device connections.
- With `secrets.method: vault-agent`, pods now carry a `checksum/vault-agent-ini`
  annotation, so a change to the rendered `nv-config-manager.ini` template rolls
  them. Before, the injector kept serving the old template until pods restarted.
- The Temporal worker no longer re-imports `pydantic` and the shared workflow
  libraries in the sandbox for every workflow run. This cuts the per-run startup
  CPU and per-run memory that contributed to workflow task timeouts, deadlock
  errors, and OOMKills during large fan-outs such as site cable validation.
  Every built-in workflow module imports dependencies other than the standard
  library and `temporalio` inside its
  `workflow.unsafe.imports_passed_through()` block. A test enforces this pattern.
  The workflows package README documents it for plugin workflows. The
  worker also passes through `annotated_types`, which Pydantic imports lazily
  after those blocks have closed.
- `workflow-cli` now sends the default of a list option as a list when the
  option is omitted. Previously, it split the default's text on commas,
  producing values such as `["['Active'", "'Provisioned']"]` for `--status`.
  Commands such as `site-cable-validation` then failed at the first stage unless
  you supplied every list option explicitly.
- MCP Nautobot inventory tools now use dedicated read-only credentials when
  sign-in is disabled. The installer provisions these for built-in Nautobot.
  Existing deployments must populate `readOnlyToken` and rerun Nautobot
  migrations; with ESO, wait for the read-only Secret to synchronize first.
  External Nautobot requires a separately provisioned view-only account and
  token. Follow the [inventory credential upgrade steps](docs/overview/mcp.mdx#nautobot-inventory-with-sign-in-disabled).
- Helm now rejects CNPG backup configurations with an unsupported destination
  or missing S3 bucket or path. When enabling backups, set
  `cnpg.backup.destinationType: s3`, `cnpg.backup.s3.bucket`, and
  `cnpg.backup.s3.path`. Backup-credential ExternalSecrets now follow the backup
  settings of enabled clusters, including Nautobot, instead of the unused
  `cnpg.backup.enabled` value.
- Cumulus neighbor discovery now collects IPv4 and IPv6 neighbors through the
  split NVUE endpoints on Cumulus Linux 5.15 and later, while retaining the
  legacy endpoint for earlier versions. Missing data for one address family no
  longer discards the other family's neighbors.
- Render queue claims are atomic, preventing concurrent producers from
  publishing duplicate render requests or clearing another producer's queued
  flag after a failed publish.
- Helm and Argo CD run the template updater after Config Store migrations,
  so template loading no longer races database initialization.
- AIR deployment checks now use the Temporal bootstrap image's CLI to verify
  search attributes. Failed checks retain their diagnostics, and workers restart
  only when a successful check confirms missing attributes.
- The ZTP SFTP server offers ECDSA as well as RSA host keys, allowing Cumulus
  clients without RSA SHA-2 support to negotiate a host key after the Paramiko
  dependency update.
- Helm renders `temporal.dynamicConfig` in Temporal's native key-to-values
  mapping format, preserving configured values and namespace constraints.

### Security

- Updated Python, Go, installer, UI, and runtime-image dependencies to
  incorporate security fixes. Bundled oauth2-proxy is now 7.15.6 and the Stork
  agent is 2.6.0. SSH dependencies require Paramiko 5.0.1 or later.
- Updated the Temporal Web UI's Markdown dependency and preserved the
  Nautobot runtime image's patched zlib library instead of replacing it with
  the builder's copy.

## 1.4.2

Changes since 1.4.1. Released November 16, 2026.

### Added

- Added [Cisco IOS XR support](docs/user-guides/cisco-ios-xr/index.mdx)
  over NETCONF for configuration backup and deployment, cable validation,
  password rotation, and diagnostics. Rescue configuration and OS image
  installation are not supported.
- Added [Arista EOS zero-touch provisioning](docs/network-ztp/configuration.mdx)
  and [password rotation](docs/temporal/workflows/device-password-rotation.mdx).
  EOS provisioning requires a matching boot-script template and a reachable
  ZTP SFTP endpoint for the intended configuration.
- Added [workflow plugin images](docs/temporal/temporal-deployment.mdx#workflow-plugins)
  with startup validation and Helm-managed API access rules.
- Added installer `cluster.custom_labels` for Kubernetes resources and exported
  metrics. Configure them in the TUI's **Cluster** screen or in
  [installer YAML](docs/install/configuration-samples.mdx#custom-deployment-labels).
- Added separate controls for Temporal History replicas and persisted shards.
  [Pin an existing cluster's shard count](docs/temporal/temporal-deployment.mdx#history-shards-and-replicas)
  before scaling replicas.
- DHCP container probes (liveness, readiness, startup) are now configurable per
  container (`kea`, `healthcheck`, `api`, `confgen`) through
  [`networkDhcp.probes`](docs/dhcp/index.mdx#configure-container-probes)
  in Helm values. Existing probe defaults are preserved.

### Changed

- Updated the managed Temporal server and bootstrap tools from 1.30.7 in
  1.4.1 to 1.31.5, through the intermediate 1.30.8 update.
  The frontend init container upgrades PostgreSQL core/visibility schemas to
  1.19/1.14. Review the [upgrade and rollback notes](docs/temporal/temporal-deployment.mdx#upgrading-from-temporal-1308-to-1315)
  before upgrading an existing deployment.
- Direct Vault lookups from `load_secret` now require `VAULT_ADDR`. Set
  `VAULT_NAMESPACE` when needed. File-backed secret loading, including
  ESO-synced configuration secrets, is unchanged. Review
  [local template testing](docs/render/template-expansion.mdx#local-plugin-testing)
  when upgrading.
- Short workflow attribution identities can use the optional
  [`global.defaultUserDomain`](docs/temporal/temporal-deployment.mdx#workflow-attribution-domain)
  fallback. Authenticated email identities retain their own domain.
- Updated the bundled Nautobot image to 2.4.45 and refreshed CA trust in runtime
  images. Custom OIDC proxy images must provide refreshed CA stores.

### Fixed

- The installer now refuses to deploy when `--load-kind` is set but the current
  kube context is not the matching Kind cluster, or when `images.source` is
  `local` and the context does not look like a cluster on this machine. Set
  `NVCM_INSTALLER_ALLOW_REMOTE_CLUSTER_FOR_LOCAL_IMAGES=1` to bypass the
  `images.source: local` check for clusters that can use locally built images.
- Installer content hashes no longer depend on wall-clock time, preventing
  unchanged jobs and templates from being detected as changed across runs.
- Gateway retries recover requests that never reached a backend during endpoint
  changes. Browser-proxy HTTP 502 retries are limited to GET and HEAD. Review the
  [retry settings](docs/install/gateway-api.mdx#gateway-retries) for controller and
  shared-Gateway behavior.
- DHCP readiness compares the running Kea configuration with the desired
  configuration hash, so readiness reflects whether configuration has converged.
- MCP starts when DHCP is not deployed. DHCP requests report that the service
  is unavailable while other MCP capabilities remain usable. Workflow response
  redaction also preserves pagination metadata.
- The Configs page opens a device when you select its row outside action buttons.
- `temporal.gateway.devUi.allowedGroups` is now enforced by the OIDC proxy. It
  was previously ignored, so every authenticated user could open Temporal Web
  and act on any workflow. Deployments that set it will start returning 403 to
  users outside the listed groups.

## 1.4.1

### Added

- Added Hyper-reach cable validation and its AIR demonstration. These features
  were backported to the 1.4.1 release branch.

### Fixed

- The AIR Hyper-reach fixture installs its LLDP host in a chassis DeviceBay and
  advertises the chassis name, enabling parent-chassis validation in simulation.
  The accompanying documentation and report examples use the chassis identity.

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
