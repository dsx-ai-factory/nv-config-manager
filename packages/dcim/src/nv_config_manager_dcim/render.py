# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Provider-neutral models required by a template render.

Providers translate their native DCIM records into the typed concepts in this
module.  Template consumers therefore never need to understand a provider's
GraphQL, REST, custom-field, or configuration-context representation.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import Field, IPvAnyAddress, IPvAnyInterface, IPvAnyNetwork, JsonValue

from nv_config_manager_dcim.models import DCIMModel

RENDER_DATA_CACHE_SCHEMA_VERSION = 1
"""Version of the portable, provider-neutral ``RenderData`` cache envelope."""


class RenderLocation(DCIMModel):
    """One location in a device's provider-neutral location hierarchy."""

    name: str
    id: str | None = None
    kind: str | None = None
    tags: tuple[str, ...] = ()
    parent: RenderLocation | None = None


class RenderDeviceIdentity(DCIMModel):
    """Stable identity and placement values used to choose templates."""

    id: str
    name: str
    platform: str
    role: str
    model: str
    location: RenderLocation
    tags: tuple[str, ...] = ()


class RenderRouteTarget(DCIMModel):
    """One route target assigned to a VRF or overlay."""

    name: str


class RenderVrf(DCIMModel):
    """A provider-neutral VRF used by a rendered device."""

    name: str
    route_distinguisher: str | None = None
    import_targets: tuple[RenderRouteTarget, ...] = ()
    export_targets: tuple[RenderRouteTarget, ...] = ()


class RenderVlan(DCIMModel):
    """A VLAN referenced by a rendered interface or location."""

    vid: int
    name: str | None = None


class RenderIPAddress(DCIMModel):
    """An address assignment and its provider-neutral prefix ancestry."""

    address: IPvAnyInterface
    host: IPvAnyAddress
    version: int
    role: str | None = None
    parent_prefixes: tuple[IPvAnyNetwork, ...] = ()


class RenderConnectedDevice(DCIMModel):
    """The modeled device at the far end of an interface connection."""

    id: str | None = None
    name: str
    role: str
    tenant: str | None = None
    tags: tuple[str, ...] = ()
    routing_asn: str | None = None


class RenderConnectedInterface(DCIMModel):
    """The modeled far-end interface of a device interface connection."""

    name: str
    vrf: str | None = None
    addresses: tuple[RenderIPAddress, ...] = ()
    device: RenderConnectedDevice


class RenderInterface(DCIMModel):
    """Typed interface inventory required by the template filter library."""

    name: str
    type: str
    mtu: int | None = None
    enabled: bool
    tags: tuple[str, ...] = ()
    description: str = ""
    role: str | None = None
    mac_address: str | None = None
    vrf: RenderVrf | None = None
    management_only: bool = False
    member_interfaces: tuple[str, ...] = ()
    parent_interface: str | None = None
    untagged_vlan: RenderVlan | None = None
    tagged_vlans: tuple[RenderVlan, ...] = ()
    addresses: tuple[RenderIPAddress, ...] = ()
    connected_interface: RenderConnectedInterface | None = None


class RenderConsoleServerPort(DCIMModel):
    """A console-server port and its modeled console endpoint."""

    name: str
    connected_device_name: str | None = None
    connected_device_manufacturer: str | None = None
    connected_port_name: str | None = None


class RenderNetworkData(DCIMModel):
    """Non-routing network inventory consumed by common templates."""

    vrfs: tuple[RenderVrf, ...] = ()
    console_server_ports: tuple[RenderConsoleServerPort, ...] = ()
    nvlink_topology: str | None = None


class RenderBGPPeer(DCIMModel):
    """One BGP peer in a normalized routing instance."""

    name: str
    status: str
    description: str
    peer_group: str
    peer_role: str
    asn: str
    peer_ipv4: IPvAnyAddress | None = None
    peer_ipv6: IPvAnyAddress | None = None
    source_interface: str | None = None
    source_vrf: str = "default"
    ttl: int | None = None


class RenderBGPInstance(DCIMModel):
    """One local BGP routing instance and its peers."""

    status: str
    asn: str
    router_id_interface: str | None = None
    vrfs: tuple[str, ...] = ("default",)
    peers: tuple[RenderBGPPeer, ...] = ()


class RenderIsisInterface(DCIMModel):
    """ISIS settings assigned to one interface."""

    interface_name: str
    metric: int | None = None


