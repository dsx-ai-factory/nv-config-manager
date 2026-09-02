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
"""Nautobot GraphQL-to-SDK render-data mapping.

This module is the only render path that understands the bundled Nautobot
queries.  It resolves Nautobot configuration contexts and native objects into
the provider-neutral Pydantic models consumed by template filters.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping, Sequence
from typing import Any, cast

from nv_config_manager_dcim.errors import DCIMInvalidDataError
from nv_config_manager_dcim.models import DeviceCertificate
from nv_config_manager_dcim.render import (
    DeviceRenderData,
    LocationRenderData,
    RenderAccessData,
    RenderBGPInstance,
    RenderBGPPeer,
    RenderConnectedDevice,
    RenderConnectedInterface,
    RenderConsoleServerPort,
    RenderCredentialReference,
    RenderData,
    RenderDeviceIdentity,
    RenderEndpointSet,
    RenderEvpnData,
    RenderFirmwareArtifact,
    RenderFirmwareBundle,
    RenderFirmwareComponent,
    RenderFirmwareData,
    RenderFirmwareOverrides,
    RenderInterface,
    RenderIPAddress,
    RenderIsisInterface,
    RenderL2Vni,
    RenderL2VniVrf,
    RenderL3Vni,
    RenderLocation,
    RenderLocationAddressSpace,
    RenderLocationDevice,
    RenderLocationRoutingData,
    RenderLocationTopology,
    RenderLocationVlan,
    RenderNamedEndpointSet,
    RenderNetworkData,
    RenderOtlpData,
    RenderOtlpDestination,
    RenderOverlayData,
    RenderPrefix,
    RenderPrefixSet,
    RenderRouteTarget,
    RenderRoutingData,
    RenderServicesData,
    RenderTelemetryData,
    RenderVlan,
    RenderVrf,
)
from pydantic import ValidationError


def _mapping(value: object, description: str) -> Mapping[str, Any]:
    """Return one required Nautobot mapping with an SDK-level error."""
    if not isinstance(value, Mapping):
        raise DCIMInvalidDataError(f"Nautobot returned invalid {description} render data")
    return value


def _mappings(value: object, description: str) -> tuple[Mapping[str, Any], ...]:
    """Return a sequence of Nautobot mappings, rejecting malformed responses."""
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise DCIMInvalidDataError(f"Nautobot returned invalid {description} render data")
    if not all(isinstance(item, Mapping) for item in value):
        raise DCIMInvalidDataError(f"Nautobot returned invalid {description} render data")
    return tuple(value)


def _is_blank(value: object) -> bool:
    """Return whether a nullable Nautobot scalar has no value."""
    return value is None or value == ""


def _required_text(record: Mapping[str, Any], key: str, description: str) -> str:
    """Return one required text field with an actionable mapping error."""
    value = record.get(key)
    if _is_blank(value):
        raise DCIMInvalidDataError(f"Nautobot {description} is missing required field '{key}'")
    return str(value)


def _optional_text(value: object) -> str | None:
    """Normalize a nullable Nautobot scalar to text."""
    if _is_blank(value):
        return None
    return str(value)


def _named_text(value: object, description: str) -> str | None:
    """Read a named Nautobot object without leaking its response shape."""
    if value is None:
        return None
    record = _mapping(value, description)
    return _optional_text(record.get("name"))


def _required_named_text(value: object, description: str) -> str:
    """Read a required named Nautobot object."""
    name = _named_text(value, description)
    if name is None:
        raise DCIMInvalidDataError(f"Nautobot {description} is missing required field 'name'")
    return name


def _tags(value: object, description: str) -> tuple[str, ...]:
    """Normalize a Nautobot tag collection."""
    return tuple(
        _required_text(tag, "name", f"{description} tag")
        for tag in _mappings(value, f"{description} tags")
    )


def _route_targets(value: object, description: str) -> tuple[RenderRouteTarget, ...]:
    """Normalize route targets from a Nautobot response collection."""
    return tuple(
        RenderRouteTarget(name=_required_text(target, "name", f"{description} route target"))
        for target in _mappings(value, f"{description} route targets")
    )


def _location(value: Mapping[str, Any]) -> RenderLocation:
    """Normalize one Nautobot location and its parent hierarchy."""
    location_type = value.get("location_type")
    parent = value.get("parent")
    return RenderLocation(
        id=_optional_text(value.get("id")),
        name=_required_text(value, "name", "location"),
        kind=_named_text(location_type, "location type"),
        tags=_tags(value.get("tags", ()), "location"),
        parent=_location(_mapping(parent, "location parent"))
        if isinstance(parent, Mapping)
        else None,
    )


def _site_location(location: RenderLocation) -> RenderLocation:
    """Return the site ancestor required by site-level template filters."""
    current: RenderLocation | None = location
    while current is not None:
        if current.kind == "Site":
            return current
        current = current.parent
    raise DCIMInvalidDataError("Nautobot device location has no Site ancestor")


def _vrf(value: object, description: str) -> RenderVrf | None:
    """Normalize an optional Nautobot VRF."""
    if value is None:
        return None
    record = _mapping(value, description)
    return RenderVrf(
        name=_required_text(record, "name", description),
        route_distinguisher=_optional_text(record.get("rd")),
        import_targets=_route_targets(record.get("import_targets", ()), description),
        export_targets=_route_targets(record.get("export_targets", ()), description),
    )


def _vlan(value: object, description: str) -> RenderVlan | None:
    """Normalize an optional Nautobot VLAN."""
    if value is None:
        return None
    record = _mapping(value, description)
    vid = record.get("vid")
    if vid is None:
        raise DCIMInvalidDataError(f"Nautobot {description} is missing required field 'vid'")
    return RenderVlan(vid=int(vid), name=_optional_text(record.get("name")))


def _ip_address(value: Mapping[str, Any], description: str) -> RenderIPAddress:
    """Normalize an IP assignment and flatten its prefix ancestry."""
    address = _required_text(value, "address", description)
    try:
        parsed_address = ipaddress.ip_interface(address)
    except ValueError as exc:
        raise DCIMInvalidDataError(
            f"Nautobot {description} has invalid address '{address}'"
        ) from exc

    host = _optional_text(value.get("host")) or str(parsed_address.ip)
    version = value.get("ip_version") or parsed_address.version
    if int(version) != parsed_address.version:
        raise DCIMInvalidDataError(
            f"Nautobot {description} reports IP version {version} for address '{address}'"
        )

    parents = []
    parent = value.get("parent")
    while isinstance(parent, Mapping):
        prefix = _required_text(parent, "prefix", f"{description} parent prefix")
        parents.append(prefix)
        parent = parent.get("parent")

    return RenderIPAddress(
        address=address,
        host=host,
        version=int(version),
        role=_named_text(value.get("role"), f"{description} role"),
        parent_prefixes=tuple(parents),
    )


def _address_collection(value: object, description: str) -> tuple[RenderIPAddress, ...]:
    """Normalize a Nautobot IP-address collection."""
    return tuple(
        _ip_address(address, f"{description} address")
        for address in _mappings(value, f"{description} addresses")
    )


def _routing_asn_from_instances(
    instances: object, connected_vrf: str | None, description: str
) -> str | None:
    """Find the peer ASN matching an interface VRF from Nautobot BGP instances."""
    records = _mappings(instances, f"{description} BGP routing instances")
    normalized_vrf = _normalize_vrf_name(connected_vrf)
    for instance in records:
        router_id = instance.get("router_id")
        if router_id is None:
            continue
        router_id = _mapping(router_id, f"{description} BGP router ID")
        interfaces = _mappings(
            router_id.get("interfaces"), f"{description} BGP router-ID interfaces"
        )
        instance_vrfs = {
            _normalize_vrf_name(_named_text(interface.get("vrf"), f"{description} BGP VRF"))
            for interface in interfaces
        }
        if not instance_vrfs:
            instance_vrfs = {"default"}
        if normalized_vrf in instance_vrfs:
            autonomous_system = _mapping(
                instance.get("autonomous_system"), f"{description} BGP autonomous system"
            )
            return _required_text(autonomous_system, "asn", f"{description} BGP autonomous system")

    if len(records) == 1:
        autonomous_system = _mapping(
            records[0].get("autonomous_system"), f"{description} BGP autonomous system"
        )
        return _required_text(autonomous_system, "asn", f"{description} BGP autonomous system")
    return None


def _normalize_vrf_name(vrf_name: str | None) -> str:
    """Normalize provider-specific default and site-prefixed VRF names."""
    if not vrf_name:
        return "default"
    if "_" in vrf_name:
        return vrf_name.split("_", 1)[1]
    return vrf_name


def _context(value: object, description: str) -> Mapping[str, Any]:
    """Normalize a nullable Nautobot configuration context mapping."""
    if value is None:
        return {}
    return _mapping(value, description)


def _context_mapping(
    context: Mapping[str, Any], key: str, device_name: str
) -> Mapping[str, Any] | None:
    """Read a structured, optional configuration-context value."""
    value = context.get(key)
    if value is None:
        return None
    return _mapping(value, f"device '{device_name}' config_context.{key}")


def _context_endpoints(
    context: Mapping[str, Any], key: str, device_name: str
) -> RenderEndpointSet | None:
    """Map one named configuration-context endpoint set."""
    value = _context_mapping(context, key, device_name)
    if value is None:
        return None
    return _endpoint_set(value, f"device '{device_name}' config_context.{key}")


def _endpoint_set(value: Mapping[str, Any], description: str) -> RenderEndpointSet:
    """Build one validated endpoint set from an already-normalized mapping."""
    try:
        return RenderEndpointSet(
            ipv4=tuple(value.get("ipv4", ())), ipv6=tuple(value.get("ipv6", ()))
        )
    except ValidationError as exc:
        raise DCIMInvalidDataError(f"Nautobot {description} contains invalid endpoints") from exc


def _context_prefixes(
    context: Mapping[str, Any], key: str, device_name: str
) -> RenderPrefixSet | None:
    """Map one named configuration-context network-prefix set."""
    value = _context_mapping(context, key, device_name)
    if value is None:
        return None
    try:
        return RenderPrefixSet(ipv4=tuple(value.get("ipv4", ())), ipv6=tuple(value.get("ipv6", ())))
    except ValidationError as exc:
        raise DCIMInvalidDataError(
            f"Nautobot device '{device_name}' config_context.{key} contains invalid prefixes"
        ) from exc


def _connected_interface(value: object, device_name: str) -> RenderConnectedInterface | None:
    """Normalize an optional interface peer without exposing raw peer context."""
    if value is None:
        return None
    record = _mapping(value, f"device '{device_name}' connected interface")
    peer_device = record.get("device")
    if not isinstance(peer_device, Mapping):
        module = record.get("module")
        if isinstance(module, Mapping):
            module_bay = module.get("parent_module_bay")
            if isinstance(module_bay, Mapping):
                peer_device = module_bay.get("parent_device")
    if not isinstance(peer_device, Mapping):
        return None

    connected_vrf = _named_text(record.get("vrf"), f"device '{device_name}' connected VRF")
    peer_name = _required_text(peer_device, "name", f"device '{device_name}' connected device")
    peer_context = _context(
        peer_device.get("config_context"), f"device '{peer_name}' configuration context"
    )
    peer_bgp = _context_mapping(peer_context, "bgp", peer_name)
    peer_asn = _routing_asn_from_instances(
        peer_device.get("bgp_routing_instances", ()),
        connected_vrf,
        f"device '{peer_name}'",
    )
    if peer_asn is None and peer_bgp is not None:
        peer_asn = _optional_text(peer_bgp.get("asn"))

    return RenderConnectedInterface(
        name=_required_text(record, "name", f"device '{device_name}' connected interface"),
        vrf=connected_vrf,
        addresses=_address_collection(
            record.get("ip_addresses", ()), f"device '{device_name}' connected interface"
        ),
        device=RenderConnectedDevice(
            id=_optional_text(peer_device.get("id")),
            name=peer_name,
            role=_required_named_text(
                peer_device.get("role"),
                f"device '{peer_name}' connected device role",
            ),
            tenant=_named_text(peer_device.get("tenant"), f"device '{peer_name}' connected tenant"),
            tags=_tags(peer_device.get("tags", ()), f"device '{peer_name}' connected device"),
            routing_asn=peer_asn,
        ),
    )


def _interface(value: Mapping[str, Any], device_name: str) -> RenderInterface:
    """Normalize one Nautobot interface into the typed render contract."""
    interface_name = _required_text(value, "name", f"device '{device_name}' interface")
    mtu = value.get("mtu")
    interface_type = _optional_text(value.get("type"))
    if interface_type is None:
        raise DCIMInvalidDataError(
            f"Nautobot device '{device_name}' interface '{interface_name}' "
            "is missing required field 'type'"
        )
    enabled = value.get("enabled")
    if not isinstance(enabled, bool):
        raise DCIMInvalidDataError(
            f"Nautobot device '{device_name}' interface '{interface_name}' "
            "is missing required boolean field 'enabled'"
        )

    untagged_vlan = _vlan(value.get("untagged_vlan"), f"interface '{interface_name}' untagged VLAN")
    return RenderInterface(
        name=interface_name,
        type=interface_type,
        mtu=int(mtu) if mtu is not None else None,
        enabled=enabled,
        tags=_tags(value.get("tags", ()), f"interface '{interface_name}'"),
        description=_optional_text(value.get("description")) or "",
        role=_named_text(value.get("role"), f"interface '{interface_name}' role"),
        mac_address=_optional_text(value.get("mac_address")),
        vrf=_vrf(value.get("vrf"), f"interface '{interface_name}' VRF"),
        management_only=bool(value.get("mgmt_only")),
        member_interfaces=tuple(
            _required_text(member, "name", f"interface '{interface_name}' member")
            for member in _mappings(value.get("member_interfaces", ()), "interface members")
        ),
        parent_interface=_named_text(
            value.get("parent_interface"), f"interface '{interface_name}' parent interface"
        ),
        untagged_vlan=untagged_vlan,
        tagged_vlans=tuple(
            vlan
            for item in _mappings(value.get("tagged_vlans", ()), "tagged VLANs")
            if (vlan := _vlan(item, f"interface '{interface_name}' tagged VLAN")) is not None
        ),
        addresses=_address_collection(
            value.get("ip_addresses", ()), f"interface '{interface_name}'"
        ),
        connected_interface=_connected_interface(value.get("connected_interface"), device_name),
    )


def _bgp_peer(value: Mapping[str, Any], device_name: str) -> RenderBGPPeer | None:
    """Normalize one Nautobot BGP endpoint into a template peer."""
    peer = value.get("peer")
    if not isinstance(peer, Mapping):
        return None
    if peer.get("source_interface") is None:
        raise DCIMInvalidDataError(
            f"Nautobot device '{device_name}' BGP peer is missing required source_interface; "
            "the Nautobot BGP plugin does not expose the VRF association on source_ip"
        )
    source_interface = _mapping(
        peer.get("source_interface"), f"device '{device_name}' BGP peer interface"
    )
    routing_instance = _mapping(
        peer.get("routing_instance"), f"device '{device_name}' BGP peer routing instance"
    )
    peer_device = _mapping(
        routing_instance.get("device"), f"device '{device_name}' BGP peer device"
    )
    autonomous_system = _mapping(
        routing_instance.get("autonomous_system"),
        f"device '{device_name}' BGP peer autonomous system",
    )
    source_vrf = "default"
    source_interface_data = value.get("source_interface")
    if isinstance(source_interface_data, Mapping):
        source_vrf = _normalize_vrf_name(
            _named_text(source_interface_data.get("vrf"), f"device '{device_name}' BGP source VRF")
        )

    peer_group = value.get("peer_group")
    peer_group_name = _named_text(peer_group, f"device '{device_name}' BGP peer group")
    peer_role = _required_named_text(
        peer_device.get("role"), f"device '{device_name}' BGP peer role"
    )
    if peer_group_name is None:
        peer_group_name = peer_role.upper()
    ttl = None
    if isinstance(peer_group, Mapping):
        extra_attributes = peer_group.get("extra_attributes")
        if isinstance(extra_attributes, Mapping) and extra_attributes.get("ttl") is not None:
            ttl = int(extra_attributes["ttl"])
        elif isinstance(peer_group.get("peergroup_template"), Mapping):
            template_attributes = peer_group["peergroup_template"].get("extra_attributes")
            if (
                isinstance(template_attributes, Mapping)
                and template_attributes.get("ttl") is not None
            ):
                ttl = int(template_attributes["ttl"])

    addresses = _address_collection(
        source_interface.get("ip_addresses", ()), f"device '{device_name}' BGP peer interface"
    )
    peer_name = _required_text(peer_device, "name", f"device '{device_name}' BGP peer device")
    return RenderBGPPeer(
        name=peer_name,
        status=_required_named_text(
            routing_instance.get("status"), f"device '{device_name}' BGP peer status"
        ),
        description=_optional_text(peer.get("description")) or peer_name,
        peer_group=peer_group_name,
        peer_role=peer_role,
        asn=_required_text(
            autonomous_system, "asn", f"device '{device_name}' BGP peer autonomous system"
        ),
        peer_ipv4=next((address.host for address in addresses if address.version == 4), None),
        peer_ipv6=next((address.host for address in addresses if address.version == 6), None),
        source_interface=_optional_text(source_interface.get("name")),
        source_vrf=source_vrf,
        ttl=ttl,
    )


def _bgp_instances(value: object, device_name: str) -> tuple[RenderBGPInstance, ...]:
    """Normalize Nautobot BGP routing instances."""
    result = []
    for instance in _mappings(value, f"device '{device_name}' BGP routing instances"):
        router_id = instance.get("router_id")
        router_interfaces = (
            _mappings(
                _mapping(router_id, f"device '{device_name}' BGP router ID").get("interfaces"),
                f"device '{device_name}' BGP router-ID interfaces",
            )
            if router_id is not None
            else ()
        )
        autonomous_system = _mapping(
            instance.get("autonomous_system"), f"device '{device_name}' BGP autonomous system"
        )
        peers = tuple(
            peer
            for endpoint in _mappings(instance.get("endpoints", ()), "BGP endpoints")
            if (peer := _bgp_peer(endpoint, device_name)) is not None
        )
        vrfs = tuple(
            sorted(
                {
                    *(
                        _normalize_vrf_name(
                            _named_text(
                                interface.get("vrf"),
                                f"device '{device_name}' BGP router-ID VRF",
                            )
                        )
                        for interface in router_interfaces
                    ),
                    *(peer.source_vrf for peer in peers),
                }
            )
        ) or ("default",)
        result.append(
            RenderBGPInstance(
                status=_required_named_text(
                    instance.get("status"), f"device '{device_name}' BGP status"
                ),
                asn=_required_text(
                    autonomous_system, "asn", f"device '{device_name}' BGP autonomous system"
                ),
                router_id_interface=_optional_text(router_interfaces[0].get("name"))
                if router_interfaces
                else None,
                vrfs=vrfs,
                peers=peers,
            )
        )
    return tuple(result)


def _firmware_artifact(value: Mapping[str, Any], image_key: str = "file") -> RenderFirmwareArtifact:
    """Map a firmware artifact from a context record."""
    return RenderFirmwareArtifact(
        version=_optional_text(value.get("version")),
        image_file=_optional_text(value.get(image_key)),
        source_path=_optional_text(value.get("s3_path")),
    )


def _firmware_data(context: Mapping[str, Any], device_name: str) -> RenderFirmwareData:
    """Map typed firmware render data from Nautobot's effective policy context."""
    target = _context_mapping(context, "intended-firmware", device_name)
    desired_version = _optional_text(target.get("version")) if target is not None else None
    raw_bundles = _context_mapping(context, "firmware_bundles", device_name)
    bundles = []
    if raw_bundles is not None:
        for version, raw_bundle in raw_bundles.items():
            bundle = _mapping(raw_bundle, f"device '{device_name}' firmware bundle '{version}'")
            raw_os = _mapping(
                bundle.get("nv_os"),
                f"device '{device_name}' firmware bundle '{version}' operating system",
            )
            raw_components = _context_mapping(bundle, "firmware", device_name) or {}
            components = []
            for name, raw_component in raw_components.items():
                component = _mapping(
                    raw_component, f"device '{device_name}' firmware component '{name}'"
                )
                components.append(
                    RenderFirmwareComponent(
                        name=str(name),
                        artifact=_firmware_artifact(component),
                    )
                )
            bundles.append(
                RenderFirmwareBundle(
                    version=str(version),
                    operating_system=_firmware_artifact(raw_os, "image_file"),
                    components=tuple(components),
                )
            )

    raw_overrides = _context_mapping(context, "firmware_overrides", device_name) or {}
    raw_custom_components = _context_mapping(raw_overrides, "custom_components", device_name) or {}
    custom_components = []
    for name, raw_component in raw_custom_components.items():
        component = _mapping(raw_component, f"device '{device_name}' firmware override '{name}'")
        custom_components.append(
            RenderFirmwareComponent(
                name=str(name),
                artifact=_firmware_artifact(component),
            )
        )
    skip_components = raw_overrides.get("skip_components", ())
    if not isinstance(skip_components, Sequence) or isinstance(skip_components, (str, bytes)):
        raise DCIMInvalidDataError(
            f"Nautobot device '{device_name}' "
            "config_context.firmware_overrides.skip_components must be a list"
        )
    return RenderFirmwareData(
        desired_version=desired_version,
        selected_bundle_version=_optional_text(context.get("firmware_bundle_version")),
        bundles=tuple(bundles),
        overrides=RenderFirmwareOverrides(
            skip_components=tuple(str(component) for component in skip_components),
            custom_components=tuple(custom_components),
        ),
    )


