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
"""Device filter tests."""

import pytest
from nv_config_manager_dcim import (
    DeviceRenderData,
    LocationRenderData,
    RenderAccessData,
    RenderBGPInstance,
    RenderCredentialReference,
    RenderDeviceIdentity,
    RenderEndpointSet,
    RenderFirmwareData,
    RenderInterface,
    RenderIsisInterface,
    RenderL3Vni,
    RenderLocation,
    RenderLocationAddressSpace,
    RenderLocationVlan,
    RenderNamedEndpointSet,
    RenderNetworkData,
    RenderOverlayData,
    RenderPrefixSet,
    RenderRoutingData,
    RenderServicesData,
    RenderVlan,
    RenderVrf,
)

from nv_config_manager_templates.dataclasses.interface import Interface
from nv_config_manager_templates.dataclasses.vrf import VRF
from nv_config_manager_templates.filters import FilterException
from nv_config_manager_templates.filters.device import (
    asn,
    attached_vrfs,
    bgp_routing_instance,
    breakout_count,
    connected_devices,
    default_gateways,
    desired_firmware,
    device_tags,
    dhcp_servers,
    dns_servers,
    evpn_df_preference,
    evpn_esi_mac,
    firmware_bundle,
    firmware_bundle_version,
    firmware_bundles,
    firmware_cache,
    firmware_component,
    firmware_overrides,
    global_fabric_mac,
    has_firmware_bundle,
    has_tag,
    has_vrf,
    has_vrf_interfaces,
    helper_addresses_by_vlan,
    helper_addresses_by_vrf,
    hostname,
    interface_by_name,
    interface_has_tag,
    interfaces,
    isis_metric,
    l2vni_vrfs,
    l3vni_mappings,
    loopback_prefix,
    management_interface,
    management_prefixes,
    model,
    ntp_servers,
    nv_os_image_file,
    nv_os_version,
    platform,
    provisioning_servers,
    role,
    router_id,
    site_name,
    spx_subnets,
    syslog_servers,
    users,
    uuid,
    vni_mappings,
    ztp_servers,
)


def _render_device(
    name: str = "test-device",
    *,
    interfaces: tuple[RenderInterface, ...] = (),
    network: RenderNetworkData | None = None,
    routing: RenderRoutingData | None = None,
    overlays: RenderOverlayData | None = None,
    firmware: RenderFirmwareData | None = None,
    services: RenderServicesData | None = None,
    access: RenderAccessData | None = None,
) -> DeviceRenderData:
    """Build compact provider-neutral device data for focused filter tests."""
    return DeviceRenderData(
        identity=RenderDeviceIdentity(
            id=name,
            name=name,
            platform="Cumulus Linux",
            role="Leaf",
            model="SN5600",
            location=RenderLocation(name="TEST-SITE", kind="Site"),
        ),
        interfaces=interfaces,
        network=network or RenderNetworkData(),
        routing=routing or RenderRoutingData(),
        overlays=overlays or RenderOverlayData(),
        firmware=firmware or RenderFirmwareData(),
        services=services or RenderServicesData(),
        access=access or RenderAccessData(),
    )


def test_basic_device_fields(public_leaf_data: dict, public_tor_data: dict) -> None:
    """Basic device metadata is extracted from public fixtures."""
    assert hostname(public_leaf_data) == "a08-u32-p01-cleaf-01"
    assert site_name(public_leaf_data) == "TEST-SITE"
    assert platform(public_leaf_data) == "Cumulus Linux"
    assert model(public_leaf_data) == "SN5600"
    assert role(public_leaf_data) == "In-Band-Leaf"
    assert desired_firmware(public_leaf_data) == "5.13.1"
    assert router_id(public_leaf_data) == "10.254.254.11"
    assert uuid(public_leaf_data) == "c9e574df-2295-4258-b5b2-16247b6e3aa7"
    assert asn(public_leaf_data) == "4230000012"

    assert hostname(public_tor_data) == "a04-u44-p01-tor-01"
    assert model(public_tor_data) == "MSN2201"
    assert role(public_tor_data) == "OOB-Leaf"


def test_tags(public_leaf_data: dict) -> None:
    """Device tag helpers handle public fixtures with no device tags."""
    assert device_tags(public_leaf_data) == []
    assert has_tag(public_leaf_data, "dns-exempt") is False

    tagged_data = public_leaf_data.model_copy(
        update={"identity": public_leaf_data.identity.model_copy(update={"tags": ("dns-exempt",)})}
    )
    assert device_tags(tagged_data) == ["dns-exempt"]
    assert has_tag(tagged_data, "dns-exempt")


