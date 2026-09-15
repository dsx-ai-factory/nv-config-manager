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
"""Custom Jinja2 filters for provider-neutral device render data."""

# pylint: disable=too-many-lines
import ipaddress
import re
from typing import Any

from nv_config_manager_dcim import DeviceRenderData, LocationRenderData

from nv_config_manager_templates.dataclasses.bgp import BGPLocalConfig, BGPPeer
from nv_config_manager_templates.dataclasses.consoleport import ConsoleServerPort
from nv_config_manager_templates.dataclasses.interface import ConnectedDevice, Interface
from nv_config_manager_templates.dataclasses.vrf import VRF
from nv_config_manager_templates.filters import FilterException
from nv_config_manager_templates.filters.ip import gateway as gateway_filter


def _required(value: Any, field: str, device: DeviceRenderData) -> Any:
    """Return a required modeled field or raise a template-actionable error."""
    if value is None:
        raise FilterException(
            f"Device {device.identity.name} is missing required render field '{field}'. "
            "Set the corresponding value in the DCIM provider."
        )
    return value


def hostname(value: DeviceRenderData) -> str:
    """Return the hostname of the device."""
    return value.identity.name


def site_name(value: DeviceRenderData) -> str:
    """Return the site name for the device."""
    location = value.identity.location
    while location:
        if location.kind == "Site":
            return location.name
        location = location.parent

    raise FilterException("Found no Site location.")


def device_tags(value: DeviceRenderData) -> list[str]:
    """Return the list of tags for this device."""
    return list(value.identity.tags)


def has_tag(value: DeviceRenderData, tag: str) -> bool:
    """Return true if device has the given tag."""
    return tag in device_tags(value)


def interface_has_tag(intf: Interface, tag: str) -> bool:
    """Return true if the interface has *tag* (name match is case-insensitive)."""
    tag_l = tag.lower()
    return any(t.lower() == tag_l for t in intf.tags)


def platform(value: DeviceRenderData) -> str:
    """Return the platform name of this device."""
    return value.identity.platform


def model(value: DeviceRenderData) -> str:
    """Return the model name."""
    return value.identity.model


def role(value: DeviceRenderData) -> str:
    """Return the role name for this device."""
    return value.identity.role


def desired_firmware(value: DeviceRenderData) -> str:
    """Return the desired firmware image version for this device."""
    return _required(
        value.firmware.desired_version,
        "device.firmware.desired_version",
        value,
    )


def router_id(value: DeviceRenderData) -> str:
    """Return the router-id of this device."""
    platform_name = platform(value)
    if platform_name == "Cumulus Linux":
        ifname = "lo"
    elif platform_name == "Arista EOS":
        ifname = "Loopback0"
    else:
        raise FilterException(f"Unhandled platform: {platform_name}")

    intf = interface_by_name(value, ifname)
    # Return primary IPv4 with prefix length stripped
    primary_ipv4 = _required(
        intf.primary_ipv4,
        f"device.interfaces[name={ifname}].primary_ipv4",
        value,
    )
    return re.sub(r"\/\d+$", "", primary_ipv4)


def uuid(value: DeviceRenderData) -> str:
    """Return the provider-neutral identifier for the device."""
    return value.identity.id


def asn(value: DeviceRenderData, vrf: str = "default") -> str:
    """Return the ASN for the device."""
    for instance in value.routing.bgp_instances:
        if vrf in instance.vrfs:
            return instance.asn
    return _required(value.routing.default_asn, "device.routing.default_asn", value)


def interface_by_name(
    value: DeviceRenderData, name: str, fail_if_missing: bool = True
) -> Interface | None:
    """Grab an interface object by interface name."""
    interface_entries = value.interfaces
    try:
        interface_entry = next(
            interface for interface in interface_entries if interface.name.lower() == name.lower()
        )
        return Interface.from_render_data(interface_entry)
    except StopIteration as exc:
        if fail_if_missing:
            raise FilterException(f"No interface found with name {name}.") from exc
        return None


