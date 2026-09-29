# NVIDIA Config Manager workflows

`nv-config-manager-workflows` is the shared Temporal library for NVIDIA Config
Manager. It provides reusable activities, workflow metadata and behavior, staged
execution, runtime dependency boundaries, and worker plugin registration.

Core service workflows and worker startup live under `src/nv_config_manager/`.
This package contains the reusable building blocks they consume and can also
discover workflows and activities contributed by installed plugins.

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

The package exposes its built-in activities through the
`nv_config_manager.workflows` entry point declared in `pyproject.toml`.
`WorkflowRegistry` discovers all installed entries in that group and merges
their workflow and activity catalogs. The NVIDIA Config Manager worker combines
discovered workflows with its service-owned workflows and registers the
discovered activities.

### Registering a workflow plugin

To make workflows and activities discoverable, an installed Python distribution
must publish an entry point that resolves to a `WorkflowPluginDescriptor`
instance or a zero-argument callable returning one. For example, a plugin can
expose a descriptor factory from `example_plugin/registration.py`:

```python
from example_plugin.activities import PLUGIN_ACTIVITIES
from example_plugin.workflows import PLUGIN_WORKFLOWS
from nv_config_manager_workflows.registration import WorkflowPluginDescriptor


def plugin() -> WorkflowPluginDescriptor:
    return WorkflowPluginDescriptor(
        name="example-plugin",
        workflows=PLUGIN_WORKFLOWS,
        activities=PLUGIN_ACTIVITIES,
    )
```

Register that factory in the plugin's `pyproject.toml`:

```toml
[project.entry-points."nv_config_manager.workflows"]
example-plugin = "example_plugin.registration:plugin"
```

The descriptor name must match the entry-point name. Install the distribution in
every process that consumes its catalog, including the Temporal worker and, for
API-enabled workflows, the Temporal API. `WorkflowRegistry.build()` then
discovers and validates it automatically:

```python
from nv_config_manager_workflows.registration import WorkflowRegistry

registry = WorkflowRegistry.build()
print(registry.plugin_diagnostics)
print(registry.all_workflows)
print(registry.all_activities)
```

The worker and API already use this registry, so no service-code registration
change is required. Plugin workflows must satisfy the registration contract
above, and contributed names and exposed endpoints must not conflict with the
rest of the installed catalog.

## Temporal compatibility

Changes to workflow definitions, scheduled activity arguments or options, and
serialized activity payloads can make existing workflow histories incompatible.
Preserve Temporal type names and payload schemas during refactors.

Replay histories for service-owned workflows live under
`src/tests/temporal/replay/fixtures/`; update them whenever durable commands may
change. Plugins must maintain replay histories and tests for their contributed
workflows in their own distributions.

## Development

Run package commands from the repository root with `uv run`:

```sh
uv run ruff check packages/workflows/src packages/workflows/tests
uv run ruff format --check packages/workflows/src packages/workflows/tests
uv run pytest packages/workflows/tests
```

When workflow commands or Temporal payloads may be affected, also run:

```sh
uv run pytest src/tests/temporal/replay
```

See the repository [contribution guide](../../CONTRIBUTING.md) for the broader
coding, testing, and review requirements.