def _services_data(context: Mapping[str, Any], device_name: str) -> RenderServicesData:
    """Map typed effective network-service policy from a configuration context."""
    raw_dhcp = _context_mapping(context, "dhcp", device_name) or {}
    dhcp = tuple(
        RenderNamedEndpointSet(
            name=str(provider),
            endpoints=_endpoint_set(
                _mapping(value, f"device '{device_name}' DHCP provider '{provider}'"),
                f"device '{device_name}' DHCP provider '{provider}'",
            ),
        )
        for provider, value in raw_dhcp.items()
    )
    return RenderServicesData(
        dns=_context_endpoints(context, "dns", device_name),
        ntp=_context_endpoints(context, "ntp", device_name),
        syslog=_context_endpoints(context, "syslog", device_name),
        tacacs=_context_endpoints(context, "tacacs", device_name),
        ztp=_context_endpoints(context, "ztp", device_name),
        firmware_cache=_context_endpoints(context, "firmware_cache", device_name),
        provisioning=_context_endpoints(context, "provisioning_servers", device_name),
        dhcp=dhcp,
        management_prefixes=_context_prefixes(context, "management_prefixes", device_name),
    )


def _access_data(context: Mapping[str, Any], device_name: str) -> RenderAccessData:
    """Map account secret references from a configuration context."""
    raw_mappings = _context_mapping(context, "password_mappings", device_name)
    if raw_mappings is None:
        return RenderAccessData()
    credentials = []
    for username, raw_credential in raw_mappings.items():
        credential = _mapping(
            raw_credential, f"device '{device_name}' password mapping '{username}'"
        )
        secret_name = _optional_text(credential.get("password"))
        rotation = _optional_text(credential.get("rotation"))
        if secret_name is None or rotation is None:
            raise DCIMInvalidDataError(
                f"Nautobot device '{device_name}' password mapping '{username}' requires "
                "both 'password' and 'rotation'"
            )
        credentials.append(
            RenderCredentialReference(
                username=str(username),
                secret_name=secret_name,
                rotation=rotation,
                role=_optional_text(credential.get("role")),
            )
        )
    return RenderAccessData(credentials=tuple(credentials))