class RenderEvpnData(DCIMModel):
    """EVPN control-plane settings consumed by common templates."""

    esi_base_mac: str | None = None
    fabric_mac: str | None = None
    df_preference: int | None = None


class RenderRoutingData(DCIMModel):
    """Typed control-plane data for a device render."""

    bgp_instances: tuple[RenderBGPInstance, ...] = ()
    default_asn: str | None = None
    local_asn: str | None = None
    site_asn: str | None = None
    isis_interfaces: tuple[RenderIsisInterface, ...] = ()
    evpn: RenderEvpnData = Field(default_factory=RenderEvpnData)


class RenderL2Vni(DCIMModel):
    """A VLAN-to-L2-VNI assignment."""

    vlan: RenderVlan
    vni: int | None = None
    overlay_name: str | None = None
    import_targets: tuple[RenderRouteTarget, ...] = ()
    export_targets: tuple[RenderRouteTarget, ...] = ()


class RenderL3Vni(DCIMModel):
    """A VRF-to-L3-VNI assignment."""

    vrf: RenderVrf
    l3_vlan_id: int | None = None
    vni: int | None = None
    overlay_name: str | None = None


class RenderL2VniVrf(DCIMModel):
    """One L2 VNI overlay and its associated route targets."""

    name: str
    vni: int | None = None
    import_targets: tuple[RenderRouteTarget, ...] = ()
    export_targets: tuple[RenderRouteTarget, ...] = ()


class RenderOverlayData(DCIMModel):
    """Typed VXLAN/overlay data consumed by common templates."""

    l2_vnis: tuple[RenderL2Vni, ...] = ()
    l3_vnis: tuple[RenderL3Vni, ...] = ()
    l2_vni_vrfs: tuple[RenderL2VniVrf, ...] = ()


class RenderFirmwareArtifact(DCIMModel):
    """A firmware or operating-system image artifact."""

    version: str | None = None
    image_file: str | None = None
    source_path: str | None = None


class RenderFirmwareComponent(DCIMModel):
    """A named firmware component included in a bundle or override."""

    name: str
    artifact: RenderFirmwareArtifact


class RenderFirmwareBundle(DCIMModel):
    """A versioned device firmware bundle."""

    version: str
    operating_system: RenderFirmwareArtifact
    components: tuple[RenderFirmwareComponent, ...] = ()


class RenderFirmwareOverrides(DCIMModel):
    """Per-device deviations from the selected firmware bundle."""

    skip_components: tuple[str, ...] = ()
    custom_components: tuple[RenderFirmwareComponent, ...] = ()


class RenderFirmwareData(DCIMModel):
    """Desired operating-system and firmware-bundle data for a device."""

    desired_version: str | None = None
    selected_bundle_version: str | None = None
    bundles: tuple[RenderFirmwareBundle, ...] = ()
    overrides: RenderFirmwareOverrides = Field(default_factory=RenderFirmwareOverrides)


class RenderEndpointSet(DCIMModel):
    """A named service's reachable IPv4 and IPv6 addresses or hostnames."""

    ipv4: tuple[str, ...] = ()
    ipv6: tuple[str, ...] = ()


class RenderNamedEndpointSet(DCIMModel):
    """A provider-named endpoint set, such as one DHCP provider."""

    name: str
    endpoints: RenderEndpointSet


class RenderPrefixSet(DCIMModel):
    """IPv4 and IPv6 networks assigned to one rendered device purpose."""

    ipv4: tuple[IPvAnyNetwork, ...] = ()
    ipv6: tuple[IPvAnyNetwork, ...] = ()


class RenderServicesData(DCIMModel):
    """Effective device-level network service policy."""

    dns: RenderEndpointSet | None = None
    ntp: RenderEndpointSet | None = None
    syslog: RenderEndpointSet | None = None
    tacacs: RenderEndpointSet | None = None
    ztp: RenderEndpointSet | None = None
    firmware_cache: RenderEndpointSet | None = None
    provisioning: RenderEndpointSet | None = None
    dhcp: tuple[RenderNamedEndpointSet, ...] = ()
    management_prefixes: RenderPrefixSet | None = None


class RenderCredentialReference(DCIMModel):
    """A named account's secret reference and rotation metadata."""

    username: str
    secret_name: str
    rotation: str
    role: str | None = None


class RenderAccessData(DCIMModel):
    """Credential references made available to a template render."""

    credentials: tuple[RenderCredentialReference, ...] = ()