def breakout_count(value: DeviceRenderData, interface_name: str) -> int:
    """Return the breakout count for the interface."""
    child_interfaces = interfaces(value, prefix=f"{interface_name}s")
    if len(child_interfaces) == 1 and child_interfaces[0].name == interface_name:
        # No breakout
        return 0

    if len(child_interfaces) <= 2:
        return 2

    if len(child_interfaces) <= 4:
        return 4

    if len(child_interfaces) <= 8:
        return 8

    raise FilterException(f"Detected more than 8 breakouts on {interface_name}.")


def loopback_prefix(value: DeviceRenderData) -> str:
    """Return the parent prefix for an interface IP."""
    try:
        interface_entries = value.interfaces
        interface_entry = next(
            interface for interface in interface_entries if interface.name == "lo"
        )
        if not interface_entry.addresses or len(interface_entry.addresses[0].parent_prefixes) < 2:
            raise FilterException(
                f"Device {hostname(value)} interface lo is missing the parent-prefix hierarchy "
                "required to calculate the loopback prefix."
            )
        return str(interface_entry.addresses[0].parent_prefixes[1])
    except StopIteration as exc:
        raise FilterException("No interface found with name lo.") from exc


def interfaces(  # pylint: disable=too-many-arguments,too-many-branches
    value: DeviceRenderData,
    prefix: str = None,
    contains: str = None,
    vrf: str = None,
    interface_role: str | list[str] | None = None,
    peer_role: str | list[str] | None = None,
    include_mgmt: bool = True,
    bgp_peers_only: bool = False,
    tags: list[str] | None = None,
) -> list[Interface]:
    """Return a list of interface objects with optional filtering."""
    interface_records = []
    for interface_entry in value.interfaces:
        if include_mgmt or not interface_entry.management_only:
            interface_records.append(Interface.from_render_data(interface_entry))

    if prefix:
        interface_records = [
            interface
            for interface in interface_records
            if interface.name.lower().startswith(prefix.lower())
        ]

    if contains:
        interface_records = [
            interface for interface in interface_records if contains in interface.name
        ]

    if vrf:
        interface_records = [interface for interface in interface_records if interface.vrf == vrf]

    if interface_role:
        if isinstance(interface_role, str):
            interface_records = [
                interface for interface in interface_records if interface.role == interface_role
            ]
        elif isinstance(interface_role, list):
            interface_records = [
                interface for interface in interface_records if interface.role in interface_role
            ]

    if peer_role:
        if isinstance(peer_role, str):
            interface_records = [
                interface
                for interface in interface_records
                if interface.connected_interface
                and (interface.connected_interface.device.role.lower() == peer_role.lower())
            ]
        elif isinstance(peer_role, list):
            peer_role_list = [role.lower() for role in peer_role]
            interface_records = [
                interface
                for interface in interface_records
                if interface.connected_interface
                and interface.connected_interface.device.role.lower() in peer_role_list
            ]

    if bgp_peers_only:
        interface_records = [
            interface for interface in interface_records if interface.has_bgp_peer()
        ]

    if tags:
        interface_records = [
            interface
            for interface in interface_records
            if any(tag in interface.tags for tag in tags)
        ]
    return sorted(interface_records, key=interface_natural_sort_key)


def management_interface(value: DeviceRenderData) -> Interface:
    """Return the management interface for the given device."""
    if platform(value) in ["Cumulus Linux", "MLNX-OS"]:
        try:
            return interface_by_name(value, "eth0")
        except FilterException as exc:
            # Some IPMI devices will use loopback instead
            if "smn" not in role(value).lower() and "ipmi" not in role(value).lower():
                raise exc
            return interface_by_name(value, "lo")

    if platform(value) == "Arista EOS":
        management_interfaces = interfaces(value, prefix="Management")
        if len(management_interfaces) == 1:
            return management_interfaces[0]

        if not management_interfaces:
            raise FilterException("No Management interface found.")
        raise FilterException("Multiple management interfaces defined.")
    raise FilterException(f"No Management Interface lookup implemnented for {platform(value)}")