def _certificates_data(
    context: Mapping[str, Any], device_name: str
) -> tuple[DeviceCertificate, ...]:
    """Map the ordered certificate assignments used by ZTP and templates."""
    return tuple(
        DeviceCertificate.model_validate(certificate)
        for certificate in _mappings(
            context.get("certificates", ()),
            f"device '{device_name}' configuration context certificates",
        )
    )


def _telemetry_data(context: Mapping[str, Any], device_name: str) -> RenderTelemetryData:
    """Map an optional, full-mTLS OTLP export policy."""
    telemetry = _context_mapping(context, "telemetry", device_name)
    if telemetry is None:
        return RenderTelemetryData()
    otlp = _context_mapping(telemetry, "otlp", device_name)
    if otlp is None:
        return RenderTelemetryData()
    destinations = tuple(
        RenderOtlpDestination.model_validate(destination)
        for destination in _mappings(
            otlp.get("destinations", ()),
            f"device '{device_name}' telemetry OTLP destinations",
        )
    )
    return RenderTelemetryData(
        otlp=RenderOtlpData(
            ca_certificate=_optional_text(otlp.get("ca_certificate")),
            destinations=destinations,
        )
    )


def _routing_data(
    device: Mapping[str, Any], context: Mapping[str, Any], device_name: str, site_asn: str | None
) -> RenderRoutingData:
    """Map native BGP plus effective routing policy into one typed section."""
    bgp_context = _context_mapping(context, "bgp", device_name)
    isis_context = _context_mapping(context, "isis", device_name)
    isis_interfaces = ()
    if isis_context is not None:
        raw_interfaces = _context_mapping(isis_context, "interfaces", device_name) or {}
        isis_interfaces = tuple(
            RenderIsisInterface(
                interface_name=str(name), metric=int(metric) if metric is not None else None
            )
            for name, metric in raw_interfaces.items()
        )
    evpn_context = _context_mapping(context, "evpn", device_name)
    return RenderRoutingData(
        bgp_instances=_bgp_instances(device.get("bgp_routing_instances", ()), device_name),
        default_asn=_optional_text(bgp_context.get("asn")) if bgp_context is not None else None,
        local_asn=_optional_text(bgp_context.get("local-asn")) if bgp_context is not None else None,
        site_asn=site_asn,
        isis_interfaces=isis_interfaces,
        evpn=RenderEvpnData(
            esi_base_mac=_optional_text(context.get("evpn_esi_base_mac")),
            fabric_mac=_optional_text(context.get("fabric-mac")),
            df_preference=int(evpn_context["df-preference"])
            if evpn_context is not None and evpn_context.get("df-preference") is not None
            else None,
        ),
    )


