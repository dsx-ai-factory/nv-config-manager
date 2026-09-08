# NV Config Manager Templates

Template rendering engine, public reference templates, and plugin host for NV
Config Manager render services.

This package is consumed from this Git repository as the
`nv-config-manager-templates` distribution. Until the publishing story is
finalized, render services and external template plugin packages install it from
a sibling checkout using the local-development commands below.

## Render Model

The render engine is intentionally small:

1. A selected DCIM provider supplies provider-neutral `RenderData`.
2. The renderer validates that payload and loads built-in and plugin filters.
3. It makes provider-supplied extension data available to plugin filters by name.
4. It selects every entrypoint template under:

   ```text
   <normalized-platform>/<normalized-role>/<desired-firmware>/entrypoint/
   ```

5. It renders each entrypoint with this Jinja context:

   ```text
   device_data   # DeviceRenderData: typed identity, interface, network, routing, overlay, firmware, service, and access data
   location_data # LocationRenderData: typed location, routing, address-space, and topology data
   plugin_data   # optional provider-supplied extension data, keyed by requirement name
   ```

The platform and role path components are normalized by lower-casing and
replacing whitespace with hyphens. For example, platform `Cumulus Linux`, role
`Storage Leaf`, and firmware `5.16.1` select templates below:

```text
cumulus-linux/storage-leaf/5.16.1/entrypoint/
```

## Template Tree

Reference templates live under:

```text
src/nv_config_manager_templates/templates/
```

The expected shape is:

```text
<platform>/
  <role>/
    base/
      <shared full-file template>.j2
    include/
      <shared partial>.j2
    <firmware-version>/
      entrypoint/
        <rendered output file>.j2
      include/
        <version-specific partial>.j2
```

Use these directory names consistently:

- `entrypoint/` contains top-level rendered files. The renderer discovers these
  and produces one output file per entrypoint.
- `base/` contains reusable skeletons with Jinja blocks.
- `include/` contains partials intended to be included or overridden by a role
  or firmware-specific template.
- `<firmware-version>/include/` contains only behavior that truly differs for
  that firmware version.

Entrypoint filenames should include the output file extension plus `.j2`, such
as `startup.yaml.j2`, `boot-script.j2`, `full-config.j2`,
`ztp.json.j2`, or `nmx-commands.txt.j2`.

## Template Design Principles

### Prefer Inheritance and Small Overrides

Use Jinja inheritance as the primary way to keep templates DRY. Start with the
most common parent template, then override the smallest useful block.

Preferred pattern:

```jinja
{% extends "cumulus-linux/leaf-common/base/startup.yaml.j2" %}

{% block interfaces %}
{% include "cumulus-linux/storage-leaf/5.16.1/include/interface.j2" %}
{% endblock interfaces %}
```

Avoid copying a complete `startup.yaml.j2` just to change one section. Copying
full files makes firmware changes, bug fixes, and schema updates fan out across
many roles. A role-specific template should contain only role-specific behavior;
a version-specific template should contain only version-specific behavior.

Common inheritance layers should generally flow from broad to narrow:

```text
platform role_common -> common family role -> concrete role -> firmware version
```

For example, a Cumulus leaf role should reuse `role_common` and
`leaf-common` behavior where possible, then override only the concrete leaf
blocks that differ.

### Treat Entrypoints as Thin Composition

Entrypoints should compose reusable blocks and includes. They should not become
large data-processing files.

Good entrypoints answer:

- Which rendered files exist for this platform, role, and firmware?
- Which base skeleton is used?
- Which blocks are overridden for this version?

They should not answer:

- How is a DCIM object shaped?
- How are interfaces sorted or filtered?
- How do we recover from missing source data?

Those questions belong in Python filters and dataclasses.

### No Direct Provider Access in Templates

Templates must not call a DCIM provider, embed backend queries, or depend on a
provider-native response path directly. The renderer receives one complete
`RenderData` object from the selected provider; filters and dataclass wrappers
are the template-facing boundary.

Use:

```jinja
{% for intf in device_data|interfaces(prefix="swp") %}
  {{ intf.name }}:
{% endfor %}
```

Do not add new template logic that reaches through nested provider payloads:

```jinja
{# Do not add new code like this. #}
{% for intf in device_data.data.device.interfaces %}
```

This boundary is deliberate. A provider's REST resources, GraphQL schema, or
client-library objects can change. When templates only call wrappers such as `interfaces`,
`interface_by_name`, `site_name`, `asn`, `site_aggregates`, or plugin-owned
filters, a provider change can be handled at its boundary instead of touching
every template that happens to read the old field path.

If a template needs data that no wrapper exposes:

1. Declare a provider-neutral plugin render-data requirement when the data is
   plugin-owned, or extend the provider's `RenderData` mapping for common data.
2. Have the selected provider fetch and normalize the data into `RenderData`.
3. Add a filter or dataclass property that returns the stable concept the
   template needs.
4. Add unit tests for the wrapper.
5. Update the template to call the wrapper.