def test_interface_has_tag(public_leaf_data: dict) -> None:
    """Interface tag matching is case-insensitive."""
    tagged_interface = public_leaf_data.interfaces[0].model_copy(
        update={"tags": ("Cable-Validation",)}
    )
    data = public_leaf_data.model_copy(
        update={"interfaces": (tagged_interface, *public_leaf_data.interfaces[1:])}
    )
    intf = interface_by_name(data, tagged_interface.name)

    assert interface_has_tag(intf, "cable-validation")
    assert interface_has_tag(intf, "CABLE-VALIDATION")
    assert not interface_has_tag(intf, "missing")


def test_desired_firmware_missing(public_leaf_data: dict) -> None:
    """Missing intended firmware reports a clear filter error."""
    no_desired_data = public_leaf_data.model_copy(
        update={"firmware": public_leaf_data.firmware.model_copy(update={"desired_version": None})}
    )

    with pytest.raises(FilterException, match="device.firmware.desired_version"):
        desired_firmware(no_desired_data)


def test_interface_by_name(public_leaf_data: dict) -> None:
    """Interfaces are converted into Interface dataclasses."""
    intf = interface_by_name(public_leaf_data, "swp1s0")

    assert isinstance(intf, Interface)
    assert intf.name == "swp1s0"
    assert intf.primary_ipv4 is None
    assert intf.vrf == "default"
    assert intf.enabled
    assert intf.role == "Downlink"
    assert intf.connected_interface.device.name == "a07-p01-dgx-01-c01"

    eth0 = interface_by_name(public_leaf_data, "eth0")
    assert eth0.primary_ipv4 == "192.0.2.18/23"
    assert eth0.connected_interface.device.name == "a08-u44-p01-mleaf-01"


def test_connected_interface_asn_uses_neighbor_routing_instance(
    public_leaf_data: dict,
) -> None:
    """Connected peer ASN is sourced from the peer BGP routing instance."""
    interface_entry = next(
        interface for interface in public_leaf_data.interfaces if interface.name == "swp53s0"
    )
    peer_device = interface_entry.connected_interface.device

    assert peer_device.routing_asn == "4230000001"

    intf = interface_by_name(public_leaf_data, "swp53s0")

    assert intf.connected_interface.device.name == "a09-u36-p01-spine-01"
    assert intf.connected_interface.device.asn == "4230000001"


def test_connected_interface_asn_uses_matching_neighbor_routing_instance(
    public_leaf_data: dict,
) -> None:
    """Connected peer ASN matches the peer routing instance for the connected VRF."""
    interface_entry = next(
        interface for interface in public_leaf_data.interfaces if interface.name == "swp53s0"
    )
    peer = interface_entry.connected_interface
    updated_interface = interface_entry.model_copy(
        update={
            "connected_interface": peer.model_copy(
                update={
                    "vrf": "TEST-SITE_BLUE",
                    "device": peer.device.model_copy(update={"routing_asn": "65001"}),
                }
            )
        }
    )
    data = public_leaf_data.model_copy(
        update={
            "interfaces": tuple(
                updated_interface if interface.name == updated_interface.name else interface
                for interface in public_leaf_data.interfaces
            )
        }
    )

    intf = interface_by_name(data, "swp53s0")

    assert intf.connected_interface.device.asn == "65001"


def test_interfaces(public_leaf_data: dict, public_spine_data: dict) -> None:
    """Interface filtering returns stable public fixture subsets."""
    swp_interfaces = interfaces(public_leaf_data, prefix="swp")
    assert len(swp_interfaces) == 96
    assert all(interface.name.startswith("swp") for interface in swp_interfaces)

    contains_interfaces = interfaces(public_spine_data, contains="1")
    assert "swp1s0" in [interface.name for interface in contains_interfaces]
    assert len(interfaces(public_spine_data, prefix="swp")) == 86


def test_management_interface(public_leaf_data: dict) -> None:
    """Management interface lookup returns platform-specific management ports."""
    assert management_interface(public_leaf_data).name == "eth0"

    bad_platform_data = public_leaf_data.model_copy(
        update={"identity": public_leaf_data.identity.model_copy(update={"platform": "Blah"})}
    )

    with pytest.raises(
        FilterException, match="No Management Interface lookup implemnented for Blah"
    ):
        management_interface(bad_platform_data)