def default_gateways(value: DeviceRenderData, version: int = 4) -> list[str]:
    """Return a list of default gateway IPs for the given device."""
    if "smn" in role(value).lower() or "uc" in role(value).lower():
        # For the SMN routers, pull the IP
        # for each uplink interface
        uplink_interfaces = interfaces(value, interface_role="Uplink")
        result = []
        for interface in uplink_interfaces:
            try:
                if version == 4 and interface.connected_interface.device.peer_ipv4:
                    result.append(interface.connected_interface.device.peer_ipv4)
                elif version == 6 and interface.connected_interface.device.peer_ipv6:
                    result.append(interface.connected_interface.device.peer_ipv6)
            except AttributeError:
                continue
        if not result:
            raise FilterException("Must have at least one default gateway.")
        return result
    mgmt_interface = management_interface(value)
    if version == 4:
        primary_address = _required(
            mgmt_interface.primary_ipv4,
            f"device.interfaces[name={mgmt_interface.name}].primary_ipv4",
            value,
        )
    else:
        primary_address = _required(
            mgmt_interface.primary_ipv6,
            f"device.interfaces[name={mgmt_interface.name}].primary_ipv6",
            value,
        )
    return [gateway_filter(primary_address)]


def attached_vrfs(value: DeviceRenderData) -> list[VRF]:
    """Return the list of attached VRFs."""
    vrfs = set()
    for interface in value.interfaces:
        if interface.vrf:
            vrf = VRF.from_render_data(interface.vrf)
            if vrf:
                vrfs.add(vrf)
    return sorted(list(vrfs), key=lambda x: x.name)


def has_vrf(value: DeviceRenderData, vrf_name: str) -> bool:
    """Return True if the device has the given VRF."""
    return vrf_name in [vrf.name for vrf in attached_vrfs(value)]


def console_server_ports(
    value: DeviceRenderData, connected_only: bool = False
) -> list[ConsoleServerPort]:
    """Return list of console server ports optionally filtered by connection status."""
    ports = [
        ConsoleServerPort.from_render_data(entry) for entry in value.network.console_server_ports
    ]
    if connected_only:
        ports = [port for port in ports if port.connected]
    return ports


# pylint: disable=too-many-locals,too-many-branches,too-many-nested-blocks
# pylint: disable=too-many-statements
def bgp_routing_instance(value: DeviceRenderData, vrf: str = "default") -> BGPLocalConfig:
    """Return a local BGP configuration with its peers."""
    routing_instances = value.routing.bgp_instances
    instance = next((item for item in routing_instances if vrf in item.vrfs), None)
    if instance is None and vrf == "default" and len(routing_instances) == 1:
        instance = routing_instances[0]

    if instance is not None:
        peers = tuple(peer for peer in instance.peers if peer.source_vrf == vrf)
        interface = _required(
            instance.router_id_interface,
            "device.routing.bgp_instances[].router_id_interface",
            value,
        )
        return BGPLocalConfig(
            status=instance.status,
            asn=int(instance.asn),
            interface=interface,
            vrf=vrf,
            peers=[
                BGPPeer(
                    name=peer.name,
                    status=peer.status,
                    description=peer.description,
                    peer_group=peer.peer_group,
                    peer_role=peer.peer_role,
                    asn=int(peer.asn),
                    peer_ipv4=str(peer.peer_ipv4) if peer.peer_ipv4 else None,
                    peer_ipv6=str(peer.peer_ipv6) if peer.peer_ipv6 else None,
                    source_interface=peer.source_interface,
                    source_vrf=peer.source_vrf,
                    ttl=peer.ttl,
                )
                for peer in peers
            ],
        )

    if routing_instances:
        raise FilterException(
            f"Device {hostname(value)} has no BGP routing instance for VRF '{vrf}'."
        )

    instance_asn = _required(value.routing.default_asn, "device.routing.default_asn", value)
    if platform(value) == "Cumulus Linux":
        loopback = interface_by_name(value, "lo")
    elif platform(value) == "Arista EOS":
        loopback = interface_by_name(value, "Loopback0")
    else:
        raise FilterException(f"Unsupported platform: {platform(value)}")
    return BGPLocalConfig(
        status="Active",
        asn=int(instance_asn),
        interface=loopback.name,
        vrf=vrf,
        peers=[],
    )