class DeviceRenderData(DCIMModel):
    """Fully typed device data supplied to a template render."""

    identity: RenderDeviceIdentity
    interfaces: tuple[RenderInterface, ...] = ()
    network: RenderNetworkData = Field(default_factory=RenderNetworkData)
    routing: RenderRoutingData = Field(default_factory=RenderRoutingData)
    overlays: RenderOverlayData = Field(default_factory=RenderOverlayData)
    firmware: RenderFirmwareData = Field(default_factory=RenderFirmwareData)
    services: RenderServicesData = Field(default_factory=RenderServicesData)
    access: RenderAccessData = Field(default_factory=RenderAccessData)


class RenderPrefix(DCIMModel):
    """A provider-neutral location prefix and its template-relevant metadata."""

    prefix: IPvAnyNetwork
    role: str | None = None
    tags: tuple[str, ...] = ()


class RenderLocationVlan(DCIMModel):
    """A location VLAN and its DHCP helper addresses."""

    vlan: RenderVlan
    helper_addresses: tuple[IPvAnyAddress, ...] = ()


class RenderLocationAddressSpace(DCIMModel):
    """Address-space information consumed by location template filters."""

    prefixes: tuple[RenderPrefix, ...] = ()
    uc_jumphost_prefixes: tuple[IPvAnyNetwork, ...] = ()
    vlans: tuple[RenderLocationVlan, ...] = ()


class RenderLocationDevice(DCIMModel):
    """A routing-relevant device related to a rendered location."""

    name: str
    routing_asn: str | None = None
    loopback_addresses: tuple[IPvAnyAddress, ...] = ()


class RenderLocationTopology(DCIMModel):
    """Location-level route-server and WAN topology used by templates."""

    route_servers: tuple[RenderLocationDevice, ...] = ()
    wan_routers: tuple[RenderLocationDevice, ...] = ()


class RenderLocationRoutingData(DCIMModel):
    """Canonical location routing data inherited by rendered devices."""

    site_asn: str | None = None


class LocationRenderData(DCIMModel):
    """Fully typed location data supplied to a template render."""

    location: RenderLocation
    routing: RenderLocationRoutingData = Field(default_factory=RenderLocationRoutingData)
    address_space: RenderLocationAddressSpace = Field(default_factory=RenderLocationAddressSpace)
    topology: RenderLocationTopology = Field(default_factory=RenderLocationTopology)


class RenderDataExtension(DCIMModel):
    """A namespaced, versioned provider payload owned by a template plugin."""

    schema_id: str = Field(alias="schema", serialization_alias="schema")
    version: int
    data: JsonValue


class RenderDataRequirement(DCIMModel):
    """One named extension-data requirement declared by a template plugin."""

    parameters: Mapping[str, JsonValue] = Field(default_factory=dict)


class RenderDataRequest(DCIMModel):
    """The complete data request passed from a render consumer to a provider."""

    device_id: str
    plugin_data_requirements: Mapping[str, RenderDataRequirement] = Field(default_factory=dict)


class RenderData(DCIMModel):
    """Complete provider-owned payload required for one device render."""

    device: DeviceRenderData
    location: LocationRenderData
    plugin_data: Mapping[str, RenderDataExtension] = Field(default_factory=dict)

    def to_cache(self) -> dict[str, JsonValue]:
        """Serialize this payload using the portable render-data cache envelope."""
        return {
            "schema_version": RENDER_DATA_CACHE_SCHEMA_VERSION,
            "device": self.device.model_dump(mode="json"),
            "location": self.location.model_dump(mode="json"),
            "plugin_data": {
                name: extension.model_dump(mode="json", by_alias=True)
                for name, extension in self.plugin_data.items()
            },
        }

    @classmethod
    def from_cache(cls, payload: Mapping[str, JsonValue]) -> RenderData:
        """Deserialize a portable provider-neutral render-data cache envelope."""
        schema_version = payload.get("schema_version")
        if schema_version != RENDER_DATA_CACHE_SCHEMA_VERSION:
            raise ValueError(
                "Unsupported RenderData cache schema version "
                f"{schema_version!r}; expected {RENDER_DATA_CACHE_SCHEMA_VERSION}"
            )
        try:
            return cls.model_validate(
                {
                    "device": payload["device"],
                    "location": payload["location"],
                    "plugin_data": payload.get("plugin_data", {}),
                }
            )
        except (KeyError, ValueError) as exc:
            raise ValueError(f"Invalid RenderData cache: {exc}") from exc
