# NVIDIA Config Manager workflows

`nv-config-manager-workflows` is the shared Temporal library for NVIDIA Config
Manager. It provides reusable activities, workflow metadata and behavior, staged
execution, runtime dependency boundaries, and worker plugin registration.

Built-in workflow definitions live in `nv_config_manager_workflows.workflows`.
The service retains worker startup, runtime configuration, RBAC, and API/CLI/MCP
presentation under `src/nv_config_manager/`, together with compatibility import
paths for downstream callers. The package can also discover workflows and
activities contributed by installed plugins.

## Package boundaries

Workflow code coordinates durable execution and must remain deterministic. Use
activities for network calls, filesystem access, configuration lookup, and other
side effects.

The workflows package must remain independent of the NVIDIA Config Manager
service package, service configuration and credential loading, and concrete DCIM
provider implementations. Activities obtain service-owned dependencies from the
providers defined in `runtime.py`. The service adapter in
`src/nv_config_manager/temporal/runtime.py` configures those providers before a
worker is constructed.

Keep dependencies flowing from workflow-specific domains toward shared
capabilities. For example, a workflow-specific activity package may use the
shared `dcim` or `device` package, but shared capability packages must not depend
on a workflow-specific package.

## Adding or changing activities

Group activities by the system or capability that owns them. Operations shared
by several workflow families belong in domains such as `dcim`, `device`,
`slack`, or `ticketing`. Logic specific to one workflow family belongs in a
domain such as `backup`, `cable_validation`, or `device_password_rotation`.

Most activity domains use this layout:

```text
activities/<domain>/
├── __init__.py     # public exports and <DOMAIN>_ACTIVITIES
├── activities.py   # @activity.defn implementations
├── models.py       # optional input and output models
└── helpers.py      # optional implementation support
```

Expose supported activity functions and payload models from the domain package.
Keep a tuple named `<DOMAIN>_ACTIVITIES` for registration. Add that tuple to
`activities/builtin.py` when the activities belong in the built-in worker
catalog; every Temporal activity type must be registered exactly once.

Keep one `activities.py` by default. Split it into descriptive modules such as
`ufm_activities.py` and `dcim_activities.py` only when a domain has cohesive
groups with separate dependencies, failure modes, or test boundaries. File
length, activity count, and circular imports are not sufficient reasons to
split a domain.

Tests should mirror the production domain under `packages/workflows/tests/` and
replace runtime providers rather than read service configuration or open real
connections.

## Workflow and worker integration

Every workflow added to a runtime catalog must be decorated with
`@workflow.defn` and inherit both `StageMixin` and `WorkflowMetadataMixin`.
Declare the activities it schedules through `workflow_required_activities` so
registry validation can verify that the merged catalog supplies them before
worker construction.

Complete metadata is optional for workflows that are not exposed through the
API. API-enabled workflows define `workflow_name`,
`workflow_description`, a Pydantic `workflow_input_class`, and
`workflow_api_endpoint`.

The package exposes its 33 built-in workflows and built-in activities through the
`nv_config_manager.workflows` entry point declared in `pyproject.toml`.
`WorkflowRegistry` discovers all installed entries in that group and merges
their workflow and activity catalogs. The NVIDIA Config Manager worker registers
the registry's workflows and activities. The local-only `HelloWorldRunning`
latency fixture is kept out of the built-in plugin; the worker appends it only
when `NVCM_ENABLE_LOCAL_TEST_WORKFLOWS` is enabled.

### Registering a workflow plugin

To make workflows and activities discoverable, an installed Python distribution
must publish an entry point that resolves to a `WorkflowPluginDescriptor`
instance or a zero-argument callable returning one. For example, a plugin can
expose a descriptor factory from `example_plugin/registration.py`:

```python
from example_plugin.activities import PLUGIN_ACTIVITIES
from example_plugin.schedulers import PLUGIN_SCHEDULERS
from example_plugin.workflows import PLUGIN_WORKFLOWS
from nv_config_manager_workflows.registration import WorkflowPluginDescriptor


def plugin() -> WorkflowPluginDescriptor:
    return WorkflowPluginDescriptor(
        name="example-plugin",
        workflows=PLUGIN_WORKFLOWS,
        activities=PLUGIN_ACTIVITIES,
        schedulers=PLUGIN_SCHEDULERS,
    )
```