def _l2_vnis(
    raw_vxlans: Sequence[Mapping[str, Any]],
    assigned_overlay_ids: set[str],
    assigned_overlay_names: set[str],
    device_vlan_ids: set[str],
    device_vlan_vids: set[int],
    device_name: str,
) -> tuple[list[RenderL2Vni], dict[str, RenderL2Vni], dict[str, RenderL2Vni]]:
    """Map the L2 VXLANs assigned to a device and index them for VRF mapping."""
    l2_vnis: list[RenderL2Vni] = []
    l2_by_id: dict[str, RenderL2Vni] = {}
    l2_by_overlay: dict[str, RenderL2Vni] = {}
    for raw_vxlan in raw_vxlans:
        vni_type = _optional_text(raw_vxlan.get("vni_type"))
        raw_overlay = raw_vxlan.get("overlay")
        overlay_name = _named_text(raw_overlay, f"device '{device_name}' VXLAN overlay")
        overlay_id = (
            _optional_text(raw_overlay.get("id")) if isinstance(raw_overlay, Mapping) else None
        )
        raw_vni = raw_vxlan.get("vnid")
        vni = int(cast(str | int, raw_vni)) if not _is_blank(raw_vni) else None
        if not vni_type or vni_type.lower() != "l2":
            continue
        raw_vlan = raw_vxlan.get("vlan")
        raw_vlan_id = _optional_text(raw_vlan.get("id")) if isinstance(raw_vlan, Mapping) else None
        raw_vlan_vid = (
            int(raw_vlan["vid"])
            if isinstance(raw_vlan, Mapping) and not _is_blank(raw_vlan.get("vid"))
            else None
        )
        overlay_is_assigned = (
            overlay_id in assigned_overlay_ids
            if assigned_overlay_ids and overlay_id is not None
            else overlay_name in assigned_overlay_names
        )
        vlan_is_attached = (
            raw_vlan_id in device_vlan_ids
            if device_vlan_ids and raw_vlan_id is not None
            else raw_vlan_vid in device_vlan_vids
        )
        if not overlay_is_assigned and not vlan_is_attached:
            continue
        vlan = _vlan(raw_vlan, f"device '{device_name}' L2 VNI VLAN")
        if vlan is None:
            raise DCIMInvalidDataError(
                f"Nautobot device '{device_name}' L2 VNI is missing its VLAN assignment"
            )
        l2_vni = RenderL2Vni(
            vlan=vlan,
            vni=vni,
            overlay_name=overlay_name,
            import_targets=_route_targets(raw_vxlan.get("import_targets", ()), "L2 VNI"),
            export_targets=_route_targets(raw_vxlan.get("export_targets", ()), "L2 VNI"),
        )
        l2_vnis.append(l2_vni)
        if raw_vxlan.get("id") is not None:
            l2_by_id[str(raw_vxlan["id"])] = l2_vni
        if overlay_name is not None:
            l2_by_overlay[overlay_name] = l2_vni
    return l2_vnis, l2_by_id, l2_by_overlay