def _service_endpoints(
    value: DeviceRenderData,
    endpoints: Any,
    service_name: str,
    optional: bool,
) -> list[str]:
    """Return one service's IPv4 endpoints with a clear missing-field error."""
    if endpoints is None:
        if optional:
            return []
        raise FilterException(
            f"Device {hostname(value)} is missing required render field "
            f"'device.services.{service_name.lower().replace('+', '')}'."
        )
    return [str(address) for address in endpoints.ipv4]


def dns_servers(value: DeviceRenderData, optional: bool = True) -> list[str]:
    """Return a list of DNS servers for the device."""
    return _service_endpoints(value, value.services.dns, "DNS", optional)


def ntp_servers(value: DeviceRenderData, optional: bool = True) -> list[str]:
    """Return a list of NTP servers for the device."""
    return _service_endpoints(value, value.services.ntp, "NTP", optional)


def syslog_servers(value: DeviceRenderData, optional: bool = True) -> list[str]:
    """Return a list of Syslog servers for the device."""
    return _service_endpoints(value, value.services.syslog, "syslog", optional)


def tacacs_servers(value: DeviceRenderData, optional: bool = True) -> list[str]:
    """Return a list of TACACS+ servers for the device."""
    return _service_endpoints(value, value.services.tacacs, "TACACS+", optional)


def ztp_servers(value: DeviceRenderData) -> list[str]:
    """Return a list of ZTP servers for the device."""
    return _service_endpoints(value, value.services.ztp, "ZTP", optional=False)


def firmware_cache(value: DeviceRenderData) -> list[str]:
    """Return a list of firmware cache servers, fall back to ZTP servers."""
    if value.services.firmware_cache is None:
        return ztp_servers(value)
    return [str(address) for address in value.services.firmware_cache.ipv4]


def nvlink_topology(value: DeviceRenderData) -> str:
    """Return the NVLink topology for the device."""
    topology = _required(value.network.nvlink_topology, "device.network.nvlink_topology", value)
    return topology.lower()


def firmware_bundle_version(value: DeviceRenderData) -> str:
    """Return the firmware bundle version for this device."""
    return value.firmware.selected_bundle_version or "1.2.0"


def firmware_bundles(value: DeviceRenderData) -> dict[str, Any]:
    """Return the legacy template view of typed firmware bundles."""
    if not value.firmware.bundles:
        raise FilterException(
            f"Device {hostname(value)} is missing required render field 'device.firmware.bundles'."
        )
    return {
        bundle.version: {
            "nv_os": {
                "version": bundle.operating_system.version or "",
                "image_file": bundle.operating_system.image_file or "",
            },
            "firmware": {
                component.name: {
                    "version": component.artifact.version,
                    "file": component.artifact.image_file,
                    "s3_path": component.artifact.source_path,
                }
                for component in bundle.components
            },
        }
        for bundle in value.firmware.bundles
    }


def firmware_bundle(value: DeviceRenderData, bundle_version: str = None) -> dict[str, Any]:
    """Return the specific firmware bundle configuration."""
    if bundle_version is None:
        bundle_version = firmware_bundle_version(value)

    bundles = firmware_bundles(value)
    if bundle_version not in bundles:
        raise FilterException(
            f"Firmware bundle version '{bundle_version}' "
            "not found in firmware_bundles. "
            f"Available versions: {list(bundles.keys())}"
        )

    return bundles[bundle_version]


def firmware_overrides(value: DeviceRenderData) -> dict[str, Any]:
    """Return firmware overrides for this device."""
    return {
        "skip_components": list(value.firmware.overrides.skip_components),
        "custom_components": {
            component.name: {
                "version": component.artifact.version,
                "file": component.artifact.image_file,
                "s3_path": component.artifact.source_path,
            }
            for component in value.firmware.overrides.custom_components
        },
    }