def test_vrf_helpers(public_tor_data: dict, public_border_leaf_data: dict) -> None:
    """VRF helpers expose attached VRFs and non-default interface state."""
    assert attached_vrfs(public_tor_data) == [
        VRF(name="OOB", vni=2000, export_targets=(), import_targets=())
    ]
    assert has_vrf(public_tor_data, "OOB") is True
    assert has_vrf(public_tor_data, "INBAND") is False
    assert has_vrf_interfaces(public_tor_data)

    border_vrfs = attached_vrfs(public_border_leaf_data)
    assert {vrf.name for vrf in border_vrfs} == {"EXIT", "INBAND", "STORAGE"}


def test_connected_devices(public_leaf_data: dict) -> None:
    """Connected device helpers expose dataclass peers and role filtering."""
    connected = connected_devices(public_leaf_data)
    assert "a09-u36-p01-spine-01" in {device.name for device in connected}

    spines = connected_devices(public_leaf_data, peer_role="Converged-Spine")
    assert spines
    assert {device.role for device in spines} == {"Converged-Spine"}


def test_bgp_routing_instance(public_leaf_data: dict) -> None:
    """BGP routing instances load local ASN and peer lists."""
    bgp_instance = bgp_routing_instance(public_leaf_data, "default")

    assert bgp_instance.asn == 4230000012
    assert bgp_instance.vrf == "default"
    assert len(bgp_instance.peers) == 26
    assert sorted(peer.name for peer in bgp_instance.peers)[0] == "a09-u36-p01-spine-01"

    with pytest.raises(
        FilterException,
        match="Device a08-u32-p01-cleaf-01 has no BGP routing instance for VRF 'dummy'",
    ):
        bgp_routing_instance(public_leaf_data, "dummy")


def test_bgp_routing_instance_allows_a_vrf_without_peers() -> None:
    """A valid non-default instance remains useful before peers are modeled."""
    data = _render_device(
        routing=RenderRoutingData(
            bgp_instances=(
                RenderBGPInstance(
                    status="Active",
                    asn="65001",
                    router_id_interface="lo",
                    vrfs=("BLUE",),
                ),
            )
        )
    )

    instance = bgp_routing_instance(data, "BLUE")

    assert instance.asn == 65001
    assert instance.vrf == "BLUE"
    assert instance.peers == []


def test_bgp_routing_instance_prefers_exact_default_vrf_match() -> None:
    """An earlier non-default instance does not mask a later default instance."""
    data = _render_device(
        routing=RenderRoutingData(
            bgp_instances=(
                RenderBGPInstance(
                    status="Active",
                    asn="65001",
                    router_id_interface="lo-blue",
                    vrfs=("BLUE",),
                ),
                RenderBGPInstance(
                    status="Active",
                    asn="65002",
                    router_id_interface="lo-default",
                    vrfs=("default",),
                ),
            )
        )
    )

    instance = bgp_routing_instance(data)

    assert instance.asn == 65002
    assert instance.interface == "lo-default"
    assert instance.vrf == "default"


def test_common_context_servers(public_leaf_data: dict) -> None:
    """Server context helpers load optional values from config context."""
    assert dns_servers(public_leaf_data) == ["192.0.2.8", "192.0.2.9"]
    assert ntp_servers(public_leaf_data) == ["192.0.2.8", "192.0.2.9"]
    assert syslog_servers(public_leaf_data) == []
    assert ztp_servers(public_leaf_data) == ["192.0.2.10"]
    assert firmware_cache(public_leaf_data) == ["192.0.2.10"]
    assert default_gateways(public_leaf_data) == ["192.0.2.1"]
    assert loopback_prefix(public_leaf_data) == "10.254.254.0/26"


def test_gni_context_helpers(public_leaf_data: dict) -> None:
    """Typed service and routing data is available through the stable filters."""
    data = public_leaf_data.model_copy(
        update={
            "services": public_leaf_data.services.model_copy(
                update={
                    "dhcp": (
                        RenderNamedEndpointSet(
                            name="nv-config-manager",
                            endpoints=RenderEndpointSet(ipv4=("192.0.2.20",)),
                        ),
                    ),
                    "management_prefixes": RenderPrefixSet(ipv4=("192.0.2.0/24",)),
                    "provisioning": RenderEndpointSet(ipv4=("192.0.2.10",)),
                }
            ),
            "routing": public_leaf_data.routing.model_copy(
                update={"isis_interfaces": (RenderIsisInterface(interface_name="swp1", metric=30),)}
            ),
        }
    )

    assert dhcp_servers(data, "nv-config-manager") == ["192.0.2.20"]
    assert dhcp_servers(data, "missing") == []
    assert management_prefixes(data) == ["192.0.2.0/24"]
    assert provisioning_servers(data) == ["192.0.2.10"]
    assert isis_metric(data, "swp1") == 30
    assert isis_metric(data, "swp2") is None


