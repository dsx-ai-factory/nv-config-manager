# NetBox provider update handoff

This document summarizes the provider-facing changes made on
`zb/pluggable-dcim` since commit `75289814` (`feat(dcim): version Nautobot
provider and consolidate GraphQL`). It is intended as an implementation
checklist for the collaborating NetBox DCIM provider.

## 2026-07-28

The central change is that a provider must now return fully modeled,
provider-neutral render concepts. The Render service and the common template
filter library no longer read provider-shaped dictionaries or a generic
`intent` object.

### Required NetBox provider updates

#### Adopt the current SDK render contract

Update the provider against the current `nv-config-manager-dcim` package on
this branch and construct the Pydantic models exported from
`nv_config_manager_dcim`.

`RenderData` contains:

- `device: DeviceRenderData`
- `location: LocationRenderData`
- `plugin_data: Mapping[str, RenderDataExtension]`

`DeviceRenderData` now has these typed sections:

- `identity`
- `interfaces`
- `network`
- `routing`
- `overlays`
- `firmware`
- `services`
- `access`

`LocationRenderData` now has these typed sections:

- `location`
- `routing`
- `address_space`
- `topology`

The previous `DeviceRenderData.intent`, `DeviceRenderData.inventory`,
`LocationRenderData.intent`, and `LocationRenderData.inventory` dictionaries
have been removed. Do not recreate them inside `plugin_data`. Data used by a
common filter belongs in a common SDK model.

The portable cache schema is version `1`. This is a new contract on this
branch, not a migration from a released schema. Use `RenderData.to_cache()` and
`RenderData.from_cache()` rather than maintaining a provider-specific cache
format.

#### Populate the common models

The provider should translate NetBox native objects into the following model
families as applicable:

| Area | SDK models |
| --- | --- |
| Identity and placement | `RenderDeviceIdentity`, `RenderLocation` |
| Interfaces and addressing | `RenderInterface`, `RenderIPAddress`, `RenderVlan`, `RenderVrf`, `RenderRouteTarget` |
| Connected inventory | `RenderConnectedInterface`, `RenderConnectedDevice`, `RenderConsoleServerPort` |
| General network inventory | `RenderNetworkData` |
| BGP, ISIS, and EVPN | `RenderRoutingData`, `RenderBGPInstance`, `RenderBGPPeer`, `RenderIsisInterface`, `RenderEvpnData` |
| VXLAN and overlays | `RenderOverlayData`, `RenderL2Vni`, `RenderL3Vni`, `RenderL2VniVrf` |
| Firmware intent | `RenderFirmwareData`, `RenderFirmwareBundle`, `RenderFirmwareComponent`, `RenderFirmwareArtifact`, `RenderFirmwareOverrides` |
| Network services | `RenderServicesData`, `RenderEndpointSet`, `RenderNamedEndpointSet`, `RenderPrefixSet` |
| Credential references | `RenderAccessData`, `RenderCredentialReference` |
| Location address space | `RenderLocationAddressSpace`, `RenderPrefix`, `RenderLocationVlan` |
| Location routing/topology | `RenderLocationRoutingData`, `RenderLocationTopology`, `RenderLocationDevice` |

Use NetBox first-class fields and native objects wherever they exist. For
example, an intended operating-system image stored in a NetBox native field
still maps to `RenderFirmwareData.desired_version`; it does not need to
resemble Nautobot configuration context.

The SDK models are immutable and now reject unknown fields. Provider-only
keys, raw API objects, and misspelled model fields fail validation instead of
being silently discarded.

#### Preserve field semantics, not Nautobot query shapes

The NetBox implementation can use any combination of REST, GraphQL, ORM
integration, or plugin APIs. It does not need to align its requests with the
two GraphQL documents used by the Nautobot provider.

Important semantic requirements include:

- `RenderDeviceIdentity` requires the provider identifier, name, platform,
  role, model, and complete location hierarchy needed to resolve the site.
- Every `RenderInterface` requires `name`, `type`, and `enabled`. Its role,
  addresses, VLANs, VRF, membership, parent, management flag, and connected
  endpoint should be populated when modeled in NetBox.
- `RenderIPAddress.parent_prefixes` is ordered from the nearest parent outward.
  Filters use this hierarchy for loopback, point-to-point aggregate, and
  Spectrum-X calculations.
- BGP instances and peers are grouped by provider-neutral VRF name.
  `router_id_interface` is optional in the model, but templates that need a
  router ID produce a clear `FilterException` when it is absent.
- A BGP instance with no peers is valid and must still be returned.
- `RenderBGPPeer.source_vrf` must describe the local peering VRF, regardless of
  how NetBox models that association.
- L2 and L3 VNI inventory must be scoped to the rendered device. Do not return
  every global VXLAN record to every device.
- Location prefixes retain their role and tags. Location VLANs retain their
  helper addresses.
