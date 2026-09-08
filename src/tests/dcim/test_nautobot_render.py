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
"""Tests for the built-in provider's Render contract implementation."""

from unittest.mock import AsyncMock, call

import pytest
from nv_config_manager_dcim import DCIMInvalidDataError, RenderDataRequirement
from nv_config_manager_dcim_nautobot_2x.client import NautobotException
from nv_config_manager_dcim_nautobot_2x.provider import NautobotDCIMClient
from nv_config_manager_dcim_nautobot_2x.render import (
    _bgp_instances,
    _bgp_peer,
    _overlay_data,
    _routing_asn_from_instances,
)

from nv_config_manager.dcim import IntendedConfigurationUpdate, RenderDataRequest


def _client() -> NautobotDCIMClient:
    """Build a provider client without opening an HTTP session."""
    return NautobotDCIMClient("https://nautobot.example", "token")


def test_peer_asn_allows_a_single_instance_without_router_id():
    """A peer router ID is optional when its one BGP instance is unambiguous."""
    assert (
        _routing_asn_from_instances(
            [{"autonomous_system": {"asn": 65001}, "router_id": None}],
            "EXIT",
            "device 'example-wan-1'",
        )
        == "65001"
    )


def test_bgp_instance_allows_an_unset_router_id():
    """Native routing instances can exist before a router ID is assigned."""
    instances = _bgp_instances(
        [
            {
                "status": {"name": "Active"},
                "autonomous_system": {"asn": 65001},
                "router_id": None,
                "endpoints": [],
            }
        ],
        "example-switch",
    )

    assert instances[0].router_id_interface is None
    assert instances[0].peers == ()


def test_bgp_peer_requires_source_interface_for_nautobot_vrf_association():
    """The BGP plugin's source IP cannot supply the peer interface VRF."""
    with pytest.raises(
        DCIMInvalidDataError,
        match="BGP peer is missing required source_interface.*VRF association on source_ip",
    ):
        _bgp_peer(
            {
                "peer": {
                    "source_ip": {"address": "192.0.2.1/31"},
                    "source_interface": None,
                }
            },
            "example-switch",
        )


@pytest.mark.parametrize(
    ("description", "expected"),
    [("WAN neighbor", "WAN neighbor"), ("", "spine-1"), (None, "spine-1")],
)
def test_bgp_peer_uses_endpoint_description_with_device_name_fallback(description, expected):
    """Configured peer descriptions are optional and preserve the historical fallback."""
    peer = _bgp_peer(
        {
            "peer": {
                "description": description,
                "source_interface": {"name": "swp1", "ip_addresses": []},
                "routing_instance": {
                    "device": {"name": "spine-1", "role": {"name": "Spine"}},
                    "status": {"name": "Active"},
                    "autonomous_system": {"asn": 65001},
                },
            }
        },
        "leaf-1",
    )

    assert peer is not None
    assert peer.name == "spine-1"
    assert peer.description == expected


def test_overlay_inventory_is_scoped_to_device_vlans_and_vrfs():
    """Global Nautobot VXLAN results retain only records used by one device."""
    payload = {
        "overlay_assignments": [],
        "vxlans": [
            {
                "id": "vxlan-201",
                "vnid": 2001,
                "vni_type": "L2",
                "overlay": {"id": "overlay-201", "name": "Vlan201"},
                "vlan": {"id": "vlan-201", "vid": 201, "name": "Vlan201"},
            },
            {
                "id": "vxlan-301",
                "vnid": 3001,
                "vni_type": "L2",
                "overlay": {"id": "overlay-301", "name": "Vlan301"},
                "vlan": {"id": "vlan-301", "vid": 301, "name": "Vlan301"},
            },
            {
                "id": "vxlan-oob",
                "vnid": 2000,
                "vni_type": "L3",
                "overlay": {"id": "overlay-oob", "name": "OOB-L3"},
                "vrf": {"id": "vrf-oob", "name": "OOB"},
            },
            {
                "id": "vxlan-storage",
                "vnid": 3000,
                "vni_type": "L3",
                "overlay": {"id": "overlay-storage", "name": "STORAGE-L3"},
                "vrf": {"id": "vrf-storage", "name": "STORAGE"},
            },
        ],
    }
    device = {
        "vrfs": [{"id": "vrf-oob", "name": "OOB"}],
        "interfaces": [
            {
                "untagged_vlan": {"id": "vlan-201", "vid": 201, "name": "Vlan201"},
                "tagged_vlans": [],
                "vrf": {"id": "vrf-oob", "name": "OOB"},
            }
        ],
    }

    overlays = _overlay_data(payload, device, "example-switch")

    assert [entry.vlan.vid for entry in overlays.l2_vnis] == [201]
    assert [entry.vrf.name for entry in overlays.l3_vnis] == ["OOB"]