def test_spx_subnets() -> None:
    """Spectrum-X subnet helper returns /31 subnet and rail prefix pairs."""
    data = _render_device(
        interfaces=(
            RenderInterface.model_validate(
                {
                    "name": "swp1",
                    "type": "A_100GBASE_CR4",
                    "enabled": True,
                    "role": "Downlink",
                    "addresses": [
                        {
                            "address": "10.0.0.0/31",
                            "host": "10.0.0.0",
                            "version": 4,
                            "parent_prefixes": [
                                "10.0.0.0/31",
                                "10.0.0.0/26",
                                "10.0.0.0/16",
                            ],
                        }
                    ],
                }
            ),
        )
    )

    assert spx_subnets(data) == [{"subnet": "10.0.0.0/31", "rail_prefix": "10.0.0.0/16"}]

    invalid_address = (
        data.interfaces[0]
        .addresses[0]
        .model_copy(update={"parent_prefixes": ("10.0.0.0/31", "10.0.0.0/26", "10.0.0.0/18")})
    )
    invalid_interface = data.interfaces[0].model_copy(update={"addresses": (invalid_address,)})
    data = data.model_copy(update={"interfaces": (invalid_interface,)})
    with pytest.raises(FilterException, match="Invalid rail prefix length /18"):
        spx_subnets(data)


def test_l2vni_vrfs() -> None:
    """L2VNI helper consumes provider-neutral overlay assignments."""
    data = _render_device(
        overlays=RenderOverlayData.model_validate(
            {
                "l2_vni_vrfs": [
                    {
                        "name": "Vlan101",
                        "vni": 1001,
                        "export_targets": [{"name": "target:1"}],
                        "import_targets": [{"name": "target:2"}],
                    },
                    {
                        "name": "Vlan102",
                        "vni": 1002,
                        "export_targets": [{"name": "target:3"}],
                        "import_targets": [{"name": "target:4"}],
                    },
                ]
            }
        )
    )

    assert l2vni_vrfs(data) == [
        {
            "name": "Vlan101",
            "vni": "1001",
            "export_targets": ["target:1"],
            "import_targets": ["target:2"],
        },
        {
            "name": "Vlan102",
            "vni": "1002",
            "export_targets": ["target:3"],
            "import_targets": ["target:4"],
        },
    ]


def test_firmware_cache_fallback_explicit() -> None:
    """firmware_cache falls back to ZTP servers unless explicitly configured."""
    mock_data = _render_device(
        services=RenderServicesData(ztp=RenderEndpointSet(ipv4=("192.168.1.100", "192.168.1.101")))
    )

    assert firmware_cache(mock_data) == ["192.168.1.100", "192.168.1.101"]

    mock_data_with_cache = mock_data.model_copy(
        update={
            "services": mock_data.services.model_copy(
                update={
                    "firmware_cache": RenderEndpointSet(ipv4=("192.168.2.100", "192.168.2.101"))
                }
            )
        }
    )

    assert firmware_cache(mock_data_with_cache) == ["192.168.2.100", "192.168.2.101"]


def test_breakout_count(public_leaf_data: dict, public_border_leaf_data: dict) -> None:
    """Breakout count is derived from child interface naming."""
    assert breakout_count(public_leaf_data, "swp1") == 2
    assert breakout_count(public_border_leaf_data, "swp1") == 4