def firmware_component(
    value: DeviceRenderData, component: str, bundle_version: str = None
) -> dict[str, Any] | None:
    """Return firmware configuration for a specific component."""
    bundle = firmware_bundle(value, bundle_version)
    overrides = firmware_overrides(value)

    # Check if component should be skipped
    if component in overrides.get("skip_components", []):
        return None

    # Check for custom component override
    custom_components = overrides.get("custom_components", {})
    if component in custom_components:
        return custom_components[component]

    # Return component from bundle
    firmware = bundle.get("firmware", {})
    return firmware.get(component)


def has_firmware_bundle(value: DeviceRenderData) -> bool:
    """Return True if device has firmware bundle configuration."""
    try:
        firmware_bundles(value)
        return True
    except FilterException:
        return False


def nv_os_version(value: DeviceRenderData, bundle_version: str = None) -> str:
    """Return the NV-OS version for the specified firmware bundle."""
    bundle = firmware_bundle(value, bundle_version)
    nv_os = bundle.get("nv_os", {})
    return nv_os.get("version", "")


def nv_os_image_file(value: DeviceRenderData, bundle_version: str = None) -> str:
    """Return the NV-OS image file name for the specified firmware bundle."""
    bundle = firmware_bundle(value, bundle_version)
    nv_os = bundle.get("nv_os", {})
    return nv_os.get("image_file", "")


def interface_natural_sort_key(interface: Interface) -> tuple[str, list[int]]:
    """
    Create a sort key for interface names that handles various vendor formats.

    Supports formats like:
    - swp1, swp2, swp3
    - swp1s1, swp1s2, swp2s1, swp2s2
    - Ethernet1, Ethernet1/1, Ethernet1/1/1
    - ge-1/1/1, xe-1/1/1
    - swp49.4001, swp50.4001

    Returns a tuple of (prefix, [numeric_parts]) for proper sorting.
    """
    pattern = r"^([\w\-]+?)(\d+(?:[\/s\.]\d+)*)?$"
    match = re.match(pattern, interface.name)
    if not match:
        return (interface.name, [])

    prefix, numeric_part = match.groups()

    numeric_parts = []
    if numeric_part:
        parts = re.split(r"[\/s\.]", numeric_part)
        numeric_parts = [int(part) for part in parts if part.isdigit()]

    return (prefix, numeric_parts)


def helper_addresses_by_vlan(
    value: DeviceRenderData, location_value: LocationRenderData
) -> dict[int, list[str]]:
    """Return mapping of VLAN IDs to helper addresses for VLANs present on device."""
    vlan_interfaces = interfaces(value, prefix="vlan")
    device_vlan_ids = {intf.vlan_number for intf in vlan_interfaces if intf.vlan_number}
    result: dict[int, list[str]] = {}

    for location_vlan in location_value.address_space.vlans:
        vlan_id = location_vlan.vlan.vid
        if vlan_id in device_vlan_ids and location_vlan.helper_addresses:
            result[vlan_id] = sorted(
                {str(address) for address in location_vlan.helper_addresses},
                key=ipaddress.ip_address,
            )

    return result