def _l3_vnis(
    raw_vxlans: Sequence[Mapping[str, Any]],
    device_vrf_ids: set[str],
    device_vrf_names: set[str],
    device_name: str,
) -> list[RenderL3Vni]:
    """Map the L3 VXLANs associated with a device VRF."""
    l3_vnis: list[RenderL3Vni] = []
    for raw_vxlan in raw_vxlans:
        vni_type = _optional_text(raw_vxlan.get("vni_type"))
        raw_overlay = raw_vxlan.get("overlay")
        overlay_name = _named_text(raw_overlay, f"device '{device_name}' VXLAN overlay")
        raw_vni = raw_vxlan.get("vnid")
        vni = int(cast(str | int, raw_vni)) if not _is_blank(raw_vni) else None
        if not vni_type or vni_type.lower() != "l3":
            continue
        raw_vrf = raw_vxlan.get("vrf")
        raw_vrf_id = _optional_text(raw_vrf.get("id")) if isinstance(raw_vrf, Mapping) else None
        raw_vrf_name = _optional_text(raw_vrf.get("name")) if isinstance(raw_vrf, Mapping) else None
        if device_vrf_ids and raw_vrf_id is not None:
            if raw_vrf_id not in device_vrf_ids:
                continue
        elif raw_vrf_name not in device_vrf_names:
            continue
        vrf = _vrf(raw_vrf, f"device '{device_name}' L3 VNI VRF")
        if vrf is None:
            raise DCIMInvalidDataError(
                f"Nautobot device '{device_name}' L3 VNI is missing its VRF assignment"
            )
        raw_l3_vlan_id = raw_vxlan.get("l3_vlan_id")
        l3_vnis.append(
            RenderL3Vni(
                vrf=vrf,
                l3_vlan_id=(
                    int(cast(str | int, raw_l3_vlan_id)) if not _is_blank(raw_l3_vlan_id) else None
                ),
                vni=vni,
                overlay_name=overlay_name,
            )
        )
    return l3_vnis