`plugin_data` follows the same rule. It is available in the render context, but
templates should not hard-code provider response paths. Expose plugin-owned
concepts through plugin filters so provider schema changes remain isolated to a
single boundary.

### Keep Data Logic in Python

Use Python filters for sorting, filtering, validation, fallback handling, and
object conversion. Templates should stay close to output formatting.

Filters should:

- Return stable domain objects or simple values.
- Raise `FilterException` when required data is missing or inconsistent.
- Provide explicit optional behavior, such as `fail_if_missing=False`, only when
  the rendered configuration is valid without that data.
- Sort collections deterministically before returning them when output order
  matters.

Templates should:

- Call filters for domain concepts.
- Format target-native output.
- Use blocks and includes for composition.
- Fail loudly by letting wrapper exceptions propagate.

### Namespace Common Templates

Common templates should be named for the domain they serve. Built-in generic
Cumulus behavior lives under `cumulus-linux/role_common`. More specific common
families use names such as `leaf-common`, `spine-common`, or
`superpod-common`.

Plugins should use their own common role names instead of adding broad names
that could be mistaken for built-in template ownership. This keeps inheritance
clear and makes accidental shadowing easier to detect.

### Be Deliberate With Firmware Versions

A firmware directory is a compatibility boundary. Add files under
`<version>/include/` or `<version>/entrypoint/` only when that firmware truly
needs different rendered output.

When a new firmware release mostly matches an existing release, prefer:

```jinja
{% extends "cumulus-linux/<role>/<previous-version>/entrypoint/startup.yaml.j2" %}
```

or a base/common template override rather than duplicating every include.

### Validate Rendered Outputs

Render tests use portable `RenderData` cache fixtures and expected output files
under `tests/resources/`. Do not add native DCIM response fixtures to template
tests. For YAML entrypoints, tests should parse the rendered output with
`yaml.safe_load` so syntax failures are caught before deployment.

When adding a role, platform, firmware version, or plugin template tree, add
fixtures that cover at least one representative device and every rendered
entrypoint.

## Built-In Filters

The engine dynamically loads public functions from these built-in filter
modules:

```text
nv_config_manager_templates.filters.bgp
nv_config_manager_templates.filters.device
nv_config_manager_templates.filters.ip
nv_config_manager_templates.filters.location
nv_config_manager_templates.filters.vault
```

Filter names are the Jinja filter names. A function named `interfaces` is used
as:

```jinja
{{ device_data|interfaces(prefix="swp") }}
```

Built-in filter name conflicts are treated as errors. Plugin filter conflicts
with built-in filters are skipped with a warning, so plugins cannot silently
replace core wrapper behavior.

## Plugin Architecture

Template plugins are normal Python packages that register an entry point in the
`nv_config_manager_templates.plugins` group. At runtime the renderer discovers
installed plugins, imports their modules, asks them for optional hooks, and adds
their templates, filters, and render-data requirements to the render
environment.

Minimal plugin registration:

```toml
[project.entry-points."nv_config_manager_templates.plugins"]
my-plugin = "my_template_plugin"
```

Minimal plugin module:

```python
from pathlib import Path
from typing import Any

from my_template_plugin.filters import get_custom_filters as _get_custom_filters


def get_template_paths() -> list[Path]:
    return [Path(__file__).parent / "templates"]


def get_custom_filters() -> dict[str, Any]:
    return _get_custom_filters()


def get_render_data_requirements() -> dict[str, object]:
    return {}
```

All plugin hooks are optional. A plugin can provide only templates, only
filters, only render-data requirements, or any combination of those
capabilities.

### Discovery and Load Order

Renderer initialization performs this sequence:

1. Read installed entry points from `nv_config_manager_templates.plugins`.
2. Import each plugin module.
3. Call `get_template_paths()` when present.
4. Call `get_custom_filters()` when present.
5. Call `get_render_data_requirements()` when present.
6. Add plugin template paths to the Jinja loader before the built-in package
   templates.
7. Load built-in filters.
8. Load non-conflicting plugin filters.
9. Collect non-conflicting plugin render-data requirements.

Because plugin template paths are loaded before the built-in template package, a
plugin can add completely new template paths or intentionally shadow a built-in
template path. Shadowing is powerful and should be rare, explicit, and covered
by render tests. Prefer namespaced templates and `extends` when the goal is to
reuse built-in behavior with a small override.

### Plugin Templates

A plugin template path is a root directory with the same logical shape as the
built-in template tree:

```text
templates/
  <platform>/
    <role>/
      base/
      include/
      <firmware-version>/
        entrypoint/
        include/
```

Plugins can:

- Add new platforms, roles, firmware versions, and entrypoint files.
- Add supplemental rendered files for a role by adding more templates under
  `entrypoint/`.
- Reuse built-in base templates with `extends`.
- Override individual blocks from built-in templates.
- Provide plugin-owned common templates for multiple plugin roles.
- Shadow a built-in template by providing the same logical template path.

Plugins should:

- Namespace plugin-owned common templates.
- Keep role and firmware-specific deltas small.
- Inherit from built-in templates instead of copying them.
- Include render fixtures for every plugin role and entrypoint.
- Avoid relying on relative load order between multiple plugins that provide the
  same template path.