`schedulers` is optional; omit it if the plugin contributes no schedulers (see
[Writing a plugin scheduler](#writing-a-plugin-scheduler)).

Register that factory in the plugin's `pyproject.toml`:

```toml
[project.entry-points."nv_config_manager.workflows"]
example-plugin = "example_plugin.registration:plugin"
```

The descriptor name must match the entry-point name. Install the distribution in
every process that consumes its catalog, including the Temporal worker, the
Temporal API and workflow CLI for API-enabled workflows, the MCP server for
MCP-enabled workflows, and the scheduler host for plugins that contribute
schedulers. `WorkflowRegistry.build()` then discovers and validates it
automatically:

```python
from nv_config_manager_workflows.registration import WorkflowRegistry

registry = WorkflowRegistry.build()
print(registry.plugin_diagnostics)
print(registry.all_workflows)
print(registry.all_activities)
```

The worker, API, workflow CLI, and MCP server use this registry, so no
service-code registration change is required. Plugin workflows must satisfy the
registration contract above, and contributed names and exposed endpoints must
not conflict with the rest of the installed catalog. A workflow's CLI name (its
class name in kebab case without a `Workflow` suffix; see
`get_workflow_cli_name()`) also must not equal a built-in `workflow-cli` command
(`login`, `logout`, `auth-status`, `list-workflows`, or `examples`), because
`workflow-cli` refuses to start when one does.

### Writing a plugin scheduler

A plugin can also contribute long-running schedulers through the descriptor's
`schedulers` field. Each scheduler is a class with:

- a class-level `scheduler_identity` string of the form `<plugin-name>.<name>`;
- a no-argument constructor; and
- an async no-argument `run()` method.

For example, in `example_plugin/schedulers.py`:

```python
from nv_config_manager_workflows.schedulers.runtime import get_scheduler_runtime


class CleanupScheduler:
    scheduler_identity = "example-plugin.cleanup"

    def __init__(self) -> None:
        self.stopped = False

    async def run(self) -> None:
        runtime = get_scheduler_runtime()
        try:
            while True:
                client = await runtime.temporal_client()
                ...  # reconcile schedules with client
                await runtime.sleep(600)
        finally:
            self.stopped = True  # release resources the scheduler owns


PLUGIN_SCHEDULERS = (CleanupScheduler,)
```

The identity selects the scheduler in Helm values and appears in host logs, so
keep it stable. It has two or more dot-separated segments, each starting with a
lowercase letter and containing only lowercase letters, digits, underscores, or
hyphens. The first segment must be the contributing plugin's descriptor name, so
a plugin that contributes schedulers must be named the same way, and the
`builtin.` prefix is reserved for this package's schedulers. A plugin that
contributes schedulers also cannot have a name starting with `backup-`: its
schedule IDs (`<identity>:<key>`, see [Schedule IDs](#schedule-ids)) would then
start with `backup-`, and the built-in backup scheduler owns every such ID. Only
the plugin name is checked for `backup-`, not the rest of the identity:

| Plugin name | Scheduler identity | Example schedule ID | Result |
| --- | --- | --- | --- |
| `acme` | `acme.cleanup` | `acme.cleanup:dev-1` | Accepted |
| `acme` | `acme.backup` | `acme.backup:dev-1` | Accepted: only the plugin name is checked for `backup-` |
| `acme` | `acme.backup-sync` | `acme.backup-sync:dev-1` | Accepted |
| `backup` | `backup.sync` | `backup.sync:dev-1` | Accepted: the ID starts with `backup.`, not `backup-` |
| `backups` | `backups.sync` | `backups.sync:dev-1` | Accepted |
| `backup-tools` | `backup-tools.sync` | — | Rejected: the plugin name starts with `backup-` |
| `acme` | `builtin.cleanup` | — | Rejected: the identity must start with `acme.` |
| `acme` | `cleanup` | — | Rejected: the identity needs at least two dot-separated segments |

Validation also reads inherited identities, so a scheduler that subclasses
another scheduler must declare its own `scheduler_identity`; otherwise it
duplicates the parent's identity or carries another plugin's prefix, and
registration fails.

#### Lifecycle

The scheduler host constructs every enabled scheduler and then runs each
`run()` as a concurrent task until the process shuts down:

- Keep the constructor cheap and do the work in `run()`. If a constructor
  raises, the host exits before any scheduler starts.
- Loop until cancelled. On shutdown the host cancels every task; release
  resources in `try`/`finally` and let `asyncio.CancelledError` propagate.
- Handle expected, recoverable errors inside the loop. Raising from `run()` or
  returning normally is a failure: the host cancels every other scheduler and
  exits nonzero so that Kubernetes restarts the pod.
- Do not install signal handlers or call `loop.stop()`. The host owns `SIGTERM`
  and `SIGINT` and turns them into cancellation.

#### Dependencies

Obtain dependencies from the package runtime rather than the service:
`get_scheduler_runtime()` supplies the Temporal client, workflow RBAC roles, and
sleep, and the `nv_config_manager_workflows.runtime` `get_*` functions supply
DCIM, NATS, and the other activity dependencies. `temporal_client()` connects a
new client on every call so certificate and configuration changes take effect;
call it once per unit of work, such as one reconciliation pass, instead of
caching the client. `workflow_roles(workflow_class_name)` returns the read and
execute roles to attach to scheduled executions, or `None` when none are
configured. Sleep through `runtime.sleep()` so tests can replace it.

`get_builtin_scheduler_runtime()` serves only the built-in schedulers, and the
host configures it only when one of them is enabled; plugin schedulers must not
use it. Scheduler code must not import `nv_config_manager`.

#### Schedule IDs

Temporal schedule IDs are shared across the namespace, and Temporal does not
enforce ownership. A plugin scheduler must create, adopt, and delete only IDs of
the form `<identity>:<key>`, such as `acme.cleanup:<device-id>`, using a key
that is stable per managed object. Stay in that namespace with the helpers in
`nv_config_manager_workflows.schedulers.schedule_ids`: build IDs with
`schedule_id(self.scheduler_identity, key)` and filter `list_schedules()`
results with `owns_schedule_id(self.scheduler_identity, id)`.
`list_schedules()` returns every scheduler's schedules, so filter it before
deleting anything. The fixture plugin in
[`tests/fixtures/plugin/`](tests/fixtures/plugin/) is a complete working plugin:
a descriptor, its entry point, and a scheduler that builds its ID with
`schedule_id()` and filters `list_schedules()` with `owns_schedule_id()`.
`BackupScheduler.reconcile_schedules` is a tested example of the create-missing,
delete-extra reconciliation pattern, but it uses legacy IDs rather than these
helpers.

Identities cannot contain `:`, so the prefix is unambiguous. `schedule_id()`
raises `ValueError` for an invalid identity, an empty key, or a key containing
`/`, which Temporal's HTTP API cannot address. The built-in backup scheduler
keeps its legacy `backup-<device-id>` IDs and owns every ID starting with
`backup-`; the reserved `backup-` plugin-name prefix keeps plugin IDs out of that
namespace.

#### Validation and selection

Scheduler contracts are validated whenever a `WorkflowRegistry` is built: in the
Temporal worker, the Temporal API, the scheduler host, the MCP server, and the
workflow CLI, whether or not the scheduler is enabled. An invalid scheduler in any
installed plugin, such as an invalid or duplicate identity, a constructor that
requires arguments, or a missing or synchronous `run()`, therefore stops each of
them at startup.

The scheduler host runs only the identities selected in the Helm
`temporal.scheduler.schedulers` map: entries that are enabled and whose optional
`requiresDcimProvider` matches `dcim.provider`, for example
`--set 'temporal.scheduler.schedulers.example-plugin\.cleanup.enabled=true'`.
See [Workflow Schedulers](../../docs/temporal/temporal-deployment.mdx#workflow-schedulers)
for the operator selection rules.

To run the scheduler host locally, outside Helm, select identities with the
comma-separated `NVCM_ENABLED_SCHEDULERS` variable. If it is unset, the host runs
`builtin.backup`; if it is empty, the host runs no schedulers and waits for
shutdown. The host needs the same Temporal and DCIM configuration as the worker.

```bash
NVCM_ENABLED_SCHEDULERS=example-plugin.cleanup \
  uv run python -m nv_config_manager.temporal.scheduler.main
```

The `nv-config-manager-temporal-scheduler` console script runs the same entry
point. The pre-move module, `nv_config_manager.temporal.ngc.schedulers.backup`,
is not runnable with `python -m`.

#### Testing

Configure the runtime with fakes, run the scheduler in a task, then cancel it
and assert that cleanup ran. The runtime is process-global, so configure it in
each test:

```python
import asyncio

import pytest
from example_plugin.schedulers import CleanupScheduler
from nv_config_manager_workflows.schedulers.runtime import (
    SchedulerRuntime,
    configure_scheduler_runtime,
)


@pytest.mark.asyncio
async def test_cleanup_scheduler_stops_when_cancelled() -> None:
    slept = asyncio.Event()

    async def temporal_client():
        return FakeTemporalClient()  # implements the calls the scheduler makes

    async def sleep(seconds: float) -> None:
        slept.set()
        await asyncio.Future()  # wait until cancelled

    configure_scheduler_runtime(
        SchedulerRuntime(
            temporal_client=temporal_client,
            workflow_roles=lambda workflow_class_name: None,
            sleep=sleep,
        )
    )
    scheduler = CleanupScheduler()
    task = asyncio.create_task(scheduler.run())
    await slept.wait()  # one reconciliation pass has completed

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert scheduler.stopped
```

### Registry manifest

The `nv-config-manager-workflows-manifest` console script builds the registry
from the installed plugins and prints its manifest as JSON: each plugin's name,
version, and contribution counts; the sorted Temporal workflow types, activity
names, and scheduler identities; and a `sha256:` fingerprint over those values.
It contains no class paths, descriptor metadata, or configuration.

```sh
uv run nv-config-manager-workflows-manifest
```

Compare fingerprints across images or processes to detect a different installed
plugin set. The Temporal worker, the Temporal API, and the scheduler host log the
fingerprint at startup in their `Workflow registry manifest <fingerprint>: ...`
record. The fingerprint is not a security signature, and it does not change
when code changes under an unchanged plugin version. If discovery or validation
fails, the command prints the error to stderr and exits with status 1. To build
the same `RegistryManifest` in-process, call `registry_manifest(registry)` from
`nv_config_manager_workflows.registration`.

## Temporal compatibility

Changes to workflow definitions, scheduled activity arguments or options, and
serialized activity payloads can make existing workflow histories incompatible.
Preserve Temporal type names and payload schemas during refactors.

Replay histories for built-in workflows live under
`packages/workflows/tests/workflows/replay/fixtures/`; update them whenever
durable commands may change. Plugins must maintain replay histories and tests
for their contributed workflows in their own distributions.

## Development

Run package commands from the repository root with `uv run`:

```sh
uv run ruff check packages/workflows/src packages/workflows/tests
uv run ruff format --check packages/workflows/src packages/workflows/tests
uv run pytest packages/workflows/tests
```

When workflow commands or Temporal payloads may be affected, also run:

```sh
uv run pytest packages/workflows/tests/workflows/replay
```

When changing the plugin contract or package boundaries, also run
`make test-workflow-wheels`. It builds the workflows and fixture-plugin wheels,
installs them into a clean virtual environment without the service package, and
runs the fixture plugin's workflows and scheduler on a local Temporal server. It
needs network access for PyPI and, on first use, the Temporal dev server
download.

See the repository [contribution guide](../../CONTRIBUTING.md) for the broader
coding, testing, and review requirements.