def _l2_vni_vrfs(
    raw_assignments: Sequence[Mapping[str, Any]],
    l2_by_id: Mapping[str, RenderL2Vni],
    l2_by_overlay: Mapping[str, RenderL2Vni],
) -> list[RenderL2VniVrf]:
    """Map overlay assignments to the L2 VNIs used as VRF-facing interfaces."""
    l2_vni_vrfs: list[RenderL2VniVrf] = []

    for assignment in raw_assignments:
        object_type = assignment.get("assigned_object_type")
        if isinstance(object_type, Mapping) and not (
            object_type.get("app_label") == "dcim"
            and str(object_type.get("model", "")).lower() == "device"
        ):
            continue
        overlay = _mapping(assignment.get("overlay"), "overlay assignment overlay")
        overlay_name = _optional_text(overlay.get("name"))
        if overlay_name is None:
            continue
        l2_vni = l2_by_overlay.get(overlay_name)
        assignment_with_targets: Mapping[str, Any] | None = None
        if l2_vni is None:
            for candidate in _mappings(overlay.get("assignments", ()), "overlay child assignments"):
                candidate_type = candidate.get("assigned_object_type")
                is_vxlan = isinstance(candidate_type, Mapping) and (
                    candidate_type.get("app_label") == "nautobot_app_overlays"
                    and str(candidate_type.get("model", "")).lower() == "vxlan"
                )
                has_targets = candidate.get("export_targets") or candidate.get("import_targets")
                if is_vxlan or has_targets:
                    assignment_with_targets = candidate
                    l2_vni = l2_by_id.get(str(candidate.get("assigned_object_id")))
                    break
        if l2_vni is None:
            continue
        import_targets = l2_vni.import_targets
        export_targets = l2_vni.export_targets
        if not import_targets and not export_targets and assignment_with_targets is not None:
            import_targets = _route_targets(
                assignment_with_targets.get("import_targets", ()), "overlay assignment"
            )
            export_targets = _route_targets(
                assignment_with_targets.get("export_targets", ()), "overlay assignment"
            )
        l2_vni_vrfs.append(
            RenderL2VniVrf(
                name=overlay_name,
                vni=l2_vni.vni,
                import_targets=import_targets,
                export_targets=export_targets,
            )
        )
    return l2_vni_vrfs