def test_firmware_bundle_filters() -> None:
    """Firmware bundle helper filters resolve bundle defaults, overrides, and components."""
    mock_data_with_bundle = _render_device(
        firmware=RenderFirmwareData.model_validate(
            {
                "selected_bundle_version": "1.2.2",
                "bundles": [
                    {
                        "version": "1.2.0",
                        "operating_system": {
                            "version": "25.02.2342",
                            "image_file": "nvos-amd64-25.02.2342.bin",
                        },
                        "components": [
                            {
                                "name": "bmc",
                                "artifact": {
                                    "image_file": "old_bmc.fwpkg",
                                    "source_path": "nv-os/25.02.2342/old_bmc.fwpkg",
                                },
                            }
                        ],
                    },
                    {
                        "version": "1.2.2",
                        "operating_system": {
                            "version": "25.02.2344",
                            "image_file": "nvos-amd64-25.02.2344.bin",
                        },
                        "components": [
                            {
                                "name": "bmc",
                                "artifact": {
                                    "image_file": "new_bmc.fwpkg",
                                    "source_path": "ytl-bundles/1.2.2/new_bmc.fwpkg",
                                },
                            },
                            {
                                "name": "cpld",
                                "artifact": {
                                    "image_file": "new_cpld.bin",
                                    "source_path": "ytl-bundles/1.2.2/new_cpld.bin",
                                },
                            },
                        ],
                    },
                ],
                "overrides": {
                    "skip_components": ["cpld"],
                    "custom_components": [
                        {
                            "name": "bios",
                            "artifact": {
                                "image_file": "custom_bios.fwpkg",
                                "source_path": "custom/custom_bios.fwpkg",
                            },
                        }
                    ],
                },
            }
        )
    )
    mock_data_no_bundle = _render_device()

    assert firmware_bundle_version(mock_data_with_bundle) == "1.2.2"
    assert firmware_bundle_version(mock_data_no_bundle) == "1.2.0"
    assert has_firmware_bundle(mock_data_with_bundle) is True
    assert has_firmware_bundle(mock_data_no_bundle) is False
    assert "1.2.2" in firmware_bundles(mock_data_with_bundle)

    with pytest.raises(FilterException, match="device.firmware.bundles"):
        firmware_bundles(mock_data_no_bundle)

    assert firmware_bundle(mock_data_with_bundle)["nv_os"]["version"] == "25.02.2344"
    assert firmware_bundle(mock_data_with_bundle, "1.2.0")["nv_os"]["version"] == "25.02.2342"

    with pytest.raises(FilterException, match="not found in firmware_bundles"):
        firmware_bundle(mock_data_with_bundle, "99.99.99")

    overrides = firmware_overrides(mock_data_with_bundle)
    assert overrides["skip_components"] == ["cpld"]
    assert "bios" in overrides["custom_components"]
    assert firmware_overrides(mock_data_no_bundle) == {
        "skip_components": [],
        "custom_components": {},
    }

    assert firmware_component(mock_data_with_bundle, "bmc")["file"] == "new_bmc.fwpkg"
    assert firmware_component(mock_data_with_bundle, "cpld") is None
    assert firmware_component(mock_data_with_bundle, "bios")["file"] == "custom_bios.fwpkg"
    assert firmware_component(mock_data_with_bundle, "nonexistent") is None
    assert nv_os_version(mock_data_with_bundle) == "25.02.2344"
    assert nv_os_image_file(mock_data_with_bundle, "1.2.0") == "nvos-amd64-25.02.2342.bin"


def test_helper_addresses_by_vlan(public_border_leaf_data: dict) -> None:
    """Helper addresses are grouped by VLANs present on the device."""
    location_data_with_vlans = LocationRenderData(
        location=RenderLocation(name="TEST-SITE", kind="Site"),
        address_space=RenderLocationAddressSpace(
            vlans=(
                RenderLocationVlan(
                    vlan=RenderVlan(vid=101), helper_addresses=("192.0.2.8", "192.0.2.9")
                ),
                RenderLocationVlan(vlan=RenderVlan(vid=150), helper_addresses=("192.0.2.10",)),
                RenderLocationVlan(vlan=RenderVlan(vid=999), helper_addresses=("192.0.2.11",)),
            )
        ),
    )

    assert helper_addresses_by_vlan(public_border_leaf_data, location_data_with_vlans) == {
        101: ["192.0.2.8", "192.0.2.9"],
        150: ["192.0.2.10"],
    }
    assert (
        helper_addresses_by_vlan(
            public_border_leaf_data,
            LocationRenderData(location=RenderLocation(name="TEST-SITE", kind="Site")),
        )
        == {}
    )


def test_helper_addresses_by_vrf(public_border_leaf_data: dict) -> None:
    """Helper addresses are grouped by attached VRF."""
    location_data_with_vlans = LocationRenderData(
        location=RenderLocation(name="TEST-SITE", kind="Site"),
        address_space=RenderLocationAddressSpace(
            vlans=(
                RenderLocationVlan(
                    vlan=RenderVlan(vid=101), helper_addresses=("192.0.2.8", "192.0.2.9")
                ),
                RenderLocationVlan(vlan=RenderVlan(vid=150), helper_addresses=("192.0.2.10",)),
            )
        ),
    )

    assert helper_addresses_by_vrf(public_border_leaf_data, location_data_with_vlans) == {
        "INBAND": {"vlans": [101, 150], "helpers": ["192.0.2.8", "192.0.2.9", "192.0.2.10"]}
    }
    assert (
        helper_addresses_by_vrf(
            public_border_leaf_data,
            LocationRenderData(location=RenderLocation(name="TEST-SITE", kind="Site")),
        )
        == {}
    )