@pytest.mark.asyncio
async def test_get_render_data_loads_provider_owned_queries():
    """The provider owns both template data queries and location resolution."""
    client = _client()
    client.graphql_query = AsyncMock(
        side_effect=[
            {
                "data": {
                    "device": {
                        "id": "device-id",
                        "name": "leaf-1",
                        "platform": {"name": "Cumulus Linux"},
                        "role": {"name": "Leaf"},
                        "device_type": {"model": "SN5600"},
                        "tags": [],
                        "interfaces": [],
                        "bgp_routing_instances": [
                            {
                                "status": {"name": "Active"},
                                "autonomous_system": {"asn": 65000},
                                "router_id": {"interfaces": [{"name": "lo", "vrf": None}]},
                                "endpoints": [
                                    {
                                        "source_interface": {"vrf": {"name": "EXIT"}},
                                        "peer": {
                                            "source_interface": {
                                                "name": "swp1",
                                                "ip_addresses": [],
                                            },
                                            "routing_instance": {
                                                "device": {
                                                    "name": "spine-1",
                                                    "role": {"name": "Spine"},
                                                },
                                                "status": {"name": "Active"},
                                                "autonomous_system": {"asn": 65001},
                                            },
                                        },
                                    }
                                ],
                            }
                        ],
                        "config_context": {"backbone": {"schema_version": 1}},
                        "location": {
                            "name": "Rack 1",
                            "location_type": {"name": "Rack"},
                            "parent": {
                                "name": "Site A",
                                "location_type": {"name": "Site"},
                                "parent": None,
                            },
                        },
                    }
                }
            },
            {
                "data": {
                    "locations": [
                        {
                            "name": "Site A",
                            "location_type": {"name": "Site"},
                        }
                    ]
                }
            },
            {"data": {"custom_devices": [{"name": "leaf-1"}]}},
        ]
    )

    render_data = await client.get_render_data(
        RenderDataRequest(
            device_id="device-id",
            plugin_data_requirements={
                "custom_graphql": RenderDataRequirement(
                    parameters={"graphql_query": "query Custom { custom_devices { name } }"}
                ),
                "custom_context": RenderDataRequirement(
                    parameters={"config_context_keys": ["backbone"]}
                ),
            },
        )
    )

    assert render_data.device.identity.location.name == "Rack 1"
    assert render_data.location.location.name == "Site A"
    assert render_data.device.routing.bgp_instances[0].vrfs == ("EXIT", "default")
    assert render_data.plugin_data["custom_graphql"].data == {
        "data": {"custom_devices": [{"name": "leaf-1"}]}
    }
    assert render_data.plugin_data["custom_context"].data == {
        "config_context": {"backbone": {"schema_version": 1}}
    }
    first_call, second_call, third_call = client.graphql_query.await_args_list
    assert first_call.args[1] == {"id": "device-id", "id_str": "device-id"}
    assert second_call.args[1] == {"location": "Site A"}
    assert third_call.args[1] == {"id": "device-id", "hostname": "leaf-1"}


@pytest.mark.asyncio
async def test_get_render_data_rejects_missing_required_interface_type():
    """Provider errors name the device, object, and missing typed field."""
    client = _client()
    client.graphql_query = AsyncMock(
        side_effect=[
            {
                "data": {
                    "device": {
                        "id": "device-id",
                        "name": "leaf-1",
                        "platform": {"name": "Cumulus Linux"},
                        "role": {"name": "Leaf"},
                        "device_type": {"model": "SN5600"},
                        "tags": [],
                        "interfaces": [{"name": "swp1", "type": None, "enabled": True}],
                        "config_context": {},
                        "location": {
                            "name": "Site A",
                            "location_type": {"name": "Site"},
                            "parent": None,
                        },
                    }
                }
            },
            {
                "data": {
                    "locations": [
                        {
                            "name": "Site A",
                            "location_type": {"name": "Site"},
                        }
                    ]
                }
            },
        ]
    )

    with pytest.raises(
        DCIMInvalidDataError,
        match="Nautobot device 'leaf-1' interface 'swp1' is missing required field 'type'",
    ):
        await client.get_render_data(RenderDataRequest(device_id="device-id"))


