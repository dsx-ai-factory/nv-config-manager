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
`workflow_api_endpoint`. The endpoint is the workflow execution path, such as
`/ngc/site_password_rotation`. They may also define `workflow_group` to
organize the workflow in UI catalogs; workflows without one use the default
group.

Browser forms are opt-in. API-enabled workflows with browser forms set
`workflow_form_enabled = True` and define an explicit `workflow_form_id`. The
ID is a globally unique lowercase kebab-case identifier such as
`site-password-rotation`, and is the stable public path segment in form and
form-option URLs. It is deliberately separate from
`workflow_api_endpoint`: the former identifies the browser-form contract,
while the latter remains the workflow execution endpoint. Do not derive the
form ID from the Python class name or execution endpoint; refactors to either
must not silently rename a public form route.

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
registration contract above. Workflow class names, declared workflow names,
Temporal workflow types, and API endpoints remain globally unique across all
installed plugins in this version. A workflow's CLI name (its class name in
kebab case without a `Workflow` suffix; see
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

### Workflow forms

The UI launcher renders a form-enabled API workflow's start form with
[RJSF](https://rjsf-team.github.io/react-jsonschema-form/) from
`GET /v1/workflow/{form_id}/form`, where `{form_id}` is the workflow's explicit
`workflow_form_id`. The response is a version 1 envelope:

```json
{"schema": {}, "ui_schema": {}, "ui_schema_version": 1, "requires": []}
```

- `schema` is a form projection of the input model's JSON Schema: an optional
  `X | None` field becomes `X`, a `None` default is dropped, every property has a
  title, and the field markers below are applied.
- `ui_schema` is the model's validated `rjsf_ui_schema`, plus options the server
  fills in (`filterScope`, `queryAliases`).
- `requires` lists the capabilities the UI must support to render the form.

A model without `rjsf_ui_schema` gets RJSF's default controls for its projected
schema. Pydantic ignores the `ClassVar` and marker metadata, so they do not
change the model's `model_json_schema()`, its Pydantic validation rules, the API
request body shape, the MCP tool schema, the corresponding generated-client
model, or Temporal replay deserialization of existing histories. The HTTP API
still uses `ServerOwned` to replace authoritative identity fields and may run
the workflow's `canonicalize_input` checks after Pydantic validation and before
starting a workflow, as described below. The form's own checks only report
problems before submitting.

#### Declaring a form

Declare the form on the input model as
`rjsf_ui_schema: ClassVar[Mapping[str, object]]`, a supported subset of an RJSF
`uiSchema`. The helpers in `nv_config_manager_workflows.ui` return plain dicts;
merge further keys into them:

```python
from collections.abc import Mapping
from typing import Annotated, ClassVar

from pydantic import BaseModel, Field

from nv_config_manager_workflows.ui import (
    FormExcluded,
    FormSchema,
    OptionSource,
    ServerOwned,
    api_options,
    device_field,
    location_field,
    variant_rows,
)

SITES = OptionSource(
    "/v1/parameter/location",
    "name",
    "id",
    type_key="location_type",
    params={"location_type": ["Site", "Module"]},
)
DEVICES = OptionSource("/v1/parameter/device", "name", "id", params={"managed_only": True})
PORTS = OptionSource(
    "/v1/parameter/device/{device_id}/interfaces", "name", "name", clear_on_change=True
)


class PortResetInput(BaseModel):
    rjsf_ui_schema: ClassVar[Mapping[str, object]] = {
        "ui:order": ["site", "device_id", "port_names", "*"],
        "ui:submitButtonOptions": {"submitText": "Reset ports"},
        "site": {**location_field(SITES, type_field="site_type"), "ui:title": "Site"},
        "site_type": {"ui:widget": "hidden"},
        "device_id": {
            **device_field(DEVICES, filters=("site", "status"), site_field="site"),
            "ui:title": "Device",
        },
        "port_names": {**api_options(PORTS), "ui:title": "Ports"},
        "reason": {"ui:widget": "textarea", "ui:options": {"rows": 3}},
    }

    site: str = Field(description="Site containing the device.")
    site_type: str | None = None
    device_id: str = Field(description="Device whose ports are reset.")
    port_names: Annotated[list[str], FormSchema(min_items=1)]
    reason: str | None = None
    user: Annotated[str | None, ServerOwned()] = None
    parent_workflow_id: Annotated[str | None, FormExcluded()] = None
```

This form selects a site, then a managed device at that site (optionally
filtered by status), then that device's ports, which reload and clear when the
device changes. The form requires at least one port, while the API still accepts
an empty list. `user` and `parent_workflow_id` are not part of the form. Its
`requires` is `core-field.api-options.v1`, `core-field.device.v1`, and
`core-field.location.v1`. For complete declarations, see `BackupInput` in
`workflows/backup.py`, `SpXOverlayTenantChangeInput` in `workflows/spx_overlay.py`,
and the fixture plugin's `FixtureInput` in
[`tests/fixtures/plugin/`](tests/fixtures/plugin/).

#### Supported keys

Form-catalog construction rejects every other key, widget, and field.

| Where | Key | Value |
| --- | --- | --- |
| root | `ui:order` | Property names, each at most once; must list every property unless it contains `"*"` |
| root | `ui:submitButtonOptions` | `{"submitText": "<label>"}` |
| root | `ui:globalOptions` | Form-wide `hideSchemaDescriptions`, `exclusiveGroups`, and `fieldComparisons` behavior described below |
| property | `ui:title`, `ui:help`, `ui:description`, `ui:placeholder` | Non-blank text |
| property | `ui:widget` | `text`, `textarea`, `checkbox`, `select`, or `hidden` |
| property | `ui:field` | `apiOptions`, `device`, `location`, or `variantRows`; set by the helpers |
| property | `ui:readonly` | Boolean |
| property | `ui:options` | `{"rows": <positive int>}` for a standard field; core-field options come from the helpers |

Property keys must name top-level properties of the projected schema, and a
property cannot set both `ui:widget` and `ui:field`. Property keys, and the
properties that `type_field`, `site_field`, and `Dependency` name, must match
`^[A-Za-z_][A-Za-z0-9_]*$`; give a field with another alias a plain one. A
field's property name is its JSON Schema name: its `validation_alias` when that
is a string, else its `alias`, else the field name. `hidden` is allowed only on
a location field's `typeField` sibling or on a property whose projected schema
has a default, such as a field with a `FormSchema(default=...)`. A hidden
property is still submitted. To leave a field out of the form, mark it
`FormExcluded` instead.

#### Option sources

A core field loads its options from the workflow API through an
`OptionSource(endpoint, label_key, value_key, *, type_key=None, params={},
depends_on={}, clear_on_change=False, response=None)`:

- `endpoint` is a path relative to the workflow API origin, such as
  `/v1/parameter/device`. It cannot contain `?`, `#`, `\`, or whitespace. A
  whole `{property}` path segment, such as `/v1/parameter/device/{device_id}/interfaces`,
  is filled from that sibling property. It is a required dependency: options
  wait until the property has a value.
- Each returned row supplies its label under `label_key` and its submitted
  value under `value_key`. `type_key` supplies the selected location type for
  a location field with a `type_field` and for the device field's Site filter.
- `params` are static query parameters: strings, finite numbers, booleans, or
  lists of them, which become repeated parameters.
- `depends_on` maps a query parameter to a `Dependency(field, *, required=True)`
  on another projected property. A required dependency holds the request while
  that property is empty. An optional one (`required=False`) is left out of the
  request instead.
- `clear_on_change=True` clears the selection when a dependency changes.
- `response="options-v1"` selects the standard enriched envelope. Its `items`
  rows use `label` and `value`, may include `description` and `group`, and its
  `meta` object supplies values referenced by `meta_text` and
  `disable_when_no_matches`.

Dependencies must name projected properties and cannot form a cycle, including
a field that depends on itself.

`OptionSource` and the helpers do not validate when they are constructed, so a
malformed source never fails the import of the module that declares it.
Form-catalog construction checks the emitted wire form instead and reports any
problem as a form error (see below).

An `OptionSource` endpoint must already be served by the workflow API. Plugins
can use one for an existing platform resource. When a plugin owns the option
lookup, prefer a generated form option provider instead of installing a
FastAPI router.

##### Plugin-provided options

A plugin can declare an API-only async resolver without adding another entry
point or importing FastAPI. Define a flat Pydantic query model and return the
standard enriched response:

```python
# example_plugin/form_option_providers.py
from pydantic import BaseModel, ConfigDict

from nv_config_manager_workflows.ui import OptionItem, OptionSourceResponse


class ProfileQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site: str


async def list_profiles(query: ProfileQuery) -> OptionSourceResponse:
    profiles = await load_profiles(query.site)
    return OptionSourceResponse(
        items=[
            OptionItem(
                label=profile.display_name,
                value=profile.name,
                description=profile.description,
            )
            for profile in profiles
        ]
    )
```

Reference the provider by a stable slug from the input form and declare its
import strings on the workflow:

```python
from collections.abc import Mapping
from typing import ClassVar

from pydantic import BaseModel

from nv_config_manager_workflows.ui import (
    Dependency,
    FormOptionProvider,
    FormOptionSource,
    api_options,
)


class ExampleInput(BaseModel):
    rjsf_ui_schema: ClassVar[Mapping[str, object]] = {
        "profile": api_options(
            FormOptionSource(
                "fabric-profiles",
                depends_on={"site": Dependency("site")},
            )
        )
    }

    site: str
    profile: str


class ExampleWorkflow(...):
    workflow_form_enabled = True
    workflow_form_id = "example-workflow"
    workflow_input_class = ExampleInput
    workflow_form_option_providers = {
        "fabric-profiles": FormOptionProvider(
            resolver="example_plugin.form_option_providers:list_profiles",
            query_model="example_plugin.form_option_providers:ProfileQuery",
        )
    }
```

Use the same `nv_config_manager.workflows` entry point shown in
[Registering a workflow plugin](#registering-a-workflow-plugin); provider
modules do not need a separate entry point. The API validates and loads only
providers referenced by a form, then generates this typed route at startup.
Form IDs and option-provider source names such as `fabric-profiles` use
lowercase kebab-case:

```http
GET /v1/workflow/example-workflow/form-options/fabric-profiles?site=site-a
```

```json
{
  "items": [
    {
      "label": "Production",
      "value": "production",
      "description": "Production fabric settings",
      "group": null
    }
  ],
  "meta": {
    "matching_device_count": null,
    "warnings": []
  }
}
```

The generated route uses the existing browser `OptionSource` protocol, so it
does not require a new UI capability. It is read-only, inherits the workflow's
execute-role authorization, and is subject to platform-owned timeouts,
concurrency and result-size limits. Provider code is trusted server-side code:
it runs in the API process with that process's permissions and is not a plugin
sandbox. Do not put secrets, nested objects, or large values in a GET query.
Query models support flat scalars, enums/literals, optional scalars, and
repeated scalars; nested models, mappings, aliases, headers, request bodies,
and FastAPI dependencies are rejected.

Built-in generated routes appear in the checked-in OpenAPI specification and
generated clients. Routes contributed by installed third-party plugins appear
in that deployment's runtime `/openapi.json`, because the project cannot know
which plugins another installation will have. Workflow form IDs and provider
slugs use lowercase kebab-case. Both form part of the
public URL. Workflow form IDs are globally unique; provider slugs are unique
within their workflow. Treat both as stable API identifiers. Adding an optional
query field is compatible; renaming or changing a field, or adding a required
field, requires a new provider slug such as `fabric-profiles-v2` while the old
version remains available for the compatibility window.

Validate an installed plugin before deployment:

```bash
nv-config-manager-workflows-validate-plugin example-plugin
```

The command builds that plugin's registry and form catalog, imports every
referenced query model and resolver, and validates their signatures and query
contracts. It does not invoke resolvers or require running services. Invalid
forms or providers produce a nonzero exit status; unused provider declarations
are warnings because they are never imported by the API.

#### Core fields

`api_options(source, *, presentation=None, select_all=False,
show_descriptions=False, meta_text=None, disable_when_no_matches=False)` renders
a select whose options come from `source`. A string property gets a single
select, and an array of strings gets a multiple select. The presentation options
require `source.response="options-v1"`; `disable_when_no_matches=True` disables
the picker when that response reports `matching_device_count` as zero.

`location_field(source, *, type_field=None)` renders a site or location select
for a string property. With `type_field`, selecting a location also writes the
row's `type_key` value into that sibling string property in the same update.
Declare that sibling `{"ui:widget": "hidden"}`. `source` must then set
`type_key`.

`device_field(source, *, filters=(), site_required=True, site_field=None,
filter_scope=None, query_param="device-id")` renders a device select, with
filter controls, for a string or string-array property. `source` cannot set
`type_key` or `depends_on`. The helper emits `filterSources`, a mapping from
each enabled filter to the backend-owned `OptionSource` that supplies its
choices. The UI requests `source.endpoint` with `source.params`, plus `site`
and `site_type` once a site is known and repeated `tenant` and `status` values
from the filters.

- `filters` picks the Site, Tenant, and Status filter controls from `"site"`,
  `"tenant"`, and `"status"`. Their `filterSources` keys exactly match the
  enabled controls; callers do not declare these sources separately.
- `site_required` holds the device options until a site is chosen.
- `site_field` names a location field in the same form that declares a
  `type_field`. Site then comes from that field instead of a Site filter
  control, and `filters` must include `"site"`.
- `filter_scope` lets device fields share one set of filter controls. Fields in
  one scope must use the same `filters`, `site_required`, and `site_field`, and
  a field with an explicit scope cannot be `ui:readonly`. Without a scope, a
  field gets the private scope `implicit:<property>`. The `implicit:` prefix is
  reserved and cannot be declared.
- `query_param` names the URL parameter that pre-fills the selection. `None`
  turns off URL prefill for that field. Two device fields in one form need
  different values because each URL parameter has one owner.

`variant_rows(*, owned_properties, modes, minimum_rows=1,
clear_inactive=True, warning=None)` renders one of several mutually exclusive
repeatable-row layouts. It is intended for a single logical input that the API
already represents as multiple top-level arrays, such as PKey members entered
by interface or by GUID. Each mode declares an `id`, `label`, and `columns`.
A column names its top-level `arrayProperty`, a `label`, and a `kind` of `text`
or `select`; an object-array column also names its string `itemProperty`, while
a scalar-array column omits it. Text columns may declare `placeholder` and
`pattern`; select columns declare static `choices`; either kind may be
`required`.

Every owned property must be a projected top-level array, belong to exactly one
mode, and be controlled only through the one anchor property carrying
`variant_rows`. List the other owned properties in `ui:order` and mark them
hidden. Switching modes clears the inactive arrays; `clear_inactive=False` is
not supported. Form-catalog construction checks that the column bindings match
the projected array and item schemas. See `IBPKeyMemberAddInput` in
`workflows/ib_pkey_member_add.py` for an object-array and scalar-array example.

#### Form-wide interactions and validation

`ui:globalOptions.exclusiveGroups` declares two or more mutually exclusive
input modes. Each group lists projected `fields`; a `device` field may also be
listed in `deviceFilters` when its non-model Site, Tenant, or Status controls
activate the same mode. A populated mode disables the other modes, and changing
modes clears their projected values and device filters. Every field and device
filter may belong to only one group, and a `deviceFilters` entry must also be in
that group's `fields`. Set `requireComplete: True` on every group when the form
must select one mode and every field in the active mode is required. A required
Site on a listed device field is also checked. For example:

```python
"ui:globalOptions": {
    "exclusiveGroups": [
        {
            "fields": ["device_id", "interface"],
            "deviceFilters": ["device_id"],
            "requireComplete": True,
        },
        {"fields": ["remote_mac_address"], "requireComplete": True},
    ]
}
```

`ui:globalOptions.fieldComparisons` adds client-side comparisons between two
projected numeric fields. Version 1 supports `operator: "lessThan"`; the error
is attached to `left` with the declared `message`:

```python
"ui:globalOptions": {
    "fieldComparisons": [{
        "left": "rd_min",
        "operator": "lessThan",
        "right": "rd_max",
        "message": "RD Min must be less than RD Max",
    }]
}
```

Form validation is not an API security boundary. When the API must enforce a
cross-field rule without changing replay deserialization, implement the same
check in the workflow class's `canonicalize_input` method and raise a
non-retryable `ApplicationError`. Do not add a Pydantic model validator solely
for a launcher interaction; existing Temporal histories deserialize that model.
The workflow HTTP endpoint runs this hook after Pydantic request validation and
returns HTTP 422 in the OpenAPI `HTTPValidationError` envelope if it raises
`ApplicationError`; callers that start a Temporal workflow directly do not pass
through this HTTP-boundary hook.

The built-in Port LLDP Info endpoint uses the hook to trim its interface or MAC
lookup value and require exactly one complete lookup method: `device_id` with
`interface`, or `remote_mac_address` by itself. The built-in SpX Overlay
Creation endpoint uses it to require `rd_min < rd_max`. These are stricter HTTP
submission rules even though the Pydantic request schemas themselves are
unchanged.

#### Field markers

Markers are plain objects in a field's `Annotated` metadata. They do not change
the Pydantic request schema or its validation. All three shape the `/form`
projection; `ServerOwned` also declares that the HTTP boundary, rather than the
submitted body, owns the marked identity value:

- `ServerOwned()`: on a field named `user`, the HTTP API replaces any submitted
  value with identity derived from the authenticated request. Other field names
  are rejected in workflow-form v1. It is left out of the form schema and its
  `required` list. Trusted callers that
  construct Temporal input directly, such as schedulers and parent workflows,
  may still provide it.
- `FormExcluded()`: the form neither shows nor submits the field, so the model
  default applies. Direct HTTP and Temporal callers may still provide it. Use it
  for values that schedulers or parent workflows send, and for fields resolved
  outside the form.
- `FormSchema(*, default=..., min_items=, max_items=, min_length=, max_length=,
  minimum=, maximum=, pattern=)`: adds form-only JSON Schema keywords (`default`,
  `minItems`, `maxItems`, `minLength`, `maxLength`, `minimum`, `maximum`, and
  `pattern`) to the property. Each keyword must fit the property's JSON type,
  and `default` must validate against the field's type and must not be `None`. Use it to tighten or
  seed the form without changing what the API accepts. For example, `BackupInput`
  keeps `trigger` required and gives the form a hidden default of `API`.

`ServerOwned` and `FormExcluded` fields must have a Pydantic default or
`default_factory`, because the request body is validated before the server
fills it. A field carries at most one marker. Markers belong only in a top-level
field's own metadata or in a top-level `Annotated` alias. Form-catalog
construction rejects a marker in a list item, a union member, or a nested
model's field. Marked fields cannot appear in `rjsf_ui_schema`.

Keep input models compatible: an input model is the API request body and the
Temporal payload of existing workflow histories. Express form-only rules with
`FormSchema` instead of new Pydantic constraints, defaults, or validators.

#### URL prefill

A launcher URL such as
`/workflows/new/site-password-rotation?location=RNO1` pre-fills the form. Each
URL parameter has exactly one owner:

| Owner | Parameters |
| --- | --- |
| Standard projected field (not hidden, no `ui:field`) | Its property name. The value is converted to the property's JSON type. |
| `apiOptions` or `location` field | Its property name. |
| `device` field | Its `query_param`; none when `query_param=None`. |
| Device filter scope | `site` (only without `site_field`), `tenant`, and `status`, for the filters it uses. |

Form-catalog construction rejects a form in which two owners claim the same
parameter, such as two device fields with the default `query_param`, or a
standard field named `status` beside a device field with a Status filter.
`ServerOwned`, `FormExcluded`, and hidden properties cannot be pre-filled. A
core field applies its URL value only when the value matches a loaded option.
Submit stays disabled until every pre-filled core field has resolved.

A few built-in forms also accept URL spellings that shipped before this
contract, such as `?device=` for device password rotation. The server adds them
as `queryAliases` from a central map in `nv_config_manager_workflows.ui.form`.
It also adds a `querySeparator` for a shipped multi-select link that encoded
multiple values in one comma-separated parameter. Authors cannot declare these
compatibility options. New forms use property names, repeated parameters, and
`query_param`.

#### Form-catalog errors and plugin isolation

The workflow API builds a `WorkflowFormCatalog` from its `WorkflowRegistry` and
validates the `/form` envelope of every form-enabled API workflow. Temporal
workers, schedulers, MCP, and the CLI build only the execution registry and do
not build or validate browser forms. Workflows remain executable through the
API or CLI without a browser form by default. A workflow opts in by declaring
`workflow_form_enabled = True` and a `workflow_form_id` on its class. The
metadata catalog reports `has_form: false` for workflows that do not opt in.
`has_form: true` means the form contract is enabled and discoverable; its form
endpoint can still return HTTP 503 when a third-party declaration is invalid.
Invalid declarations on form-enabled workflows raise
`WorkflowFormContractError` with a message that names the model and the problem.
For example, the error can report an unknown key, widget, or `ui:field`, a
property that is not in the form schema, an incompatible property type, a
malformed endpoint or parameter, a dependency cycle, a scope conflict, a
misplaced marker or missing default, or a URL owner collision. A wrongly typed
declaration value, such as a list where a widget name belongs, or a Pydantic
JSON Schema error is reported the same way. The result
depends on the workflow's owning plugin, which is the first plugin in registry
order to contribute the class, with the built-in plugin first:

- **Built-in plugin:** the error stops workflow API startup and fails the
  built-in form validation in CI. It does not stop Temporal workers, schedulers,
  MCP, or the CLI.
- **Third-party plugin:** the form catalog records a diagnostic containing the
  plugin, workflow, and sanitized message. The workflow, its execution
  endpoint, the CLI, and MCP are unaffected. Only its form is unavailable:
  `GET /v1/workflow/{form_id}/form` returns HTTP 503 with
  `workflow_form_unavailable`, the plugin and workflow identifiers, and the
  generic message "This workflow form is unavailable because its plugin failed
  form validation." The launcher shows that generic message with **Return to
  Workflows** and no **Try again** action. The detailed sanitized validation
  message remains in the form catalog, is written to the API startup log, and
  is reported by the plugin-validation CLI.

Other registration errors, such as invalid metadata, duplicate endpoints, or
missing activities, remain fatal for every plugin. Inspect a plugin's form
diagnostics before release:

```python
from nv_config_manager_workflows.registration import WorkflowRegistry
from nv_config_manager_workflows.registration.form_catalog import WorkflowFormCatalog

registry = WorkflowRegistry.build()
form_catalog = WorkflowFormCatalog.build(registry)
print(form_catalog.diagnostics)
```

#### Capabilities and versioning

The backend derives `requires` from the declaration:

- `core-field.api-options.v1`, `core-field.device.v1`, and
  `core-field.location.v1` for each core field type the form uses
- `core-field.api-options.enriched.v1` when an `apiOptions` source sets
  `response="options-v1"`
- `core-field.variant-rows.v1` when the form uses `variant_rows`
- `interaction.exclusive-groups.v1` for
  `ui:globalOptions.exclusiveGroups`
- `prefill.query-separator.v1` for a server-owned legacy multi-value URL
  delimiter
- `theme.hide-schema-descriptions.v1` when `ui:globalOptions.hideSchemaDescriptions`
  is `true`
- `validation.field-comparison.v1` for
  `ui:globalOptions.fieldComparisons`

`ui_schema_version` is `1`. The UI checks the version first and then checks every
`requires` entry against its capability manifest. If it does not support either
one, the UI shows a "needs a newer UI" state instead of rendering a partial form.
After those checks, it validates the envelope against the wire schema. An
older UI can therefore detect a newer server's forms without rendering them
incorrectly.

The wire schema `workflow-form-v1.schema.json` and the capability manifest
`workflow-form-v1.capabilities.json` ship in `nv_config_manager_workflows/ui/`
and are read on first use through `wire_schema()` and `capability_manifest()`,
so importing a workflow module does not parse them. The UI keeps byte-for-byte copies in
`ui/src/lib/` because its container build cannot read files outside `ui/`.
`packages/workflows/tests/ui/test_form.py` fails when the copies differ.
Change both copies together, and add a capability only together with the UI
code that implements it.

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
