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
"""CI-only end-to-end contract tests for Nautobot provider event handlers.

The suite mutates real mock-topology records through Nautobot REST, verifies the
changelog event reaches the selected provider, and asserts the resulting render
updates the affected device's config-store metadata. Each mutation is restored.
"""

import time
from typing import Any
from uuid import uuid4

import pytest
import requests
from nv_config_manager_dcim_nautobot_2x.provider import NautobotProvider

pytestmark = [
    pytest.mark.integration,
    pytest.mark.ci_only,
    pytest.mark.dcim_provider("nautobot-2x"),
    # These tests perform multiple mutate/render/restore cycles. Their polling
    # helpers retain a 180-second deadline so a stuck queue still fails with a
    # useful diagnostic instead of the repository-wide 30-second timeout.
    pytest.mark.timeout(660),
]

_REPRESENTED_EVENT_TYPES = frozenset(
    {
        "dcim.cable",
        "dcim.device",
        "dcim.interface",
        "extras.configcontext",
        "ipam.ipaddress",
        "ipam.prefix",
        "ipam.vrf",
        "nautobot_bgp_models.autonomoussystem",
        "nautobot_bgp_models.bgproutinginstance",
        "nautobot_bgp_models.peering",
        "nautobot_bgp_models.peerendpoint",
        "nv_config_manager.configmanagerdevicestatus",
    }
)
_UNREPRESENTED_EVENT_TYPES = frozenset(
    {
        "dcim.cablepath",
        "dcim.deviceredundancygroup",
        "dcim.frontport",
        "dcim.rearport",
        "nautobot_bgp_models.peergroup",
    }
)

_EVENT_TARGETS_QUERY = """
query {
  config_manager_devices(render_enabled: true) {
    device {
      id
      name
      comments
      location {
        id
      }
      interfaces {
        id
        name
        description
        vrf {
          id
          name
        }
        ip_addresses {
          id
          address
          description
        }
      }
    }
  }
}
"""

_RENDER_STATUS_QUERY = """
query {
  config_manager_devices(render_enabled: true) {
    device {
      id
    }
    intended_config {
      commit_message
    }
  }
}
"""

_MANAGED_DEVICES_AT_LOCATIONS_QUERY = """
query ManagedDevicesAtLocations($locations: [String]) {
  devices(location: $locations, nv_config_manager_device_status: true) {
    id
    name
  }
}
"""