def helper_addresses_by_vrf(
    value: DeviceRenderData,
    location_value: LocationRenderData,
) -> dict[str, dict[str, list]]:
    """Return mapping of VRFs to their VLANs and helper addresses.

    Args:
        value: Provider-neutral device render data
        location_value: Provider-neutral location render data

    Returns:
        Dictionary mapping VRF names to dicts with 'vlans' and 'helpers' keys:
        {
            "default": {
                "vlans": [900, 901, 998],
                "helpers": ["10.48.175.141", "10.48.175.211"]
            }
        }
    """
    # Get all helper addresses for VLANs on this device
    all_vlan_helpers = helper_addresses_by_vlan(value, location_value)

    # Get all VLAN interfaces and group by VRF
    vlan_interfaces = interfaces(value, prefix="vlan")

    # Build mapping of VRF -> {vlans: [], helpers: []}
    result = {}
    for intf in vlan_interfaces:
        vlan_id = intf.vlan_number
        if vlan_id and vlan_id in all_vlan_helpers:
            vrf_name = intf.vrf
            if vrf_name not in result:
                result[vrf_name] = {"vlans": [], "helpers": set()}

            result[vrf_name]["vlans"].append(vlan_id)
            # Add all helpers for this VLAN to the VRF's helper set
            result[vrf_name]["helpers"].update(all_vlan_helpers[vlan_id])

    # Convert helper sets to sorted lists
    for vrf_config in result.values():
        vrf_config["helpers"] = sorted(list(vrf_config["helpers"]), key=ipaddress.ip_address)

    return result


def users(value: DeviceRenderData) -> list[dict[str, str]]:
    """Return username/role/password_key for each user."""
    if not value.access.credentials:
        raise FilterException(
            f"Device {hostname(value)} is missing required render field "
            "'device.access.credentials'."
        )
    return [
        {
            "username": credential.username,
            "role": (credential.role or "").strip(),
            "password_key": f"{credential.secret_name}_{credential.rotation}",
        }
        for credential in sorted(value.access.credentials, key=lambda item: item.username)
    ]


def _vrf_name_matches(actual: str | None, expected: Any) -> bool:
    """Match Nautobot VRF names, tolerating site-prefixed names used for uniqueness."""
    if not actual:
        return False

    expected_name = str(expected)
    return actual == expected_name or actual.split("_", 1)[-1] == expected_name


def _device_has_vrf(value: DeviceRenderData, vrf_name: Any) -> bool:
    """Return true if the device render data has the requested VRF attached."""
    for vrf in value.network.vrfs:
        if _vrf_name_matches(vrf.name, vrf_name):
            return True

    for interface in value.interfaces:
        if interface.vrf and _vrf_name_matches(interface.vrf.name, vrf_name):
            return True
    return False


def l3vni_mappings(value: DeviceRenderData, vrf_name: Any) -> str:
    """Return the L3 VLAN value for a VRF from overlay plugin VXLAN data."""
    device_name = value.identity.name
    for vxlan in value.overlays.l3_vnis:
        if _vrf_name_matches(vxlan.vrf.name, vrf_name):
            if vxlan.l3_vlan_id is None:
                return ""
            return str(vxlan.l3_vlan_id)

    if _device_has_vrf(value, vrf_name):
        return ""

    raise FilterException(
        f"VRF '{vrf_name}' not found in overlay VXLAN data for device {device_name}"
    )


def vni_mappings(value: DeviceRenderData, vlan_id: Any, fail_if_missing: bool = True) -> str:
    """Return the VNI value for a VLAN from overlay data, optionally allowing no mapping."""
    device_name = value.identity.name
    try:
        vlan_key = int(vlan_id)
    except (TypeError, ValueError) as exc:
        raise FilterException(
            f"Invalid VLAN ID '{vlan_id}' for overlay VXLAN lookup on device {device_name}"
        ) from exc

    for vxlan in value.overlays.l2_vnis:
        if vxlan.vlan.vid == vlan_key:
            if vxlan.vni is None:
                raise FilterException(
                    f"Device {device_name} is missing required render field "
                    f"'device.overlays.l2_vnis[vlan={vlan_id}].vni'."
                )
            return str(vxlan.vni)

    if not fail_if_missing:
        return ""

    raise FilterException(
        f"VLAN {vlan_id} not found in overlay VXLAN data for device {device_name}"
    )