### Plugin Filters

`get_custom_filters()` returns a dictionary mapping Jinja filter names to Python
callables:

```python
from inspect import getmembers, isfunction
from typing import Any

from my_template_plugin.filters import device, location

FILTER_MODULES = [device, location]


def get_custom_filters() -> dict[str, Any]:
    filters: dict[str, Any] = {}
    for filter_module in FILTER_MODULES:
        for name, func in getmembers(filter_module, isfunction):
            if not name.startswith("_") and func.__module__ == filter_module.__name__:
                filters[name] = func
    return filters
```

Plugins use filters to expose plugin-specific domain concepts to templates. A
plugin filter can interpret core `device_data`, core `location_data`, or
provider-supplied `plugin_data`. This is the correct place to hide plugin model
shape, provider schema details, and output sorting.

Filter naming should be specific enough to avoid collisions. If a plugin filter
name conflicts with an existing built-in filter or a previously loaded plugin
filter, the renderer skips it and logs a warning.

### Plugin Render-data Requirements

`get_render_data_requirements()` returns a dictionary of names to
provider-neutral requirements. The selected provider owns the native API calls
and supplies matching normalized results in `RenderData.plugin_data`:

```python
from nv_config_manager_dcim import RenderDataRequirement


def get_render_data_requirements() -> dict[str, RenderDataRequirement]:
    return {
        "my_extra_data": RenderDataRequirement(parameters={"kind": "example-policy"}),
    }
```

Requirement names must be unique across loaded plugins. Later conflicts are
skipped with a warning. Providers can use any native transport to satisfy a
requirement, and a provider that cannot support a required concept should fail
with a clear DCIM error rather than silently rendering incomplete
configuration.

Use requirements only for plugin-owned data that does not belong in the common
device or location mappings. Do not use them as a reason to put raw provider
response traversal in templates. Add plugin filters that turn `plugin_data`
into stable template concepts.

`cache-query` writes the complete payload—including plugin data—in one portable
`RenderData` envelope. Reuse it with `render --cached-render-data`.

### Plugin Packaging and Deployment

A plugin package should depend on `nv-config-manager-templates` through the
same Git/sibling-checkout workflow. The render service installs locally built
plugin artifacts into the render environment so their entry points are visible
to `importlib.metadata`; no package-index publishing is required.

The Helm chart can install plugins from images that provide wheel files under:

```text
/plugin-wheels
```

The main render image still provides the template engine and runtime
dependencies. Plugin images should contain plugin wheels and should not vendor a
second copy of the engine unless the deployment contract changes.

Template version reporting includes the engine package version and installed
template plugin package versions. This creates a compound version key like:

```text
engine=nv-config-manager-templates:<engine-version>;plugins=<plugin>:<plugin-version>
```

That version vector lets rendered configuration be compared against the exact
engine and plugin set that produced it.

### Plugin Capability Summary

Plugins can:

- Add new template trees for new platforms, roles, and firmware versions.
- Add additional rendered entrypoint files for matching devices.
- Inherit from built-in templates and override selected blocks.
- Intentionally shadow built-in templates by providing the same logical path.
- Export custom Jinja filters.
- Declare provider-neutral render-data requirements.
- Interpret provider-supplied plugin data through filters.
- Participate in rendered template version keys through package metadata.
- Ship independently as Python wheels and plugin images.

Plugins should not:

- Put native DCIM queries or client calls in Jinja templates.
- Hard-code raw provider response paths in templates.
- Copy large built-in templates for small changes.
- Depend on overriding built-in filters.
- Depend on unspecified load order between multiple plugins with the same
  template paths, filter names, or query names.
- Hide required data failures by rendering incomplete configuration.

## Local Development

Run the rendering-engine checks from this directory:

```bash
cd packages/templates
uv sync
uv run pytest
uv run ruff check src tests
```

`template-cli` is a provider-neutral command shipped by this library. It loads
an installed DCIM provider directly through `nv-config-manager-dcim`; it does
not require an NVCM service checkout. Until package publishing is finalized,
install the template library, SDK, and chosen provider from sibling Git
checkouts. Render a cached fixture locally:

```bash
uv run template-cli render \
  --cached-render-data tests/resources/render-data/a09-u28-p01-bleaf-01.json \
  --entrypoint startup.yaml.j2
```

For live data, create a service-level TOML file such as
`nautobot-provider.toml`:

```toml
[provider]
name = "nautobot"

[provider.settings]
server = "https://nautobot.example"
token = "<token>"
verify = true
```

Then query the installed provider and write one portable `RenderData` envelope:

```bash
uv run template-cli cache-query \
  --provider-config nautobot-provider.toml \
  --device-name <device-name> \
  --output-render-data-file tests/resources/render-data/<device-name>.json
```

Use that portable cache with `--cached-render-data`. It is the only cache format
accepted by `template-cli`.

Vault lookups are disabled by default for local renders unless `--vault` is
provided.