def test_users() -> None:
    """Users are converted from password mappings into sorted password keys."""
    mock_data = _render_device(
        access=RenderAccessData(
            credentials=(
                RenderCredentialReference(
                    username="admin",
                    secret_name="admin_password",
                    rotation="r2",
                    role="system-admin",
                ),
                RenderCredentialReference(
                    username="cumulus",
                    secret_name="root_password",
                    rotation="r1",
                    role="system-admin",
                ),
            )
        )
    )

    assert users(mock_data) == [
        {"username": "admin", "role": "system-admin", "password_key": "admin_password_r2"},
        {"username": "cumulus", "role": "system-admin", "password_key": "root_password_r1"},
    ]


def test_users_missing_required_key() -> None:
    """Missing credential data raises an actionable template error."""
    with pytest.raises(FilterException, match="device.access.credentials"):
        users(_render_device(name="my-device"))


def test_l3vni_mappings(public_tor_data: dict) -> None:
    """L3VNI mappings return L3 VLAN strings."""
    assert l3vni_mappings(public_tor_data, "OOB") == "4002"
    assert l3vni_mappings(public_tor_data, "INBAND") == "4001"
    assert l3vni_mappings(public_tor_data, "STORAGE") == "4003"

    with pytest.raises(FilterException, match="VRF 'oob' not found"):
        l3vni_mappings(public_tor_data, "oob")

    assert (
        l3vni_mappings(
            _render_device(
                network=RenderNetworkData(vrfs=(RenderVrf(name="OOB"),)),
                overlays=RenderOverlayData(l3_vnis=(RenderL3Vni(vrf=RenderVrf(name="OOB")),)),
            ),
            "OOB",
        )
        == ""
    )


def test_vni_mappings(public_tor_data: dict) -> None:
    """VNI mappings return VNI strings for string and integer VLAN IDs."""
    assert vni_mappings(public_tor_data, "101") == "1001"
    assert vni_mappings(public_tor_data, "202") == "2002"
    assert vni_mappings(public_tor_data, 150) == "1050"

    with pytest.raises(FilterException, match="VLAN 999 not found"):
        vni_mappings(public_tor_data, "999")
    assert vni_mappings(public_tor_data, "999", fail_if_missing=False) == ""


def test_mapping_filters_keep_device_name_safe_when_data_is_missing() -> None:
    """Missing normalized overlay inventory retains the device name in errors."""
    data = _render_device(name="leaf01")

    with pytest.raises(FilterException, match="leaf01"):
        l3vni_mappings(data, "default")
    with pytest.raises(FilterException, match="leaf01"):
        vni_mappings(data, 10)


def test_global_fabric_mac(public_tor_data: dict) -> None:
    """fabric-mac returns strings, empty optional values, or clear failures."""
    assert global_fabric_mac(public_tor_data) == "00:00:5E:00:01:69"
    assert (
        global_fabric_mac(
            _render_device(),
            fail_if_missing=False,
        )
        == ""
    )

    with pytest.raises(FilterException, match="device.routing.evpn.fabric_mac"):
        global_fabric_mac(_render_device())


def test_evpn_esi_mac(public_tor_data: dict) -> None:
    """ESI MAC math carries across octets and refuses overflow."""
    assert evpn_esi_mac(public_tor_data, 1) == "44:38:39:ff:69:01"

    data = _render_device(
        name="leaf01",
        routing=RenderRoutingData(evpn={"esi_base_mac": "00:00:00:00:00:ff"}),
    )
    assert evpn_esi_mac(data, 1) == "00:00:00:00:01:00"

    overflow_data = _render_device(
        name="leaf01",
        routing=RenderRoutingData(evpn={"esi_base_mac": "ff:ff:ff:ff:ff:ff"}),
    )
    with pytest.raises(FilterException, match="overflow"):
        evpn_esi_mac(overflow_data, 1)


def test_evpn_helpers(public_tor_data: dict) -> None:
    """Small EVPN helper filters expose defaults from public fixtures."""
    assert evpn_df_preference(public_tor_data) == 50000