def evpn_esi_mac(value: DeviceRenderData, local_id: int | str) -> str:
    """Compute EVPN ESI MAC address from evpn_esi_base_mac and bond local-id."""
    hostname_val = value.identity.name
    base_mac = _required(
        value.routing.evpn.esi_base_mac,
        "device.routing.evpn.esi_base_mac",
        value,
    )
    try:
        octets = base_mac.split(":")
        if len(octets) != 6:
            raise ValueError("MAC address must contain six octets.")
        base_mac_int = int("".join(f"{int(octet, 16):02x}" for octet in octets), 16)
        esi_mac_int = base_mac_int + int(local_id)
    except (AttributeError, TypeError, ValueError) as exc:
        raise FilterException(
            f"Invalid EVPN ESI MAC inputs for device {hostname_val}: "
            f"base_mac={base_mac}, local_id={local_id}"
        ) from exc

    if esi_mac_int > 0xFFFFFFFFFFFF:
        raise FilterException(
            f"EVPN ESI MAC overflow for device {hostname_val}: "
            f"base_mac={base_mac}, local_id={local_id}"
        )

    return ":".join(f"{(esi_mac_int >> offset) & 0xFF:02x}" for offset in range(40, -1, -8))


def global_fabric_mac(value: DeviceRenderData, fail_if_missing: bool = True) -> str:
    """Get the global fabric MAC address from typed EVPN routing data."""
    fabric_mac = value.routing.evpn.fabric_mac
    if fabric_mac is not None:
        return fabric_mac
    if fail_if_missing:
        raise FilterException(
            f"Device {value.identity.name} is missing required render field "
            "'device.routing.evpn.fabric_mac'."
        )
    return ""


def evpn_df_preference(value: DeviceRenderData) -> int:
    """Return EVPN DF preference, defaulting to 50000."""
    if value.routing.evpn.df_preference is None:
        return 50000
    return value.routing.evpn.df_preference


def spx_subnets(value: DeviceRenderData, ip_version: int = 4) -> list[dict[str, str]]:
    """List Spectrum-X /31 downlink subnets with their rail prefix."""
    results = []
    for intf in value.interfaces:
        if intf.role != "Downlink":
            continue
        if not intf.addresses:
            continue

        for ip_entry in intf.addresses:
            if ip_entry.version != ip_version:
                continue

            if len(ip_entry.parent_prefixes) < 3:
                raise FilterException(
                    f"Incomplete parent hierarchy for interface {intf.name}. "
                    f"Expected /31 -> device prefix -> rail prefix structure."
                )
            subnet = ip_entry.parent_prefixes[0]
            rail_prefix = ip_entry.parent_prefixes[2]

            rail_network = ipaddress.ip_network(rail_prefix)
            if rail_network.prefixlen not in [16, 17]:
                raise FilterException(
                    f"Invalid rail prefix length /{rail_network.prefixlen} for "
                    f"{rail_prefix}. Expected /16 (4-rail) or /17 (8-rail)."
                )

            results.append({"subnet": str(subnet), "rail_prefix": str(rail_prefix)})

    return results


def get_vrf(
    value: DeviceRenderData, vrf_name: str = "", startswith: str = ""
) -> VRF | None | list[VRF]:
    """Return the VRF object for the given VRF name or matching startswith pattern."""
    if vrf_name:
        return next((vrf for vrf in attached_vrfs(value) if vrf.name == vrf_name), None)
    if startswith:
        return [vrf for vrf in attached_vrfs(value) if vrf.name.startswith(startswith)]
    return None


def has_vrf_interfaces(value: DeviceRenderData) -> bool:
    """Return True if the device has any interfaces in non-default VRFs."""
    for interface in value.interfaces:
        if interface.vrf and interface.vrf.name.lower() != "default":
            return True
    return False