def _overlay_data(
    payload: Mapping[str, Any], device: Mapping[str, Any], device_name: str
) -> RenderOverlayData:
    """Map Nautobot overlay plugin records into typed L2/L3 VNI concepts."""
    raw_vxlans = _mappings(payload.get("vxlans", ()), f"device '{device_name}' VXLANs")
    raw_assignments = _mappings(payload.get("overlay_assignments", ()), "overlay assignments")
    assigned_overlay_ids: set[str] = set()
    assigned_overlay_names: set[str] = set()
    for assignment in raw_assignments:
        object_type = assignment.get("assigned_object_type")
        if isinstance(object_type, Mapping) and not (
            object_type.get("app_label") == "dcim"
            and str(object_type.get("model", "")).lower() == "device"
        ):
            continue
        overlay = _mapping(assignment.get("overlay"), "overlay assignment overlay")
        if overlay.get("id") is not None:
            assigned_overlay_ids.add(str(overlay["id"]))
        if (overlay_name := _optional_text(overlay.get("name"))) is not None:
            assigned_overlay_names.add(overlay_name)

    raw_device_vrfs = list(_mappings(device.get("vrfs", ()), "device VRFs"))
    raw_device_vrfs.extend(
        interface["vrf"]
        for interface in _mappings(device.get("interfaces", ()), "device interfaces")
        if isinstance(interface.get("vrf"), Mapping)
    )
    device_vrf_ids = {str(vrf["id"]) for vrf in raw_device_vrfs if vrf.get("id") is not None}
    device_vrf_names = {
        str(vrf["name"]) for vrf in raw_device_vrfs if not _is_blank(vrf.get("name"))
    }
    raw_device_vlans = [
        vlan
        for interface in _mappings(device.get("interfaces", ()), "device interfaces")
        for vlan in (
            interface.get("untagged_vlan"),
            *_mappings(interface.get("tagged_vlans", ()), "interface tagged VLANs"),
        )
        if isinstance(vlan, Mapping)
    ]
    device_vlan_ids = {str(vlan["id"]) for vlan in raw_device_vlans if vlan.get("id") is not None}
    device_vlan_vids = {
        int(vlan["vid"]) for vlan in raw_device_vlans if not _is_blank(vlan.get("vid"))
    }

    l2_vnis, l2_by_id, l2_by_overlay = _l2_vnis(
        raw_vxlans,
        assigned_overlay_ids,
        assigned_overlay_names,
        device_vlan_ids,
        device_vlan_vids,
        device_name,
    )
    l3_vnis = _l3_vnis(raw_vxlans, device_vrf_ids, device_vrf_names, device_name)
    l2_vni_vrfs = _l2_vni_vrfs(raw_assignments, l2_by_id, l2_by_overlay)

    return RenderOverlayData(
        l2_vnis=tuple(l2_vnis),
        l3_vnis=tuple(l3_vnis),
        l2_vni_vrfs=tuple(l2_vni_vrfs),
    )


def _location_device(value: Mapping[str, Any], description: str) -> RenderLocationDevice:
    """Map one routing-relevant device returned by the location query."""
    device_name = _required_text(value, "name", description)
    context = _context(value.get("config_context"), f"device '{device_name}' configuration context")
    bgp = _context_mapping(context, "bgp", device_name)
    loopbacks = []
    for interface in _mappings(
        value.get("interfaces", ()), f"device '{device_name}' loopback interfaces"
    ):
        for address in _mappings(
            interface.get("ip_addresses", ()), f"device '{device_name}' loopback addresses"
        ):
            host = _required_text(address, "host", f"device '{device_name}' loopback address")
            try:
                loopbacks.append(ipaddress.ip_address(host))
            except ValueError as exc:
                raise DCIMInvalidDataError(
                    f"Nautobot device '{device_name}' has invalid loopback address '{host}'"
                ) from exc
    return RenderLocationDevice(
        name=device_name,
        routing_asn=_optional_text(bgp.get("asn")) if bgp is not None else None,
        loopback_addresses=tuple(loopbacks),
    )