@pytest.mark.asyncio
async def test_render_state_operations_map_to_normalized_models():
    """Provider-normalized state hides Nautobot query and REST details."""
    client = _client()
    client.graphql_query = AsyncMock(
        side_effect=[
            {
                "data": {
                    "config_manager_device": {
                        "render_enabled": True,
                        "is_aggregate_managed": False,
                    }
                }
            },
            {
                "data": {
                    "config_manager_devices": [
                        {"id": "device-1"},
                        {"id": "device-2"},
                    ]
                }
            },
            {"data": {"devices": []}},
        ]
    )

    status = await client.get_render_device_status("device-1")
    enabled_ids = await client.get_render_enabled_device_ids(False)
    assert status and status.render_enabled is True
    assert enabled_ids == ["device-1", "device-2"]
    assert client.graphql_query.await_args_list[1].args[1] == {"is_aggregate_managed": False}


@pytest.mark.asyncio
async def test_intended_configuration_writes_are_provider_owned():
    """Render code supplies intent, while the provider owns plugin REST paths."""
    client = _client()
    client.post = AsyncMock()
    client.patch = AsyncMock()
    update = IntendedConfigurationUpdate(
        device_id="device-id",
        config_store_instance="https://config-store/",
        path="startup.yaml",
        commit_id="commit-id",
        updated="2026-07-20T00:00:00+00:00",
        updated_by="user",
        commit_message="render",
        template_version="version",
    )

    await client.upsert_intended_configuration(update)
    await client.update_render_template_version("device-id", "next-version")

    client.post.assert_awaited_once_with(
        "plugins/nv-config-manager/intendedconfig/",
        {
            "device_id": "device-id",
            "config_store_instance": "https://config-store/",
            "path": "startup.yaml",
            "commit_id": "commit-id",
            "updated": "2026-07-20T00:00:00+00:00",
            "updated_by": "user",
            "commit_message": "render",
            "template_version": "version",
        },
    )
    client.patch.assert_awaited_once_with(
        "plugins/nv-config-manager/intendedconfig/device-id/", {"template_version": "next-version"}
    )


@pytest.mark.asyncio
async def test_intended_configuration_updates_duplicate_record():
    """The provider handles Nautobot versions whose create endpoint is not an upsert."""
    client = _client()
    client.post = AsyncMock(
        side_effect=NautobotException(
            "Nautobot API error: POST plugins/nv-config-manager/intendedconfig/ returned 400: "
            "{'device_id': ['Intended Config Settings with this Device id already exists.']}"
        )
    )
    client.patch = AsyncMock()
    update = IntendedConfigurationUpdate(
        device_id="device-id",
        config_store_instance="https://config-store/",
        path="startup.yaml",
        commit_id="commit-id",
        updated="2026-07-20T00:00:00+00:00",
        updated_by="user",
        commit_message="render",
        template_version="version",
    )

    await client.upsert_intended_configuration(update)

    client.patch.assert_has_awaits(
        [
            call(
                "plugins/nv-config-manager/intendedconfig/device-id/",
                {
                    "config_store_instance": "https://config-store/",
                    "path": "startup.yaml",
                    "commit_id": "commit-id",
                    "updated": "2026-07-20T00:00:00+00:00",
                    "updated_by": "user",
                    "commit_message": "render",
                    "template_version": "version",
                },
            )
        ]
    )


@pytest.mark.asyncio
async def test_intended_interface_neighbors_are_provider_owned():
    """Temporal activities receive normalized neighbor records, not GraphQL access."""
    client = _client()
    client.graphql_query = AsyncMock(
        return_value={
            "data": {
                "interfaces": [
                    {
                        "name": "swp1",
                        "tags": [],
                        "connected_interface": {
                            "name": "swp2",
                            "mac_address": "00:11:22:33:44:55",
                            "device": {
                                "name": "leaf-2",
                                "serial": "serial-2",
                                "position": 10,
                                "role": {"name": "Leaf"},
                                "rack": {"name": "rack-1"},
                            },
                        },
                    }
                ]
            }
        }
    )

    interfaces = await client.get_intended_interface_neighbors("device-id")

    assert len(interfaces) == 1
    assert interfaces[0].name == "swp1"
    assert interfaces[0].connected_interface_name == "swp2"
    assert interfaces[0].connected_device and interfaces[0].connected_device.name == "leaf-2"
    assert client.graphql_query.await_args.args[1] == {"device_id": "device-id"}