def device_aggregate(
    value: DeviceRenderData, peer_role: str, ip_version: int = 4, allow_multiple=False
) -> str | list[str]:
    """Aggregate all p2p interfaces."""
    supernets = set()
    for intf in value.interfaces:
        if intf.role != "Downlink":
            continue
        if not intf.addresses:
            continue
        supernets.update(
            {
                ip_entry.parent_prefixes[1]
                for ip_entry in intf.addresses
                if ip_entry.version == ip_version and len(ip_entry.parent_prefixes) >= 2
            }
        )

    if not supernets:
        raise FilterException("Found zero P2P aggregates on downlinks.")
    if len(supernets) > 1 and not allow_multiple:
        raise FilterException(f"Found multiple P2P aggregates on downlinks: {supernets}")

    peer_interfaces = [
        intf
        for intf in interfaces(value)
        if intf.connected_interface
        and intf.connected_interface.device.role.lower() == peer_role.lower()
    ]
    for peer_intf in peer_interfaces:
        peer_address = peer_intf.primary_ipv4 if ip_version == 4 else peer_intf.primary_ipv6
        if peer_address is None:
            raise FilterException(
                f"Connected peer on interface {peer_intf.name} is missing "
                f"an IPv{ip_version} address."
            )
        peer_net_if = ipaddress.ip_interface(peer_address)
        contained = False
        for supernet in supernets:
            if peer_net_if.network.subnet_of(supernet):
                contained = True
        if not contained:
            raise FilterException(
                f"IP {peer_net_if} on {peer_intf.name} "
                f"is not contained in {[str(s) for s in supernets]}."
            )

    if allow_multiple:
        return [str(supernet) for supernet in sorted(supernets)]
    return str(supernets.pop())


def connected_devices(
    value: DeviceRenderData, peer_role: str | None = None
) -> set[ConnectedDevice]:
    """Return all devices connected to this device."""
    devices = {
        intf.connected_interface.device for intf in interfaces(value) if intf.connected_interface
    }
    if peer_role:
        devices = {device for device in devices if device.role == peer_role}
    return devices


def peer_group_ttl(value: DeviceRenderData, peer_group: str, vrf: str = "default") -> int | None:
    """Return the TTL for the first peer in the given BGP peer group, or None."""
    instance = bgp_routing_instance(value, vrf=vrf)
    for peer in instance.peers:
        if peer.peer_group == peer_group:
            return peer.ttl
    return None


def isis_metric(value: DeviceRenderData, interface_name: str) -> int | None:
    """Retrieve the ISIS metric for the given interface."""
    if not value.routing.isis_interfaces:
        raise FilterException(
            f"Device {hostname(value)} is missing required render field "
            "'device.routing.isis_interfaces'."
        )
    return next(
        (
            interface.metric
            for interface in value.routing.isis_interfaces
            if interface.interface_name == interface_name
        ),
        None,
    )


def dhcp_servers(value: DeviceRenderData, provider: str, optional: bool = True) -> list[str]:
    """Return a list of DHCP servers for the device."""
    endpoints = next(
        (entry.endpoints for entry in value.services.dhcp if entry.name == provider), None
    )
    if endpoints is None:
        if optional:
            return []
        raise FilterException(
            f"Device {hostname(value)} is missing required render field "
            f"'device.services.dhcp[{provider}]'."
        )
    return [str(address) for address in endpoints.ipv4]


def management_prefixes(value: DeviceRenderData) -> list[str]:
    """Return a list of remote prefixes for management traffic."""
    prefixes = _required(
        value.services.management_prefixes,
        "device.services.management_prefixes",
        value,
    )
    return [str(prefix) for prefix in prefixes.ipv4]


def provisioning_servers(value: DeviceRenderData, fail_if_missing: bool = True) -> list[str]:
    """Return a list of provisioning servers."""
    endpoints = value.services.provisioning
    if endpoints is None:
        if fail_if_missing:
            raise FilterException(
                f"Device {hostname(value)} is missing required render field "
                "'device.services.provisioning'."
            )
        return []
    return [str(address) for address in endpoints.ipv4]


def l2vni_vrfs(value: DeviceRenderData) -> list[dict[str, Any]]:
    """Return per-LG L2VNI overlays for the device, sourced from the overlays plugin."""
    return [
        {
            "name": overlay.name,
            "vni": str(overlay.vni) if overlay.vni is not None else "",
            "export_targets": [target.name for target in overlay.export_targets],
            "import_targets": [target.name for target in overlay.import_targets],
        }
        for overlay in sorted(value.overlays.l2_vni_vrfs, key=lambda item: item.name)
    ]