def _graphql(
    nautobot_url: str,
    nautobot_client: requests.Session,
    query: str,
    variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute one provider test query against Nautobot."""
    response = nautobot_client.post(
        f"{nautobot_url}/api/graphql/",
        json={"query": query, "variables": variables or {}},
        timeout=30,
    )
    if not response.ok:
        pytest.fail(f"Nautobot GraphQL request failed ({response.status_code}): {response.text}")
    payload = response.json()
    if payload.get("errors"):
        pytest.fail(f"Nautobot GraphQL query failed: {payload['errors']}")
    return payload


def _render_devices(nautobot_url: str, nautobot_client: requests.Session) -> list[dict[str, Any]]:
    """Return render-enabled devices with event-capable children."""
    return _graphql(nautobot_url, nautobot_client, _EVENT_TARGETS_QUERY)["data"][
        "config_manager_devices"
    ]


def _managed_devices_at_locations(
    nautobot_url: str, nautobot_client: requests.Session, location_ids: list[str]
) -> list[dict[str, Any]]:
    """Return managed devices selected by the provider's location event filter."""
    return _graphql(
        nautobot_url,
        nautobot_client,
        _MANAGED_DEVICES_AT_LOCATIONS_QUERY,
        {"locations": location_ids},
    )["data"]["devices"]


def _scoped_record_and_affected_device(
    nautobot_url: str,
    nautobot_client: requests.Session,
    records: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Find a scoped record selected by the provider's managed-device query."""
    for record in records:
        location_ids = [location["id"] for location in record.get("locations", [])]
        affected_devices = _managed_devices_at_locations(
            nautobot_url, nautobot_client, location_ids
        )
        if affected_devices:
            return record, affected_devices[0]
    return None


def _api_list(
    nautobot_url: str,
    nautobot_client: requests.Session,
    path: str,
    **params: str,
) -> list[dict[str, Any]]:
    """Return all records from a small mock-topology Nautobot collection."""
    response = nautobot_client.get(
        f"{nautobot_url}/api/{path}/", params={"limit": "1000", **params}, timeout=30
    )
    response.raise_for_status()
    return response.json()["results"]


def _event_prefix(object_type: str, operation: str = "update") -> str:
    """Return the stable portion of a Nautobot event render commit message."""
    return f"Triggered from nb {object_type} {operation}"


def _queue_status(render_api_url: str, render_client: requests.Session) -> tuple[int, int]:
    """Return pending and acknowledged-in-flight JetStream render messages."""
    response = render_client.get(f"{render_api_url}/v1/admin/consumers", timeout=10)
    response.raise_for_status()
    device_consumers = [
        consumer
        for consumer in response.json().get("consumers", [])
        if str(consumer.get("name", "")).endswith("-device")
    ]
    if not device_consumers or any(
        consumer.get("num_pending", -1) < 0 or consumer.get("num_ack_pending", -1) < 0
        for consumer in device_consumers
    ):
        return -1, -1
    return (
        sum(consumer["num_pending"] for consumer in device_consumers),
        sum(consumer["num_ack_pending"] for consumer in device_consumers),
    )


def _wait_for_queues_to_drain(render_api_url: str, render_client: requests.Session) -> None:
    """Wait for event-triggered renders to finish before the next mutation."""
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        pending, ack_pending = _queue_status(render_api_url, render_client)
        if pending == 0 and ack_pending == 0:
            return
        time.sleep(2)
    pytest.fail(f"Render queues did not drain: pending={pending}, ack_pending={ack_pending}")


def _event_message_for_device(
    nautobot_url: str, nautobot_client: requests.Session, device_id: str
) -> str:
    """Return the last render message for one managed device."""
    devices = _graphql(nautobot_url, nautobot_client, _RENDER_STATUS_QUERY)["data"][
        "config_manager_devices"
    ]
    for managed_device in devices:
        device = managed_device.get("device") or {}
        if device.get("id") == device_id:
            return (managed_device.get("intended_config") or {}).get("commit_message") or ""
    pytest.fail(f"Nautobot has no render-enabled managed device {device_id}")


def _wait_for_event_render(
    nautobot_url: str,
    nautobot_client: requests.Session,
    device_id: str,
    message_prefix: str,
    previous_message: str,
) -> str:
    """Wait for a provider event to update its device's intended-config metadata."""
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        devices = _graphql(nautobot_url, nautobot_client, _RENDER_STATUS_QUERY)["data"][
            "config_manager_devices"
        ]
        for managed_device in devices:
            device = managed_device.get("device") or {}
            commit_message = (managed_device.get("intended_config") or {}).get(
                "commit_message"
            ) or ""
            if (
                device.get("id") == device_id
                and commit_message.startswith(message_prefix)
                and commit_message != previous_message
            ):
                return commit_message
        time.sleep(2)
    pytest.fail(
        f"Nautobot event did not render device {device_id} with commit message "
        f"starting {message_prefix!r} after {previous_message!r}"
    )


def _patch_and_assert_event(
    nautobot_url: str,
    nautobot_client: requests.Session,
    render_api_url: str,
    render_client: requests.Session,
    *,
    target_device_id: str,
    path: str,
    object_type: str,
    changes: dict[str, Any],
    restore_changes: dict[str, Any],
) -> None:
    """Mutate, observe, and restore one Nautobot event-producing record."""
    previous_message = _event_message_for_device(nautobot_url, nautobot_client, target_device_id)
    rendered_message = previous_message
    response = nautobot_client.patch(f"{nautobot_url}/api/{path}/", json=changes, timeout=30)
    response.raise_for_status()
    try:
        rendered_message = _wait_for_event_render(
            nautobot_url,
            nautobot_client,
            target_device_id,
            _event_prefix(object_type),
            previous_message,
        )
        _wait_for_queues_to_drain(render_api_url, render_client)
    finally:
        restore_response = nautobot_client.patch(
            f"{nautobot_url}/api/{path}/", json=restore_changes, timeout=30
        )
        restore_response.raise_for_status()
        _wait_for_event_render(
            nautobot_url,
            nautobot_client,
            target_device_id,
            _event_prefix(object_type),
            rendered_message,
        )
        _wait_for_queues_to_drain(render_api_url, render_client)


class TestNautobotProviderEvents:
    """Verify each mock-topology Nautobot event crosses the provider boundary."""

    def test_mock_topology_coverage_matches_provider_registration(self) -> None:
        """Every registered type is either exercised live or lacks fixture data."""

        class Registry:
            def __init__(self) -> None:
                self.handlers: dict[str, object] = {}

            def register_render_event_handler(self, object_type: str, handler: object) -> None:
                self.handlers[object_type] = handler

        registry = Registry()
        NautobotProvider().register_render_event_handlers(registry)

        assert set(registry.handlers) == _REPRESENTED_EVENT_TYPES | _UNREPRESENTED_EVENT_TYPES

    def test_device_event_triggers_its_device_render(
        self,
        nautobot_url: str,
        nautobot_client: requests.Session,
        render_api_url: str,
        render_client: requests.Session,
    ) -> None:
        """A ``dcim.device`` update renders the changed managed device."""
        device = _render_devices(nautobot_url, nautobot_client)[0]["device"]
        _patch_and_assert_event(
            nautobot_url,
            nautobot_client,
            render_api_url,
            render_client,
            target_device_id=device["id"],
            path=f"dcim/devices/{device['id']}",
            object_type="dcim.device",
            changes={"comments": f"nvcm-provider-device-{uuid4().hex}"},
            restore_changes={"comments": device.get("comments") or ""},
        )

    def test_interface_and_ip_address_events_resolve_affected_device(
        self,
        nautobot_url: str,
        nautobot_client: requests.Session,
        render_api_url: str,
        render_client: requests.Session,
    ) -> None:
        """Interface and IP events resolve their owning managed device."""
        target = next(
            (
                managed_device["device"]
                for managed_device in _render_devices(nautobot_url, nautobot_client)
                if any(
                    interface.get("ip_addresses")
                    for interface in managed_device["device"]["interfaces"]
                )
            ),
            None,
        )
        if target is None:
            pytest.fail("Mock topology has no render-enabled interface with an IP address")
        interface = next(
            interface for interface in target["interfaces"] if interface.get("ip_addresses")
        )
        ip_address = interface["ip_addresses"][0]

        _patch_and_assert_event(
            nautobot_url,
            nautobot_client,
            render_api_url,
            render_client,
            target_device_id=target["id"],
            path=f"dcim/interfaces/{interface['id']}",
            object_type="dcim.interface",
            changes={"description": f"nvcm-provider-interface-{uuid4().hex}"},
            restore_changes={"description": interface.get("description") or ""},
        )
        _patch_and_assert_event(
            nautobot_url,
            nautobot_client,
            render_api_url,
            render_client,
            target_device_id=target["id"],
            path=f"ipam/ip-addresses/{ip_address['id']}",
            object_type="ipam.ipaddress",
            changes={"description": f"nvcm-provider-address-{uuid4().hex}"},
            restore_changes={"description": ip_address.get("description") or ""},
        )

    def test_vrf_event_resolves_all_affected_devices(
        self,
        nautobot_url: str,
        nautobot_client: requests.Session,
        render_api_url: str,
        render_client: requests.Session,
    ) -> None:
        """A shared ``ipam.vrf`` update resolves devices through provider GraphQL."""
        target: dict[str, Any] | None = None
        vrf: dict[str, Any] | None = None
        for managed_device in _render_devices(nautobot_url, nautobot_client):
            for interface in managed_device["device"]["interfaces"]:
                if interface.get("vrf"):
                    target = managed_device["device"]
                    vrf = interface["vrf"]
                    break
            if target is not None:
                break
        if target is None or vrf is None:
            pytest.fail("Mock topology has no render-enabled interface assigned to a VRF")

        response = nautobot_client.get(f"{nautobot_url}/api/ipam/vrfs/{vrf['id']}/", timeout=30)
        response.raise_for_status()
        _patch_and_assert_event(
            nautobot_url,
            nautobot_client,
            render_api_url,
            render_client,
            target_device_id=target["id"],
            path=f"ipam/vrfs/{vrf['id']}",
            object_type="ipam.vrf",
            changes={"description": f"nvcm-provider-vrf-{uuid4().hex}"},
            restore_changes={"description": response.json().get("description") or ""},
        )

    def test_context_prefix_and_managed_device_events(
        self,
        nautobot_url: str,
        nautobot_client: requests.Session,
        render_api_url: str,
        render_client: requests.Session,
    ) -> None:
        """Scope-aware and provider-plugin model events render their affected device."""
        contexts = _api_list(nautobot_url, nautobot_client, "extras/config-contexts")
        prefixes = _api_list(nautobot_url, nautobot_client, "ipam/prefixes")
        context_selection = _scoped_record_and_affected_device(
            nautobot_url, nautobot_client, contexts
        )
        if context_selection is None:
            pytest.fail("Mock topology has no scoped config context affecting a managed device")
        context, context_target = context_selection

        prefix_selection = _scoped_record_and_affected_device(
            nautobot_url, nautobot_client, prefixes
        )
        if prefix_selection is None:
            pytest.fail("Mock topology has no scoped prefix affecting a managed device")
        prefix, prefix_target = prefix_selection

        _patch_and_assert_event(
            nautobot_url,
            nautobot_client,
            render_api_url,
            render_client,
            target_device_id=context_target["id"],
            path=f"extras/config-contexts/{context['id']}",
            object_type="extras.configcontext",
            changes={"description": f"nvcm-provider-context-{uuid4().hex}"},
            restore_changes={"description": context.get("description") or ""},
        )
        _patch_and_assert_event(
            nautobot_url,
            nautobot_client,
            render_api_url,
            render_client,
            target_device_id=prefix_target["id"],
            path=f"ipam/prefixes/{prefix['id']}",
            object_type="ipam.prefix",
            changes={"description": f"nvcm-provider-prefix-{uuid4().hex}"},
            restore_changes={"description": prefix.get("description") or ""},
        )

        managed_status = next(
            (
                candidate
                for candidate in _api_list(
                    nautobot_url,
                    nautobot_client,
                    "plugins/nv-config-manager/configmanagerdevicestatus",
                )
                if candidate["device"]["id"] == context_target["id"]
            ),
            None,
        )
        if managed_status is None:
            pytest.fail(f"Mock topology has no managed-device record for {context_target['name']}")
        _patch_and_assert_event(
            nautobot_url,
            nautobot_client,
            render_api_url,
            render_client,
            target_device_id=context_target["id"],
            path=f"plugins/nv-config-manager/configmanagerdevicestatus/{managed_status['id']}",
            object_type="nv_config_manager.configmanagerdevicestatus",
            changes={"backup_enabled": not managed_status["backup_enabled"]},
            restore_changes={"backup_enabled": managed_status["backup_enabled"]},
        )

    def test_cable_event_resolves_its_terminations(
        self,
        nautobot_url: str,
        nautobot_client: requests.Session,
        render_api_url: str,
        render_client: requests.Session,
    ) -> None:
        """A ``dcim.cable`` update resolves its managed-device termination."""
        managed_device_ids = {
            device["device"]["id"] for device in _render_devices(nautobot_url, nautobot_client)
        }
        interfaces = {
            interface["id"]: interface
            for interface in _api_list(nautobot_url, nautobot_client, "dcim/interfaces")
        }
        cable = next(
            (
                candidate
                for candidate in _api_list(nautobot_url, nautobot_client, "dcim/cables")
                if any(
                    (interfaces.get(termination_id, {}).get("device") or {}).get("id")
                    in managed_device_ids
                    for termination_id in (
                        candidate.get("termination_a_id"),
                        candidate.get("termination_b_id"),
                    )
                )
            ),
            None,
        )
        if cable is None:
            pytest.fail("Mock topology has no cable attached to a render-enabled device")
        target_device_id = next(
            (
                interface["device"]["id"]
                for termination_id in (cable["termination_a_id"], cable["termination_b_id"])
                if (interface := interfaces.get(termination_id))
                and interface.get("device")
                and interface["device"]["id"] in managed_device_ids
            ),
            None,
        )
        if target_device_id is None:
            pytest.fail(f"Cable {cable['id']} has no render-enabled termination")

        _patch_and_assert_event(
            nautobot_url,
            nautobot_client,
            render_api_url,
            render_client,
            target_device_id=target_device_id,
            path=f"dcim/cables/{cable['id']}",
            object_type="dcim.cable",
            changes={"label": f"nvcm-provider-cable-{uuid4().hex}"},
            restore_changes={"label": cable.get("label") or ""},
        )

    def test_bgp_events_resolve_affected_devices(
        self,
        nautobot_url: str,
        nautobot_client: requests.Session,
        render_api_url: str,
        render_client: requests.Session,
    ) -> None:
        """Every represented Nautobot BGP event resolves its associated device."""
        managed_device_ids = {
            device["device"]["id"] for device in _render_devices(nautobot_url, nautobot_client)
        }
        routing_instances = {
            candidate["id"]: candidate
            for candidate in _api_list(
                nautobot_url, nautobot_client, "plugins/bgp/routing-instances"
            )
        }
        peer_endpoint = next(
            (
                candidate
                for candidate in _api_list(
                    nautobot_url, nautobot_client, "plugins/bgp/peer-endpoints"
                )
                if candidate.get("peering")
                and (
                    routing_instance := routing_instances.get(
                        (candidate.get("routing_instance") or {}).get("id")
                    )
                )
                and routing_instance["device"]["id"] in managed_device_ids
            ),
            None,
        )
        if peer_endpoint is None:
            pytest.fail("Mock topology has no BGP peer endpoint on a render-enabled device")
        routing_instance = routing_instances[peer_endpoint["routing_instance"]["id"]]
        target_device_id = routing_instance["device"]["id"]

        autonomous_system = next(
            (
                candidate
                for candidate in _api_list(
                    nautobot_url, nautobot_client, "plugins/bgp/autonomous-systems"
                )
                if candidate["id"] == routing_instance["autonomous_system"]["id"]
            ),
            None,
        )
        if autonomous_system is None:
            pytest.fail(
                f"Routing instance {routing_instance['id']} has no autonomous-system record"
            )
        _patch_and_assert_event(
            nautobot_url,
            nautobot_client,
            render_api_url,
            render_client,
            target_device_id=target_device_id,
            path=f"plugins/bgp/autonomous-systems/{autonomous_system['id']}",
            object_type="nautobot_bgp_models.autonomoussystem",
            changes={"description": f"nvcm-provider-asn-{uuid4().hex}"},
            restore_changes={"description": autonomous_system.get("description") or ""},
        )
        _patch_and_assert_event(
            nautobot_url,
            nautobot_client,
            render_api_url,
            render_client,
            target_device_id=target_device_id,
            path=f"plugins/bgp/routing-instances/{routing_instance['id']}",
            object_type="nautobot_bgp_models.bgproutinginstance",
            changes={"description": f"nvcm-provider-routing-{uuid4().hex}"},
            restore_changes={"description": routing_instance.get("description") or ""},
        )

        _patch_and_assert_event(
            nautobot_url,
            nautobot_client,
            render_api_url,
            render_client,
            target_device_id=target_device_id,
            path=f"plugins/bgp/peer-endpoints/{peer_endpoint['id']}",
            object_type="nautobot_bgp_models.peerendpoint",
            changes={"description": f"nvcm-provider-peer-endpoint-{uuid4().hex}"},
            restore_changes={"description": peer_endpoint.get("description") or ""},
        )

        peering = next(
            (
                candidate
                for candidate in _api_list(nautobot_url, nautobot_client, "plugins/bgp/peerings")
                if candidate["id"] == peer_endpoint["peering"]["id"]
            ),
            None,
        )
        statuses = _api_list(
            nautobot_url,
            nautobot_client,
            "extras/statuses",
            content_types="nautobot_bgp_models.peering",
        )
        if peering is None or not peering.get("status"):
            pytest.fail(f"Peer endpoint {peer_endpoint['id']} has no mutable peering status")
        alternate_status = next(
            (status for status in statuses if status["id"] != peering["status"]["id"]),
            None,
        )
        if alternate_status is None:
            pytest.fail("Mock topology has no alternate BGP peering status")
        _patch_and_assert_event(
            nautobot_url,
            nautobot_client,
            render_api_url,
            render_client,
            target_device_id=target_device_id,
            path=f"plugins/bgp/peerings/{peering['id']}",
            object_type="nautobot_bgp_models.peering",
            changes={"status": alternate_status["id"]},
            restore_changes={"status": peering["status"]["id"]},
        )