def _location_data(
    payload: Mapping[str, Any], device_location: RenderLocation, fallback_site_asn: str | None
) -> LocationRenderData:
    """Map the location render query into typed location data."""
    locations = _mappings(payload.get("locations", ()), "render locations")
    if not locations:
        raise DCIMInvalidDataError("Nautobot returned incomplete render location data")
    location_record = locations[0]
    site = _site_location(device_location)
    location_tags = _tags(location_record.get("tags", ()), "render location")
    if location_tags:
        site = site.model_copy(update={"tags": location_tags})

    prefixes = tuple(
        RenderPrefix(
            prefix=_required_text(prefix, "prefix", "location prefix"),
            role=_named_text(prefix.get("role"), "location prefix role"),
            tags=_tags(prefix.get("tags", ()), "location prefix"),
        )
        for prefix in _mappings(payload.get("prefixes", ()), "location prefixes")
    )
    uc_jumphost_prefixes = tuple(
        _required_text(prefix, "prefix", "UC jumphost prefix")
        for prefix in _mappings(payload.get("uc_jumphost_prefixes", ()), "UC jumphost prefixes")
    )
    vlans = []
    for raw_vlan in _mappings(payload.get("vlans", ()), "location VLANs"):
        vlan = _vlan(raw_vlan, "location VLAN")
        if vlan is None:
            continue
        helpers = []
        for helper in _mappings(
            raw_vlan.get("rel_vlan_to_helper_address", ()), "VLAN helper addresses"
        ):
            host = _required_text(helper, "host", f"VLAN {vlan.vid} helper address")
            try:
                helpers.append(ipaddress.ip_address(host))
            except ValueError as exc:
                raise DCIMInvalidDataError(
                    f"Nautobot VLAN {vlan.vid} has invalid helper address '{host}'"
                ) from exc
        vlans.append(RenderLocationVlan(vlan=vlan, helper_addresses=tuple(helpers)))

    return LocationRenderData(
        location=site,
        routing=RenderLocationRoutingData(site_asn=fallback_site_asn),
        address_space=RenderLocationAddressSpace(
            prefixes=prefixes,
            uc_jumphost_prefixes=uc_jumphost_prefixes,
            vlans=tuple(vlans),
        ),
        topology=RenderLocationTopology(
            route_servers=tuple(
                _location_device(device, "route server")
                for device in _mappings(payload.get("route_servers", ()), "route servers")
            ),
            wan_routers=tuple(
                _location_device(device, "WAN router")
                for device in _mappings(payload.get("wan_devices", ()), "WAN routers")
            ),
        ),
    )


def _console_server_port(value: Mapping[str, Any]) -> RenderConsoleServerPort:
    """Normalize one Nautobot console-server port."""
    port_name = _required_text(value, "name", "console server port")
    connected_port = value.get("connected_console_port")
    if not isinstance(connected_port, Mapping):
        return RenderConsoleServerPort(name=port_name)
    connected_device = _mapping(connected_port.get("device"), "connected console device")
    device_type = _mapping(connected_device.get("device_type"), "connected console device type")
    manufacturer = _mapping(device_type.get("manufacturer"), "connected console manufacturer")
    return RenderConsoleServerPort(
        name=port_name,
        connected_device_name=_required_text(connected_device, "name", "connected console device"),
        connected_device_manufacturer=_required_text(
            manufacturer, "name", "connected console manufacturer"
        ),
        connected_port_name=_required_text(connected_port, "name", "connected console port"),
    )


def _build_render_data(
    device_response: Mapping[str, Any], location_response: Mapping[str, Any]
) -> RenderData:
    """Map the Nautobot render queries into the provider-neutral contract."""
    payload = _mapping(device_response.get("data"), "device")
    device = _mapping(payload.get("device"), "device")
    device_name = _required_text(device, "name", "device")
    platform = _mapping(device.get("platform"), "device platform")
    role = _mapping(device.get("role"), "device role")
    device_type = _mapping(device.get("device_type"), "device type")
    location = _mapping(device.get("location"), "device location")
    context = _context(
        device.get("config_context"), f"device '{device_name}' configuration context"
    )
    fallback_site_asn = _optional_text(context.get("site_asn"))
    location_payload = _mapping(location_response.get("data"), "location")
    rendered_location = _location(location)
    location_data = _location_data(location_payload, rendered_location, fallback_site_asn)
    site_asn = location_data.routing.site_asn

    raw_nvlink_domains = _mappings(device.get("nvlink_domain", ()), "device NVLink domains")
    nvlink_topology = (
        _optional_text(raw_nvlink_domains[0].get("topology")) if raw_nvlink_domains else None
    )
    return RenderData(
        device=DeviceRenderData(
            identity=RenderDeviceIdentity(
                id=_required_text(device, "id", "device"),
                name=device_name,
                platform=_required_text(platform, "name", "device platform"),
                role=_required_text(role, "name", "device role"),
                model=_required_text(device_type, "model", "device type"),
                location=rendered_location,
                tags=_tags(device.get("tags", ()), "device"),
            ),
            interfaces=tuple(
                _interface(interface, device_name)
                for interface in _mappings(device.get("interfaces", ()), "device interfaces")
            ),
            network=RenderNetworkData(
                vrfs=tuple(
                    vrf
                    for raw_vrf in _mappings(device.get("vrfs", ()), "device VRFs")
                    if (vrf := _vrf(raw_vrf, "device VRF")) is not None
                ),
                console_server_ports=tuple(
                    _console_server_port(port)
                    for port in _mappings(
                        device.get("console_server_ports", ()), "console server ports"
                    )
                ),
                nvlink_topology=nvlink_topology,
            ),
            routing=_routing_data(device, context, device_name, site_asn),
            overlays=_overlay_data(payload, device, device_name),
            firmware=_firmware_data(context, device_name),
            services=_services_data(context, device_name),
            access=_access_data(context, device_name),
            certificates=_certificates_data(context, device_name),
            telemetry=_telemetry_data(context, device_name),
        ),
        location=location_data,
    )


def build_render_data(
    device_response: Mapping[str, Any], location_response: Mapping[str, Any]
) -> RenderData:
    """Map Nautobot data and expose only SDK-level data-contract errors."""
    try:
        return _build_render_data(device_response, location_response)
    except DCIMInvalidDataError:
        raise
    except (TypeError, ValueError, ValidationError) as exc:
        raise DCIMInvalidDataError(
            f"Nautobot render data failed provider-neutral validation: {exc}"
        ) from exc