- Secret values do not belong in render data. `RenderCredentialReference`
  contains only the secret reference and rotation metadata.

The Nautobot provider requires BGP peer `source_interface` because the
Nautobot BGP plugin does not expose the VRF association through `source_ip`.
That is a Nautobot-specific limitation. The NetBox provider should derive
`source_vrf`, peer addresses, and source-interface data from the best native
NetBox representation and should not copy this workaround unless NetBox has
the same limitation.

#### Return actionable provider errors

Validate data while translating native records into SDK models. Missing or
invalid native data that cannot satisfy the contract must raise
`DCIMInvalidDataError` with the device, object, and field identified.

Do not silently invent defaults for required data. Workflow retries are
intentionally available so an operator can correct the DCIM record and retry
the same workflow.

Pydantic `ValidationError`, conversion `ValueError`, or provider client errors
must not leak through the public provider boundary. Map them to the
corresponding SDK error described in
[`contributing-dcim-provider.mdx`](docs/development/contributing-dcim-provider.mdx).

#### Use the extension envelope for template-plugin data

Template plugins declare additional needs through
`RenderDataRequest.plugin_data_requirements`. The provider obtains the
requested data and returns each result as a namespaced, versioned
`RenderDataExtension`:

```python
RenderDataExtension(
    schema="com.example.fabric",
    version=1,
    data={"provider_neutral_key": "value"},
)
```

The extension is for a template plugin's explicitly declared domain model. It
is not an escape hatch for common filter data or a raw NetBox response.

#### Keep events provider-owned

Continue to register NetBox event handlers through
`DCIMRenderEventProvider`. Each handler owns the NetBox-specific logic that
identifies affected devices and returns `RenderEventRequest` objects. The core
dispatcher must not learn NetBox object types or relationship rules.

Add CI-only integration coverage for every registered event type represented
in the NetBox test data. The tests may mutate disposable test data, verify the
affected device render, restore the original record, and confirm the device
Render queue drains. They must not run mutations against production data.

### Common filter compatibility

The common filter names have not changed. Their implementations now read only
the typed `DeviceRenderData` and `LocationRenderData` sections. The NetBox
provider is compatible when those same filters work without a provider check,
NetBox adapter, native query, or provider-specific template branch.

At minimum, exercise:

- Device identity, tags, firmware, and platform filters.
- Interface, addressing, VLAN, VRF, connection, and console filters.
- BGP, ISIS, EVPN, and router-ID filters.
- L2/L3 VNI and route-target filters.
- DNS, NTP, syslog, TACACS, DHCP, provisioning, and management-prefix filters.
- Firmware bundle and override filters.
- Location aggregate, topology, helper-address, and tag filters.
- Credential-reference filters.

Required-but-unset values should produce the new field-specific
`FilterException` messages. Optional fields should remain optional until a
filter with a stronger requirement consumes them.

### Recommended NetBox data-mapping document

The NetBox plugin should provide its own `netbox-data-mapping.mdx`, following
the structure of
[`nautobot-data-mapping.mdx`](docs/render/nautobot-data-mapping.mdx).

For every built-in device and location filter, document:

1. The filter name.
2. The provider-neutral `RenderData` field it consumes.
3. The NetBox native model, field, custom field, relationship, or plugin model
   used to populate it.
4. Any REST, GraphQL, or plugin calls needed to assemble it.
5. Relevant scoping or normalization rules.
6. Known NetBox/plugin limitations and the validation error users will see.

This mapping should describe actual NetBox behavior rather than copying the
Nautobot source column. It will serve as both user documentation and a
coverage checklist: an undocumented filter mapping is likely an untested
provider boundary.

### Suggested validation checklist

- Unit-test every native-to-SDK mapper, including malformed and missing data.
- Assert unknown/provider-specific fields are rejected by SDK models.
- Round-trip representative data through `RenderData.to_cache()` and
  `RenderData.from_cache()`.
- Build fixtures exclusively from provider-neutral cached render data.
- Run the common network-template filter and render tests against NetBox
  fixtures.
- Render all template entrypoints for every represented mock device.
- Invoke `/v1/render/all`, wait for the device consumers to drain, and verify
  every render-enabled device has an intended configuration and commit ID.
- Run NetBox event mutation tests only in CI or against a disposable local
  environment.

### Changes that are Nautobot-only

The following changes since `75289814` do not impose implementation
requirements on the NetBox provider:

- Consolidated Nautobot GraphQL fields and Nautobot-specific mapping logic.
- The Nautobot BGP plugin `source_interface` validation described above.
- Device scoping based on Nautobot Overlay plugin assignments, interface
  VLANs, and device VRFs.
- Nautobot intended-configuration create/update behavior.
- Nautobot bootstrap roles, statuses, configuration contexts, and mock data.

NetBox should produce the same provider-neutral outcomes, but its native
implementation should follow NetBox's own data model.
