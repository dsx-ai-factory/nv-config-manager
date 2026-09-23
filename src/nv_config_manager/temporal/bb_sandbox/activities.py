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
"""Real sandbox intent activities and mocked Backbone device interactions."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
from copy import deepcopy
from typing import Any
from uuid import UUID

from nv_config_manager_templates.render import Renderer
from pydantic import BaseModel, Field
from temporalio import activity
from temporalio.exceptions import ApplicationError

from nv_config_manager.common.config import (
    ConfigStoreType,
    config_store_client,
    pynautobot_client,
)
from nv_config_manager.temporal.client.device import (
    DiffChangedException,
    MockNetworkConnection,
    NetworkConnection,
)
from nv_config_manager.temporal.client.nautobot import NautobotClient
from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData

MAINTENANCE_STATUS = "Maintenance"
ACTIVE_STATUS = "Active"
PLANNED_STATUS = "Planned"
MOCK_DRAIN_METRIC = 1_000_000
RESERVATION_COMMENT_KEY = "bb_reservation"
LAG_SEQUENCE_START = 1200
SANDBOX_PROVIDER = "BB Sandbox Demo"
SANDBOX_CIRCUIT_TYPE = "Internal Backbone Demo"


class InterfaceIntent(BaseModel):
    """Resolved native Nautobot interface intent."""

    id: str
    name: str
    status: str
    isis_metric: int = 10
    addresses: list[str] = Field(default_factory=list)
    custom_fields: dict[str, Any] = Field(default_factory=dict)


class DrainLookupInput(BaseModel):
    """Input for resolving one drain target."""

    device: str
    port: str


class DrainIntent(BaseModel):
    """Resolved device and interface used by the drain workflow."""

    device_id: str
    device_name: str
    interface: InterfaceIntent


class SetInterfaceStatusInput(BaseModel):
    """Native Nautobot status update."""

    interface_ids: list[str]
    status: str
    isis_metric: int | None = None
    expected_status: str | None = None
    expected_isis_metric: int | None = None


class NautobotMutationOutput(BaseModel):
    """Summary of a real Nautobot mutation."""

    updated_ids: list[str]
    status: str


class InternalBackboneLookupInput(BaseModel):
    """Input for resolving both endpoints of an internal Backbone circuit."""

    circuit_id: str
    local_device: str
    local_ports: list[str]
    remote_device: str
    remote_ports: list[str]
    lag_name: str | None
    ipv4_prefix: str
    ipv6_prefix: str
    igp_metric: int
    minimum_links: int


class BackboneEndpointIntent(BaseModel):
    """Resolved device, LAG, and member interfaces for one circuit endpoint."""

    device_id: str
    device_name: str
    interfaces: list[InterfaceIntent]
    lag: InterfaceIntent
    ipv4_address: str
    ipv6_address: str


class InternalBackboneIntent(BaseModel):
    """Resolved two-ended internal Backbone circuit intent."""

    circuit_uuid: str
    circuit_id: str
    local: BackboneEndpointIntent
    remote: BackboneEndpointIntent
    ipv4_prefix: str
    ipv6_prefix: str
    igp_metric: int
    minimum_links: int


class EnableBackboneInterfacesInput(BaseModel):
    """Native Nautobot mutations for both physical circuit endpoints."""

    local_interface_ids: list[str]
    local_device_id: str
    local_lag_name: str
    local_remote_device: str
    remote_interface_ids: list[str]
    remote_device_id: str
    remote_lag_name: str
    remote_remote_device: str
    minimum_links: int


class BackbonePhysicalMutationOutput(NautobotMutationOutput):
    """IDs of the newly created common-name endpoint LAGs."""

    local_lag_id: str
    remote_lag_id: str


class ApplyBackboneAddressingInput(BaseModel):
    """Native dual-stack addressing intent for both LAGs."""

    circuit_uuid: str
    local_lag_id: str
    remote_lag_id: str
    local_ipv4: str
    remote_ipv4: str
    local_ipv6: str
    remote_ipv6: str
    expected_rtt_ms: float
    jira: str
    requested_by: str | None


class ActivateBackboneRoutingInput(BaseModel):
    """Explicit IS-IS intent for both endpoint LAGs."""

    interface_ids: list[str]
    igp_metric: int


class MockDiffInput(BaseModel):
    """Parameters used to construct a simulated device candidate diff."""

    phase: str
    device: str
    ports: list[str]
    lag_name: str | None = None
    remote_device: str | None = None
    remote_ports: list[str] = Field(default_factory=list)
    remote_lag: str | None = None
    local_ipv4: str | None = None
    remote_ipv4: str | None = None
    local_ipv6: str | None = None
    remote_ipv6: str | None = None
    igp_metric: int | None = None
    minimum_links: int | None = None


class MockDiffOutput(BaseModel):
    """A candidate diff produced without contacting a device."""

    diff: str
    mocked: bool = True


class RenderRevisionDiffInput(BaseModel):
    """Pinned Config Store revision produced by a post-mutation render."""

    device_id: str
    device_name: str
    filename: str
    to_version: int


class RenderRevisionDiffOutput(BaseModel):
    """Actual intended-config delta between consecutive render revisions."""

    diff: str
    from_version: int
    to_version: int


class DrainCandidateInput(BaseModel):
    """Rendered Junos interfaces candidate and its single drain target."""

    device_data: NetworkDeviceData
    configuration: str
    interface_name: str
    current_metric: int = Field(ge=1, le=16_777_214)


class DrainCandidateOutput(BaseModel):
    """Device candidate diff with whether device interaction was mocked."""

    diff: str
    mocked: bool


class ProposedInterfaceRenderInput(BaseModel):
    """Nautobot interface values to override for a non-persistent render."""

    device_id: str
    interface_name: str
    status: str
    isis_metric: int = Field(ge=1, le=16_777_214)
    filename: str


class ProposedInterfaceRenderOutput(BaseModel):
    """Rendered configuration produced without changing Nautobot or Config Store."""

    configuration: str
    filename: str


class DrainApplyInput(DrainCandidateInput):
    """Approved rendered Junos interfaces candidate."""

    approved_diff: str


class DrainApplyOutput(BaseModel):
    """Result of applying the drain candidate."""

    mocked: bool


class MockAppliedIntentInput(BaseModel):
    """Expected post-deployment state for a mocked Backbone device."""

    phase: str
    device: str
    lag_name: str
    member_ports: list[str] = Field(default_factory=list)
    ipv4_address: str | None = None
    ipv6_address: str | None = None
    igp_metric: int | None = None


class MockAppliedIntentOutput(BaseModel):
    """Mock device observation matching the expected rendered intent."""

    healthy: bool = True
    observations: list[str]


class MockNeighborInput(BaseModel):
    """Expected physical neighbor validation."""

    device: str
    ports: list[str]
    expected_neighbor: str


class MockNeighborOutput(BaseModel):
    """Simulated LLDP validation result."""

    matched: bool
    observed_neighbor: str | None
    mocked: bool = True


class MockPingInput(BaseModel):
    """Input for a simulated point-to-point ping."""

    source: str
    destination: str
    expected_rtt_ms: float


class MockPingOutput(BaseModel):
    """Simulated ping statistics."""

    transmitted: int
    received: int
    average_rtt_ms: float
    healthy: bool
    mocked: bool = True


class MockRoutingInput(BaseModel):
    """Input for simulated internal Backbone protocol health checks."""

    device: str
    lag_name: str
    remote_device: str


class MockRoutingOutput(BaseModel):
    """Simulated routing adjacency health."""

    igp_state: str
    mpls_state: str
    rsvp_state: str
    ibgp_reachability: str
    mocked: bool = True


BACKBONE_DEVICE_ROLE = "Backbone Router"


class RelatedIgpChange(BaseModel):
    """POP-pair IS-IS metric change, matching bb_confgen isis.json keys."""

    local_pop: str
    remote_pop: str
    metric: int
    macsec: bool = True


class CircuitTurnupLookupInput(BaseModel):
    """Operator intent for a WAN circuit turn-up plus related IGP retunes."""

    circuit_id: str
    local_device: str
    local_ports: list[str]
    local_lag: str
    remote_device: str
    remote_ports: list[str]
    remote_lag: str
    ipv4_prefix: str
    minimum_links: int
    path_metric: int
    macsec: bool
    related_metrics: list[RelatedIgpChange] = Field(default_factory=list)


class CircuitTurnupEndpoint(BaseModel):
    """One circuit endpoint resolved from Nautobot."""

    device_id: str
    device_name: str
    lag_name: str
    ports: list[str]
    ipv4_address: str


class CircuitTurnupIntent(BaseModel):
    """Resolved turn-up plan. Nautobot is not mutated until persist."""

    circuit_id: str
    circuit_uuid: str
    local: CircuitTurnupEndpoint
    remote: CircuitTurnupEndpoint
    ipv4_prefix: str
    minimum_links: int
    path_metric: int
    macsec: bool
    xconnect: str | None
    related_metrics: list[RelatedIgpChange]
    impacted_devices: list[str]
    preflight_notes: list[str]


class DevicePlanDiff(BaseModel):
    """Proposed candidate for one impacted device."""

    device_name: str
    diff: str


class CircuitTurnupPlanOutput(BaseModel):
    """In-memory candidate diffs. Config Store is unchanged."""

    devices: list[DevicePlanDiff]
    combined_diff: str
    mocked: bool = True


class ProposedNautobotMutation(BaseModel):
    """Nautobot write the approved plan would perform."""

    model: str
    summary: str


class CircuitTurnupPersistOutput(BaseModel):
    """Intended Nautobot writes."""

    mutations: list[ProposedNautobotMutation]
    written: bool = False


class CircuitTurnupValidateOutput(BaseModel):
    """Simulated post-change checks from the GNI ticket."""

    checks: list[str]
    healthy: bool
    mocked: bool = True


class CircuitReservationLookupInput(BaseModel):
    """Allocate unused ports, LAGs, and a /31 for a Planned circuit."""

    local_device: str
    remote_device: str
    jira: str
    circuit_id: str | None = None
    path_metric: int = 750
    macsec: bool = True
    minimum_links: int = 1


class CircuitReservationPlan(BaseModel):
    """Allocated reservation that has not been written yet."""

    intent: CircuitTurnupIntent
    create_circuit: bool
    parent_prefix: str
    location_ids: dict[str, str] = Field(default_factory=dict)


class CircuitReservationPersistOutput(BaseModel):
    """Nautobot objects written as Planned."""

    circuit_uuid: str
    written: bool = True
    mutations: list[ProposedNautobotMutation]


class PlannedCircuitObjects(BaseModel):
    """Planned circuit and the objects activate will flip to Active."""

    intent: CircuitTurnupIntent
    circuit_uuid: str
    interface_ids: list[str]
    ip_ids: list[str]
    prefix_ids: list[str]


class ActivationDeviceCheckInput(BaseModel):
    """Probe whether reserved LAG, member, and address are still absent."""

    device_data: NetworkDeviceData
    lag_name: str
    member_port: str
    ipv4_address: str


class ActivationDeviceCheckOutput(BaseModel):
    """Occupancy found by a partial candidate diff against the device."""

    mocked: bool
    conflicts: list[str]
    diff: str

    @classmethod
    def from_mock(cls) -> ActivationDeviceCheckOutput:
        return cls(mocked=True, conflicts=[], diff="")

    @classmethod
    def from_candidate_diff(
        cls,
        device_name: str,
        lag_name: str,
        member_port: str,
        ipv4_address: str,
        diff: str,
    ) -> ActivationDeviceCheckOutput:
        return cls(
            mocked=False,
            conflicts=_activation_occupancy_conflicts(
                device_name,
                lag_name,
                member_port,
                ipv4_address,
                diff,
            ),
            diff=diff,
        )


def _results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return list(payload.get("results") or [])


async def _lookup_one(
    client: NautobotClient,
    path: str,
    params: dict[str, Any],
    description: str,
) -> dict[str, Any]:
    payload = await client.get(path, params=params)
    results = _results(payload)
    if not results:
        raise ApplicationError(f"{description} not found in Nautobot", non_retryable=True)
    if len(results) > 1:
        raise ApplicationError(
            f"{description} is ambiguous ({len(results)} matches)", non_retryable=True
        )
    return results[0]


async def _resolve_device(client: NautobotClient, reference: str) -> dict[str, Any]:
    try:
        UUID(reference)
    except ValueError:
        return await _lookup_one(
            client, "dcim/devices/", {"name": reference}, f"Device {reference!r}"
        )
    return await client.get(f"dcim/devices/{reference}/")


def _interface_intent(interface: dict[str, Any]) -> InterfaceIntent:
    addresses = [entry["address"] for entry in interface.get("ip_addresses") or []]
    return InterfaceIntent(
        id=str(interface["id"]),
        name=str(interface["name"]),
        status=_status_label(interface),
        addresses=addresses,
        custom_fields=dict(interface.get("custom_fields") or {}),
    )


def _status_id(interface: dict[str, Any]) -> str | None:
    """Return an interface's status UUID from any REST serializer depth."""
    status = interface.get("status")
    if isinstance(status, str):
        return status
    if isinstance(status, dict) and status.get("id"):
        return str(status["id"])
    return None


def _status_label(interface: dict[str, Any]) -> str:
    """Return a human-readable status name, which depth-0 payloads omit."""
    status = interface.get("status")
    if not isinstance(status, dict):
        return "Unknown"
    return str(status.get("name") or status.get("display") or "Unknown")


async def _resolve_status(client: NautobotClient, name: str) -> str:
    status = await _lookup_one(client, "extras/statuses/", {"name": name}, f"Status {name!r}")
    return str(status["id"])


async def _isis_metric(client: NautobotClient, interface_id: str) -> int:
    payload = await client.get(
        "plugins/routing/isis-interfaces/",
        params={"interface": interface_id},
    )
    results = _results(payload)
    if not results:
        return 10
    if len(results) > 1:
        raise ApplicationError(
            f"Multiple IS-IS records for interface {interface_id}",
            non_retryable=True,
        )
    return int(results[0]["metric"])


async def _upsert_isis_interface(
    client: NautobotClient,
    interface_id: str,
    metric: int,
) -> str:
    payload = await client.get(
        "plugins/routing/isis-interfaces/",
        params={"interface": interface_id},
    )
    results = _results(payload)
    if len(results) > 1:
        raise ApplicationError(
            f"Multiple IS-IS records for interface {interface_id}",
            non_retryable=True,
        )
    if results:
        record_id = str(results[0]["id"])
        await client.patch(
            f"plugins/routing/isis-interfaces/{record_id}/",
            data={"metric": metric},
        )
        return record_id
    record = await client.post(
        "plugins/routing/isis-interfaces/",
        data={"interface": interface_id, "metric": metric, "level": "2", "passive": False},
    )
    return str(record["id"])


def _point_to_point_addresses(prefix: str) -> tuple[str, str]:
    network = ipaddress.ip_network(prefix)
    if network.num_addresses != 2:
        raise ApplicationError(
            f"Backbone prefix {prefix} is not a point-to-point /31 or /127",
            non_retryable=True,
        )
    hosts = list(network.hosts())
    return f"{hosts[0]}/{network.prefixlen}", f"{hosts[1]}/{network.prefixlen}"


@activity.defn
async def resolve_drain_intent(activity_input: DrainLookupInput) -> DrainIntent:
    """Resolve the real Nautobot device and interface before draining."""
    client = NautobotClient()
    async with client:
        device = await _resolve_device(client, activity_input.device)
        interface = await _lookup_one(
            client,
            "dcim/interfaces/",
            {"device": device["id"], "name": activity_input.port, "depth": 1},
            f"Interface {device['name']}:{activity_input.port}",
        )
        intent = _interface_intent(interface)
        intent.isis_metric = await _isis_metric(client, intent.id)
        return DrainIntent(
            device_id=str(device["id"]),
            device_name=str(device["name"]),
            interface=intent,
        )


@activity.defn
async def set_interface_status(
    activity_input: SetInterfaceStatusInput,
) -> NautobotMutationOutput:
    """Apply an interface status after checking the expected Nautobot state."""
    client = NautobotClient()
    async with client:
        expected_status_id = (
            await _resolve_status(client, activity_input.expected_status)
            if activity_input.expected_status is not None
            else None
        )
        for interface_id in activity_input.interface_ids:
            interface = await client.get(
                f"dcim/interfaces/{interface_id}/",
                params={"depth": 1},
            )
            current_status = _status_label(interface)
            current_metric = await _isis_metric(client, interface_id)
            status_changed = (
                expected_status_id is not None and _status_id(interface) != expected_status_id
            )
            metric_changed = (
                activity_input.expected_isis_metric is not None
                and current_metric != activity_input.expected_isis_metric
            )
            if status_changed or metric_changed:
                raise ApplicationError(
                    f"Nautobot drift on {interface_id}: expected "
                    f"{activity_input.expected_status}/metric "
                    f"{activity_input.expected_isis_metric}, found "
                    f"{current_status}/metric {current_metric}; nothing persisted",
                    non_retryable=True,
                )
        status_id = await _resolve_status(client, activity_input.status)
        for interface_id in activity_input.interface_ids:
            await client.patch(
                f"dcim/interfaces/{interface_id}/",
                data={"status": status_id},
            )
            if activity_input.isis_metric is not None:
                await _upsert_isis_interface(client, interface_id, activity_input.isis_metric)
    return NautobotMutationOutput(
        updated_ids=activity_input.interface_ids,
        status=activity_input.status,
    )


async def _resolve_endpoint(
    client: NautobotClient,
    device: dict[str, Any],
    ports: list[str],
    lag_name: str,
    ipv4_address: str,
    ipv6_address: str,
) -> BackboneEndpointIntent:
    interfaces = [
        await _lookup_one(
            client,
            "dcim/interfaces/",
            {"device": device["id"], "name": port, "depth": 2},
            f"Interface {device['name']}:{port}",
        )
        for port in ports
    ]
    return BackboneEndpointIntent(
        device_id=str(device["id"]),
        device_name=str(device["name"]),
        interfaces=[_interface_intent(interface) for interface in interfaces],
        lag=InterfaceIntent(id="", name=lag_name, status="Planned"),
        ipv4_address=ipv4_address,
        ipv6_address=ipv6_address,
    )


async def _next_common_lag_name(
    client: NautobotClient,
    local_device_id: str,
    remote_device_id: str,
    requested_name: str | None,
) -> str:
    """Return the first ae sequence >=100 unused on both endpoint devices."""
    return await client.get_next_common_lag_name(
        local_device_id,
        remote_device_id,
        requested_name=requested_name,
    )


@activity.defn
async def resolve_internal_backbone_intent(
    activity_input: InternalBackboneLookupInput,
) -> InternalBackboneIntent:
    """Resolve and validate both endpoints of a native Backbone circuit."""
    local_ipv4, remote_ipv4 = _point_to_point_addresses(activity_input.ipv4_prefix)
    local_ipv6, remote_ipv6 = _point_to_point_addresses(activity_input.ipv6_prefix)
    client = NautobotClient()
    async with client:
        local_device = await _resolve_device(client, activity_input.local_device)
        remote_device = await _resolve_device(client, activity_input.remote_device)
        lag_name = await _next_common_lag_name(
            client,
            str(local_device["id"]),
            str(remote_device["id"]),
            activity_input.lag_name,
        )
        circuit = await _lookup_one(
            client,
            "circuits/circuits/",
            {"cid": activity_input.circuit_id, "depth": 2},
            f"Circuit {activity_input.circuit_id!r}",
        )
        local = await _resolve_endpoint(
            client,
            local_device,
            activity_input.local_ports,
            lag_name,
            local_ipv4,
            local_ipv6,
        )
        remote = await _resolve_endpoint(
            client,
            remote_device,
            activity_input.remote_ports,
            lag_name,
            remote_ipv4,
            remote_ipv6,
        )
        terminations = _results(
            await client.get(
                "circuits/circuit-terminations/",
                params={"circuit": circuit["id"], "depth": 2},
            )
        )

    described_devices = {
        str(termination.get("description") or "").removeprefix("Remote endpoint ").split(":", 1)[0]
        for termination in terminations
        if str(termination.get("description") or "").startswith("Remote endpoint ")
    }
    expected_devices = {local.device_name, remote.device_name}
    if described_devices and not expected_devices.issubset(described_devices):
        raise ApplicationError(
            f"Circuit {circuit['cid']} termination metadata {sorted(described_devices)} does not "
            f"match requested endpoints {sorted(expected_devices)}",
            non_retryable=True,
        )
    return InternalBackboneIntent(
        circuit_uuid=str(circuit["id"]),
        circuit_id=str(circuit["cid"]),
        local=local,
        remote=remote,
        ipv4_prefix=activity_input.ipv4_prefix,
        ipv6_prefix=activity_input.ipv6_prefix,
        igp_metric=activity_input.igp_metric,
        minimum_links=activity_input.minimum_links,
    )


async def _enable_endpoint(
    client: NautobotClient,
    interface_ids: list[str],
    device_id: str,
    lag_name: str,
    remote_device: str,
    minimum_links: int,
) -> tuple[str, list[str]]:
    status_id = await _resolve_status(client, ACTIVE_STATUS)
    lag = await client.post(
        "dcim/interfaces/",
        data={
            "device": device_id,
            "name": lag_name,
            "type": "lag",
            "status": status_id,
            "enabled": True,
            "description": f"WAN:{remote_device}:{lag_name}:BB Sandbox Demo",
            "custom_fields": {"bb_min_links": minimum_links},
        },
    )
    lag_id = str(lag["id"])
    for interface_id in interface_ids:
        await client.patch(
            f"dcim/interfaces/{interface_id}/",
            data={"enabled": True, "lag": lag_id},
        )
    return lag_id, [lag_id, *interface_ids]


@activity.defn
async def enable_backbone_interfaces(
    activity_input: EnableBackboneInterfacesInput,
) -> BackbonePhysicalMutationOutput:
    """Enable and bind physical members at both internal circuit endpoints."""
    client = NautobotClient()
    async with client:
        local_lag_id, local_ids = await _enable_endpoint(
            client,
            activity_input.local_interface_ids,
            activity_input.local_device_id,
            activity_input.local_lag_name,
            activity_input.local_remote_device,
            activity_input.minimum_links,
        )
        remote_lag_id, remote_ids = await _enable_endpoint(
            client,
            activity_input.remote_interface_ids,
            activity_input.remote_device_id,
            activity_input.remote_lag_name,
            activity_input.remote_remote_device,
            activity_input.minimum_links,
        )
    return BackbonePhysicalMutationOutput(
        updated_ids=[*local_ids, *remote_ids],
        status="Enabled",
        local_lag_id=local_lag_id,
        remote_lag_id=remote_lag_id,
    )


async def _ensure_ip_assignment(
    client: NautobotClient,
    address: str,
    interface_id: str,
    namespace_id: str,
    status_id: str,
    ensured_prefixes: set[str],
) -> str:
    network = str(ipaddress.ip_interface(address).network)
    if network not in ensured_prefixes:
        prefix_result = await client.get(
            "ipam/prefixes/", params={"prefix": network, "namespace": namespace_id}
        )
        if not _results(prefix_result):
            await client.post(
                "ipam/prefixes/",
                data={
                    "prefix": network,
                    "namespace": namespace_id,
                    "status": status_id,
                    "type": "network",
                },
            )
        ensured_prefixes.add(network)
    address_result = await client.get(
        "ipam/ip-addresses/", params={"address": address, "namespace": namespace_id}
    )
    addresses = _results(address_result)
    ip_record = (
        addresses[0]
        if addresses
        else await client.post(
            "ipam/ip-addresses/",
            data={"address": address, "namespace": namespace_id, "status": status_id},
        )
    )
    assignment_result = await client.get(
        "ipam/ip-address-to-interface/",
        params={"ip_address": ip_record["id"], "interface": interface_id},
    )
    if not _results(assignment_result):
        await client.post(
            "ipam/ip-address-to-interface/",
            data={"ip_address": ip_record["id"], "interface": interface_id},
        )
    return str(ip_record["id"])


@activity.defn
async def apply_backbone_addressing(
    activity_input: ApplyBackboneAddressingInput,
) -> NautobotMutationOutput:
    """Persist dual-stack addressing and circuit audit intent in Nautobot."""
    client = NautobotClient()
    async with client:
        namespace = await _lookup_one(
            client, "ipam/namespaces/", {"name": "Global"}, "Global namespace"
        )
        status_id = await _resolve_status(client, ACTIVE_STATUS)
        address_ids = []
        ensured_prefixes: set[str] = set()
        for address, lag_id in (
            (activity_input.local_ipv4, activity_input.local_lag_id),
            (activity_input.remote_ipv4, activity_input.remote_lag_id),
            (activity_input.local_ipv6, activity_input.local_lag_id),
            (activity_input.remote_ipv6, activity_input.remote_lag_id),
        ):
            address_ids.append(
                await _ensure_ip_assignment(
                    client,
                    address,
                    lag_id,
                    str(namespace["id"]),
                    status_id,
                    ensured_prefixes,
                )
            )
        circuit = await client.get(f"circuits/circuits/{activity_input.circuit_uuid}/")
        circuit_fields = dict(circuit.get("custom_fields") or {})
        circuit_fields.update(
            {
                "bb_change_ticket": activity_input.jira,
                "bb_expected_rtt_ms": f"{activity_input.expected_rtt_ms:g}",
                "bb_requested_by": activity_input.requested_by or "unknown",
            }
        )
        await client.patch(
            f"circuits/circuits/{activity_input.circuit_uuid}/",
            data={"custom_fields": circuit_fields},
        )
    return NautobotMutationOutput(updated_ids=address_ids, status="Addressed")


@activity.defn
async def activate_backbone_routing(
    activity_input: ActivateBackboneRoutingInput,
) -> NautobotMutationOutput:
    """Persist IS-IS peering intent and Active status on both endpoint LAGs."""
    client = NautobotClient()
    async with client:
        status_id = await _resolve_status(client, ACTIVE_STATUS)
        updated_ids = []
        for interface_id in activity_input.interface_ids:
            await client.patch(
                f"dcim/interfaces/{interface_id}/",
                data={"status": status_id},
            )
            updated_ids.append(
                await _upsert_isis_interface(client, interface_id, activity_input.igp_metric)
            )
    return NautobotMutationOutput(
        updated_ids=updated_ids,
        status=ACTIVE_STATUS,
    )


@activity.defn
async def build_mock_candidate_diff(activity_input: MockDiffInput) -> MockDiffOutput:
    """Build a realistic Junos candidate diff without opening a device session."""
    if activity_input.phase == "drain":
        port = activity_input.ports[0]
        diff = (
            f"[edit protocols isis interface {port}]\n"
            f"-   level 2 metric 10;\n+   level 2 metric {MOCK_DRAIN_METRIC};"
        )
    elif activity_input.phase == "physical":
        lines = [
            *[
                f"[{activity_input.device} edit interfaces {port}]\n-   disable;"
                for port in activity_input.ports
            ],
            *[
                f"[{activity_input.remote_device} edit interfaces {port}]\n-   disable;"
                for port in activity_input.remote_ports
            ],
            f"[{activity_input.device} edit interfaces {activity_input.lag_name} aggregated-ether-options]\n"
            f"+   minimum-links {activity_input.minimum_links};",
            f"[{activity_input.remote_device} edit interfaces {activity_input.remote_lag} aggregated-ether-options]\n"
            f"+   minimum-links {activity_input.minimum_links};",
        ]
        diff = "\n".join(lines)
    elif activity_input.phase == "addressing":
        diff = (
            f"[{activity_input.device} edit interfaces {activity_input.lag_name} unit 0]\n"
            f"+   family inet {{ address {activity_input.local_ipv4}; }}\n"
            f"+   family inet6 {{ address {activity_input.local_ipv6}; }}\n"
            f"[{activity_input.remote_device} edit interfaces {activity_input.remote_lag} unit 0]\n"
            f"+   family inet {{ address {activity_input.remote_ipv4}; }}\n"
            f"+   family inet6 {{ address {activity_input.remote_ipv6}; }}"
        )
    elif activity_input.phase == "routing":
        diff = (
            f"[{activity_input.device} edit protocols isis interface {activity_input.lag_name}.0]\n"
            f"+   point-to-point;\n+   level 2 metric {activity_input.igp_metric};\n"
            f"[{activity_input.device} edit protocols mpls]\n"
            f"+   interface {activity_input.lag_name}.0;\n"
            f"[{activity_input.device} edit protocols rsvp]\n"
            f"+   interface {activity_input.lag_name}.0;\n"
            f"[{activity_input.remote_device} edit protocols isis interface {activity_input.remote_lag}.0]\n"
            f"+   point-to-point;\n+   level 2 metric {activity_input.igp_metric};\n"
            f"[{activity_input.remote_device} edit protocols mpls]\n"
            f"+   interface {activity_input.remote_lag}.0;\n"
            f"[{activity_input.remote_device} edit protocols rsvp]\n"
            f"+   interface {activity_input.remote_lag}.0;"
        )
    else:
        raise ApplicationError(
            f"Unknown mock diff phase {activity_input.phase!r}", non_retryable=True
        )
    return MockDiffOutput(diff=diff)


@activity.defn
async def load_render_revision_diff(
    activity_input: RenderRevisionDiffInput,
) -> RenderRevisionDiffOutput:
    """Load the actual Config Store delta for the pinned post-mutation render."""
    client = config_store_client(ConfigStoreType.INTENDED)
    async with client:
        versions_response = await client.get_config_versions(
            activity_input.device_id,
            activity_input.filename,
            limit=100,
        )
        raw_versions = versions_response.get("versions", [])
        versions = sorted(int(version["version"]) for version in raw_versions)
        prior_versions = [version for version in versions if version < activity_input.to_version]
        if not prior_versions:
            raise ApplicationError(
                f"No baseline {activity_input.filename} render for {activity_input.device_name}",
                non_retryable=True,
            )
        from_version = prior_versions[-1]
        diff_response = await client.get_config_diff(
            activity_input.device_id,
            activity_input.filename,
            from_version,
            activity_input.to_version,
        )
    return RenderRevisionDiffOutput(
        diff=str(diff_response["diff"]),
        from_version=from_version,
        to_version=activity_input.to_version,
    )


def _mock_drain_diff(interface_name: str, current_metric: int) -> str:
    """Return the focused Junos comparison used by the local device backend."""
    return (
        f"[edit protocols isis interface {interface_name}.0]\n"
        f"-   level 2 metric {current_metric};\n"
        f"+   level 2 metric {MOCK_DRAIN_METRIC};"
    )


def _diff_side(diff: str, prefix: str) -> str:
    return "\n".join(line[1:] for line in diff.splitlines() if line.startswith(prefix))


def _activation_probe_config(lag_name: str, member_port: str, ipv4_address: str) -> str:
    return (
        "interfaces {\n"
        f"    {member_port} {{\n"
        "        ether-options {\n"
        f"            802.3ad {lag_name};\n"
        "        }\n"
        "    }\n"
        f"    {lag_name} {{\n"
        "        unit 0 {{\n"
        "            family inet {{\n"
        f"                address {ipv4_address};\n"
        "            }}\n"
        "        }}\n"
        "    }}\n"
        "}\n"
    )


def _activation_occupancy_conflicts(
    device_name: str,
    lag_name: str,
    member_port: str,
    ipv4_address: str,
    diff: str,
) -> list[str]:
    added = _diff_side(diff, "+")
    removed = _diff_side(diff, "-")
    conflicts: list[str] = []
    if not re.search(rf"(?m)^\s*{re.escape(lag_name)}\s*\{{", added):
        conflicts.append(f"{device_name}:{lag_name} already configured")
    member_taken = f"802.3ad {lag_name}" not in added or "802.3ad" in removed
    if member_taken:
        conflicts.append(f"{device_name}:{member_port} already aggregated")
    if ipv4_address not in added:
        conflicts.append(f"{device_name} already has {ipv4_address}")
    return conflicts


def _override_interface_render_data(
    device_data: dict[str, Any],
    interface_name: str,
    status: str,
    isis_metric: int,
) -> dict[str, Any]:
    """Copy render data and replace only the interface values the workflow will persist."""
    proposed_data = deepcopy(device_data)
    device = proposed_data.get("data", {}).get("device")
    if not isinstance(device, dict):
        raise ApplicationError("Render data has no device", non_retryable=True)
    interfaces = device.get("interfaces")
    if not isinstance(interfaces, list):
        raise ApplicationError("Render data has no interfaces", non_retryable=True)
    interface = next(
        (
            item
            for item in interfaces
            if isinstance(item, dict) and item.get("name") == interface_name
        ),
        None,
    )
    if interface is None:
        raise ApplicationError(
            f"Interface {interface_name} missing from render data",
            non_retryable=True,
        )
    interface["status"] = {"name": status}
    interface["isis"] = {
        "metric": isis_metric,
        "level": "2",
        "passive": False,
    }
    return proposed_data


def _render_proposed_interface_intent(
    activity_input: ProposedInterfaceRenderInput,
) -> ProposedInterfaceRenderOutput:
    """Render an in-memory Nautobot interface proposal through the template library."""
    nb = pynautobot_client()
    renderer = Renderer(nb.base_url.replace("/api", ""), nb.token)
    device_data, location_data, plugin_data = renderer.load_data(device_id=activity_input.device_id)
    proposed_data = _override_interface_render_data(
        device_data,
        activity_input.interface_name,
        activity_input.status,
        activity_input.isis_metric,
    )
    rendered_files = {
        entrypoint.split("/")[-1].removesuffix(".j2"): renderer.render(
            entrypoint,
            proposed_data,
            location_data,
            plugin_data,
        )
        for entrypoint in renderer.list_entrypoints(proposed_data)
    }
    configuration = rendered_files.get(activity_input.filename)
    if configuration is None:
        raise ApplicationError(
            f"Proposed render produced no {activity_input.filename}",
            non_retryable=True,
        )
    return ProposedInterfaceRenderOutput(
        configuration=configuration,
        filename=activity_input.filename,
    )


@activity.defn
async def render_proposed_interface_intent(
    activity_input: ProposedInterfaceRenderInput,
) -> ProposedInterfaceRenderOutput:
    """Render proposed interface intent without persisting it."""
    return await asyncio.to_thread(_render_proposed_interface_intent, activity_input)


@activity.defn
def perform_drain_candidate_diff(activity_input: DrainCandidateInput) -> DrainCandidateOutput:
    """Diff the interfaces entrypoint through NETCONF or the focused local mock."""
    connection = NetworkConnection.from_device_data(activity_input.device_data)
    if isinstance(connection, MockNetworkConnection):
        return DrainCandidateOutput(
            diff=_mock_drain_diff(
                activity_input.interface_name,
                activity_input.current_metric,
            ),
            mocked=True,
        )
    return DrainCandidateOutput(
        diff=connection.perform_candidate_diff(activity_input.configuration, partial=True),
        mocked=False,
    )


@activity.defn
def apply_drain_candidate(activity_input: DrainApplyInput) -> DrainApplyOutput:
    """Guard and apply the interfaces entrypoint through the selected device backend."""
    connection = NetworkConnection.from_device_data(activity_input.device_data)
    if isinstance(connection, MockNetworkConnection):
        current_diff = _mock_drain_diff(
            activity_input.interface_name,
            activity_input.current_metric,
        )
        if current_diff != activity_input.approved_diff:
            raise DiffChangedException(
                f"Candidate diff for {activity_input.device_data.name} changed since approval"
            )
        return DrainApplyOutput(mocked=True)
    connection.commit_candidate_config(
        activity_input.configuration,
        activity_input.approved_diff,
        partial=True,
        commit_confirm=True,
    )
    return DrainApplyOutput(mocked=False)


@activity.defn
async def mock_apply_candidate(activity_input: MockDiffInput) -> str:
    """Simulate applying an already-approved candidate configuration."""
    endpoints = activity_input.device
    if activity_input.remote_device:
        endpoints += f" and {activity_input.remote_device}"
    return f"MOCK DEVICE: committed {activity_input.phase} candidate on {endpoints}."


@activity.defn
async def mock_validate_applied_intent(
    activity_input: MockAppliedIntentInput,
) -> MockAppliedIntentOutput:
    """Return realistic post-deployment observations without a device connection."""
    if activity_input.phase == "drain":
        observations = [
            f"{activity_input.lag_name}.0 IS-IS level 2 metric {activity_input.igp_metric}"
        ]
    elif activity_input.phase == "physical":
        observations = [
            f"{port} is up and a member of {activity_input.lag_name}"
            for port in activity_input.member_ports
        ]
    elif activity_input.phase == "addressing":
        observations = [
            f"{activity_input.lag_name}.0 has {activity_input.ipv4_address}",
            f"{activity_input.lag_name}.0 has {activity_input.ipv6_address}",
        ]
    elif activity_input.phase == "routing":
        observations = [
            f"{activity_input.lag_name}.0 IS-IS metric {activity_input.igp_metric}",
            f"{activity_input.lag_name}.0 enabled for MPLS and RSVP",
        ]
    else:
        raise ApplicationError(
            f"Unknown mock validation phase {activity_input.phase!r}", non_retryable=True
        )
    return MockAppliedIntentOutput(observations=observations)


@activity.defn
async def mock_validate_neighbor(activity_input: MockNeighborInput) -> MockNeighborOutput:
    """Simulate LLDP neighbor observation for the physical stage."""
    return MockNeighborOutput(
        matched=True,
        observed_neighbor=activity_input.expected_neighbor,
    )


@activity.defn
async def mock_ping_rtt(activity_input: MockPingInput) -> MockPingOutput:
    """Simulate lossless ping with RTT below the requested ceiling."""
    observed = round(max(0.1, activity_input.expected_rtt_ms * 0.8), 2)
    return MockPingOutput(
        transmitted=5,
        received=5,
        average_rtt_ms=observed,
        healthy=observed <= activity_input.expected_rtt_ms,
    )


@activity.defn
async def mock_validate_routing(activity_input: MockRoutingInput) -> MockRoutingOutput:
    """Simulate healthy routing protocol state after activation."""
    return MockRoutingOutput(
        igp_state="Up",
        mpls_state="Up",
        rsvp_state="Up",
        ibgp_reachability=f"Reachable via IS-IS to {activity_input.remote_device}",
    )


def _device_pop(device_name: str) -> str:
    return device_name.split("-", 1)[0].upper()


async def _lag_exists(client: NautobotClient, device_id: str, lag_name: str) -> bool:
    payload = await client.get(
        "dcim/interfaces/",
        params={"device": device_id, "name": lag_name},
    )
    return bool(_results(payload))


async def _backbone_router_names(client: NautobotClient) -> list[str]:
    roles = await client.get_all("extras/roles/", params={"name": BACKBONE_DEVICE_ROLE})
    if len(roles) != 1:
        return []
    devices = await client.get_all(
        "dcim/devices/",
        params={"role": roles[0]["id"], "depth": 0},
    )
    return sorted(str(device["name"]) for device in devices if device.get("name"))


def _expand_related_devices(
    routers: list[str],
    related: list[RelatedIgpChange],
    endpoints: set[str],
) -> list[str]:
    pops = {change.local_pop.upper() for change in related} | {
        change.remote_pop.upper() for change in related
    }
    extra = [name for name in routers if _device_pop(name) in pops and name not in endpoints]
    return extra


def _termination_xconnect(terminations: list[dict[str, Any]]) -> str | None:
    for termination in terminations:
        xconnect = termination.get("xconnect_id")
        if xconnect:
            return str(xconnect)
    return None


def _endpoint_turnup_diff(
    device: str,
    lag: str,
    remote_device: str,
    remote_lag: str,
    port: str,
    remote_port: str,
    ipv4: str,
    metric: int,
    macsec: bool,
    circuit_id: str,
    xconnect: str | None,
    minimum_links: int,
) -> str:
    xconnect_part = f":{xconnect}" if xconnect else ""
    lines = [
        f"[{device} edit interfaces {port}]",
        f'+   description "WAN:{lag}:{remote_device}:{remote_port}:{circuit_id}{xconnect_part}";',
        f"+   ether-options 802.3ad {lag};",
        f"[{device} edit interfaces {lag}]",
        f'+   description "WAN:{remote_device}:{remote_lag}";',
        "+   apply-groups BB-MACSEC-INT;" if macsec else "+   apply-groups BB-INT;",
        f"+   aggregated-ether-options minimum-links {minimum_links};",
        f"+   unit 0 family inet address {ipv4};",
        f"[{device} edit protocols isis interface {lag}.0]",
        "+   point-to-point;",
        f"+   level 2 metric {metric};",
        f"[{device} edit protocols mpls]",
        f"+   interface {lag}.0;",
        f"[{device} edit protocols rsvp]",
        f"+   interface {lag}.0;",
        f"[{device} edit protocols sflow]",
        f"+   interfaces {port}.0;",
    ]
    if macsec:
        ca = f"MACSEC-{_device_pop(device)}-{_device_pop(remote_device)}"
        lines.extend(
            [
                f"[{device} edit security macsec connectivity-association {ca}]",
                "+   cipher-suite gcm-aes-xpn-256;",
                "+   security-mode static-cak;",
                "+   mka should-secure;",
                "+   pre-shared-key ckn <REDACTED>;",
                "+   pre-shared-key cak <REDACTED>;",
                f"[{device} edit security macsec interfaces {port}]",
                f"+   connectivity-association {ca};",
            ]
        )
    return "\n".join(lines)


def _related_metric_diff(device: str, related: list[RelatedIgpChange]) -> str:
    pop = _device_pop(device)
    matching = [
        change for change in related if pop in {change.local_pop.upper(), change.remote_pop.upper()}
    ]
    if not matching:
        return ""
    lines = [f"[{device} edit protocols isis]"]
    for change in matching:
        pair = f"{change.local_pop}-{change.remote_pop}".lower()
        lines.append(f"+   /* POP pair {pair} interfaces */ level 2 metric {change.metric};")
    return "\n".join(lines)


@activity.defn
async def resolve_circuit_turnup_intent(
    activity_input: CircuitTurnupLookupInput,
) -> CircuitTurnupIntent:
    """Resolve circuit, ports, and POP-pair fan-out without writing Nautobot."""
    local_ipv4, remote_ipv4 = _point_to_point_addresses(activity_input.ipv4_prefix)
    notes: list[str] = []
    client = NautobotClient()
    async with client:
        local_device = await _resolve_device(client, activity_input.local_device)
        remote_device = await _resolve_device(client, activity_input.remote_device)
        for port in activity_input.local_ports:
            await _lookup_one(
                client,
                "dcim/interfaces/",
                {"device": local_device["id"], "name": port},
                f"Interface {local_device['name']}:{port}",
            )
        for port in activity_input.remote_ports:
            await _lookup_one(
                client,
                "dcim/interfaces/",
                {"device": remote_device["id"], "name": port},
                f"Interface {remote_device['name']}:{port}",
            )
        if await _lag_exists(client, str(local_device["id"]), activity_input.local_lag):
            raise ApplicationError(
                f"LAG {activity_input.local_lag} already exists on {local_device['name']}",
                non_retryable=True,
            )
        if await _lag_exists(client, str(remote_device["id"]), activity_input.remote_lag):
            raise ApplicationError(
                f"LAG {activity_input.remote_lag} already exists on {remote_device['name']}",
                non_retryable=True,
            )
        circuit = await _lookup_one(
            client,
            "circuits/circuits/",
            {"cid": activity_input.circuit_id, "depth": 2},
            f"Circuit {activity_input.circuit_id!r}",
        )
        terminations = _results(
            await client.get(
                "circuits/circuit-terminations/",
                params={"circuit": circuit["id"], "depth": 1},
            )
        )
        routers = await _backbone_router_names(client)

    xconnect = _termination_xconnect(terminations)
    if not xconnect:
        notes.append(
            "Circuit terminations have no xconnect_id; confirm the circuit reference out of band."
        )
    local_name = str(local_device["name"])
    remote_name = str(remote_device["name"])
    extra = _expand_related_devices(
        routers,
        activity_input.related_metrics,
        {local_name, remote_name},
    )
    if activity_input.related_metrics and not extra:
        notes.append(
            "No extra Backbone routers matched related POP pairs in Nautobot; "
            "metric fan-out is limited to the circuit endpoints."
        )
    impacted = [local_name, remote_name, *extra]
    return CircuitTurnupIntent(
        circuit_id=str(circuit["cid"]),
        circuit_uuid=str(circuit["id"]),
        local=CircuitTurnupEndpoint(
            device_id=str(local_device["id"]),
            device_name=local_name,
            lag_name=activity_input.local_lag,
            ports=list(activity_input.local_ports),
            ipv4_address=local_ipv4,
        ),
        remote=CircuitTurnupEndpoint(
            device_id=str(remote_device["id"]),
            device_name=remote_name,
            lag_name=activity_input.remote_lag,
            ports=list(activity_input.remote_ports),
            ipv4_address=remote_ipv4,
        ),
        ipv4_prefix=activity_input.ipv4_prefix,
        minimum_links=activity_input.minimum_links,
        path_metric=activity_input.path_metric,
        macsec=activity_input.macsec,
        xconnect=xconnect,
        related_metrics=activity_input.related_metrics,
        impacted_devices=impacted,
        preflight_notes=notes,
    )


@activity.defn
async def build_circuit_turnup_plan(
    intent: CircuitTurnupIntent,
) -> CircuitTurnupPlanOutput:
    """Build Junos-style candidates without rendering or touching Config Store."""
    local_port = intent.local.ports[0]
    remote_port = intent.remote.ports[0]
    devices = [
        DevicePlanDiff(
            device_name=intent.local.device_name,
            diff=_endpoint_turnup_diff(
                intent.local.device_name,
                intent.local.lag_name,
                intent.remote.device_name,
                intent.remote.lag_name,
                local_port,
                remote_port,
                intent.local.ipv4_address,
                intent.path_metric,
                intent.macsec,
                intent.circuit_id,
                intent.xconnect,
                intent.minimum_links,
            ),
        ),
        DevicePlanDiff(
            device_name=intent.remote.device_name,
            diff=_endpoint_turnup_diff(
                intent.remote.device_name,
                intent.remote.lag_name,
                intent.local.device_name,
                intent.local.lag_name,
                remote_port,
                local_port,
                intent.remote.ipv4_address,
                intent.path_metric,
                intent.macsec,
                intent.circuit_id,
                intent.xconnect,
                intent.minimum_links,
            ),
        ),
    ]
    endpoints = {intent.local.device_name, intent.remote.device_name}
    for device_name in intent.impacted_devices:
        if device_name in endpoints:
            continue
        related_diff = _related_metric_diff(device_name, intent.related_metrics)
        if related_diff:
            devices.append(DevicePlanDiff(device_name=device_name, diff=related_diff))
    combined = "\n\n".join(f"# {item.device_name}\n{item.diff}" for item in devices)
    return CircuitTurnupPlanOutput(devices=devices, combined_diff=combined)


@activity.defn
async def persist_circuit_turnup_intent(
    intent: CircuitTurnupIntent,
) -> CircuitTurnupPersistOutput:
    """Describe intended Nautobot writes without persisting them."""
    mutations = [
        ProposedNautobotMutation(
            model="dcim.interface",
            summary=(
                f"Create {intent.local.device_name}:{intent.local.lag_name} and "
                f"{intent.remote.device_name}:{intent.remote.lag_name}; bind members"
            ),
        ),
        ProposedNautobotMutation(
            model="ipam.ipaddress",
            summary=f"Assign {intent.ipv4_prefix} to the new LAG units",
        ),
        ProposedNautobotMutation(
            model="plugins.routing.isisinterface",
            summary=f"Set new LAG IS-IS metric {intent.path_metric}",
        ),
        ProposedNautobotMutation(
            model="circuits.circuit",
            summary=f"Attach {intent.circuit_id} terminations to the new LAGs",
        ),
    ]
    if intent.macsec:
        mutations.append(
            ProposedNautobotMutation(
                model="plugins.routing.macsecassociation",
                summary="Bind MACsec CA to physical members; CKN/CAK stay in Vault",
            )
        )
    for change in intent.related_metrics:
        pair = f"{change.local_pop}-{change.remote_pop}".lower()
        mutations.append(
            ProposedNautobotMutation(
                model="plugins.routing.isisinterface",
                summary=f"Retune POP pair {pair} to metric {change.metric}",
            )
        )
    return CircuitTurnupPersistOutput(mutations=mutations, written=False)


@activity.defn
async def mock_validate_circuit_turnup(
    intent: CircuitTurnupIntent,
) -> CircuitTurnupValidateOutput:
    """Simulate the GNI post-change shows."""
    local_lag = intent.local.lag_name
    remote_lag = intent.remote.lag_name
    checks = [
        f"show interfaces {local_lag} / {remote_lag} - members and LACP up",
        "show security macsec connections - secured on both ends"
        if intent.macsec
        else "MACsec skipped",
        f"show isis adjacency - Up on {local_lag}.0 / {remote_lag}.0",
        "show mpls lsp — BASE/HIPRI/LOPRI mesh still present",
        "show route protocol isis — new path usable",
    ]
    return CircuitTurnupValidateOutput(checks=checks, healthy=True)


def _nested_id(value: Any) -> str | None:
    if isinstance(value, dict) and value.get("id"):
        return str(value["id"])
    if isinstance(value, str) and value:
        return value
    return None


def _interface_type(interface: dict[str, Any]) -> str:
    type_value = interface.get("type")
    if isinstance(type_value, dict):
        return str(type_value.get("value") or type_value.get("name") or "").casefold()
    return str(type_value or "").casefold()


def _is_eligible_member(interface: dict[str, Any]) -> bool:
    name = str(interface.get("name") or "")
    if not name.startswith("et-") or ":" in name:
        return False
    if _interface_type(interface) in {"lag", "virtual"}:
        return False
    if interface.get("lag"):
        return False
    if str(interface.get("description") or "").strip():
        return False
    return True


async def _next_member_port(
    client: NautobotClient, device_id: str, device_name: str
) -> dict[str, Any]:
    interfaces = await client.get_all("dcim/interfaces/", params={"device": device_id, "depth": 1})
    eligible = sorted(
        (interface for interface in interfaces if _is_eligible_member(interface)),
        key=lambda interface: str(interface["name"]),
    )
    if not eligible:
        raise ApplicationError(
            f"No unused et- member port on {device_name}",
            non_retryable=True,
        )
    return eligible[0]


async def _next_lag_name(client: NautobotClient, device_id: str) -> str:
    interfaces = await client.get_all("dcim/interfaces/", params={"device": device_id})
    used = {str(interface["name"]).casefold() for interface in interfaces}
    sequence = LAG_SEQUENCE_START
    while f"ae{sequence}" in used:
        sequence += 1
    return f"ae{sequence}"


def _reservation_cid(jira: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9-]", "-", jira).strip("-")
    return f"BB-RESERVE-{slug}"


def _reservation_comments(intent: CircuitTurnupIntent) -> str:
    payload = {
        RESERVATION_COMMENT_KEY: {
            "circuit_id": intent.circuit_id,
            "local_device": intent.local.device_name,
            "local_lag": intent.local.lag_name,
            "local_ports": intent.local.ports,
            "local_ipv4": intent.local.ipv4_address,
            "remote_device": intent.remote.device_name,
            "remote_lag": intent.remote.lag_name,
            "remote_ports": intent.remote.ports,
            "remote_ipv4": intent.remote.ipv4_address,
            "ipv4_prefix": intent.ipv4_prefix,
            "path_metric": intent.path_metric,
            "macsec": intent.macsec,
            "minimum_links": intent.minimum_links,
        }
    }
    return json.dumps(payload)


def _parse_reservation_comments(comments: str | None) -> dict[str, Any]:
    try:
        payload = json.loads(comments or "")
    except json.JSONDecodeError as error:
        raise ApplicationError(
            "Circuit comments do not contain a reservation payload",
            non_retryable=True,
        ) from error
    reservation = payload.get(RESERVATION_COMMENT_KEY) if isinstance(payload, dict) else None
    if not isinstance(reservation, dict):
        raise ApplicationError(
            "Circuit comments do not contain a reservation payload",
            non_retryable=True,
        )
    return reservation


def _member_description(lag: str, remote: str, remote_port: str, circuit_id: str) -> str:
    return f"WAN:{lag}:{remote}:{remote_port}:Reserved:{circuit_id}"


def _lag_description(remote: str, remote_lag: str, circuit_id: str) -> str:
    return f"WAN:{remote}:{remote_lag}:Reserved:{circuit_id}"


@activity.defn
async def plan_circuit_reservation(
    activity_input: CircuitReservationLookupInput,
) -> CircuitReservationPlan:
    """Pick unused ports, LAGs, and a /31 without writing Nautobot."""
    if activity_input.local_device == activity_input.remote_device:
        raise ApplicationError("local and remote devices must be distinct", non_retryable=True)
    client = NautobotClient()
    async with client:
        local_device = await _resolve_device(client, activity_input.local_device)
        remote_device = await _resolve_device(client, activity_input.remote_device)
        local_device = await client.get(f"dcim/devices/{local_device['id']}/", params={"depth": 1})
        remote_device = await client.get(
            f"dcim/devices/{remote_device['id']}/", params={"depth": 1}
        )
        local_port = await _next_member_port(
            client, str(local_device["id"]), str(local_device["name"])
        )
        remote_port = await _next_member_port(
            client, str(remote_device["id"]), str(remote_device["name"])
        )
        local_lag = await _next_lag_name(client, str(local_device["id"]))
        remote_lag = await _next_lag_name(client, str(remote_device["id"]))
        prefix, parent = await client.get_next_available_prefix("BB-P2P", 31)
        create_circuit = not activity_input.circuit_id
        if activity_input.circuit_id:
            circuit = await _lookup_one(
                client,
                "circuits/circuits/",
                {"cid": activity_input.circuit_id, "depth": 1},
                f"Circuit {activity_input.circuit_id!r}",
            )
            if _status_label(circuit) != PLANNED_STATUS:
                raise ApplicationError(
                    f"Circuit {activity_input.circuit_id} is {_status_label(circuit)}, not Planned",
                    non_retryable=True,
                )
            circuit_id = str(circuit["cid"])
            circuit_uuid = str(circuit["id"])
        else:
            circuit_id = _reservation_cid(activity_input.jira)
            existing = _results(await client.get("circuits/circuits/", params={"cid": circuit_id}))
            if existing:
                raise ApplicationError(
                    f"Circuit {circuit_id} already exists; choose a different Jira key",
                    non_retryable=True,
                )
            circuit_uuid = ""
        local_ipv4, remote_ipv4 = _point_to_point_addresses(prefix)
        locations = {
            str(local_device["name"]): _nested_id(local_device.get("location")) or "",
            str(remote_device["name"]): _nested_id(remote_device.get("location")) or "",
        }
    intent = CircuitTurnupIntent(
        circuit_id=circuit_id,
        circuit_uuid=circuit_uuid,
        local=CircuitTurnupEndpoint(
            device_id=str(local_device["id"]),
            device_name=str(local_device["name"]),
            lag_name=local_lag,
            ports=[str(local_port["name"])],
            ipv4_address=local_ipv4,
        ),
        remote=CircuitTurnupEndpoint(
            device_id=str(remote_device["id"]),
            device_name=str(remote_device["name"]),
            lag_name=remote_lag,
            ports=[str(remote_port["name"])],
            ipv4_address=remote_ipv4,
        ),
        ipv4_prefix=prefix,
        minimum_links=activity_input.minimum_links,
        path_metric=activity_input.path_metric,
        macsec=activity_input.macsec,
        xconnect=None,
        related_metrics=[],
        impacted_devices=[str(local_device["name"]), str(remote_device["name"])],
        preflight_notes=[],
    )
    return CircuitReservationPlan(
        intent=intent,
        create_circuit=create_circuit,
        parent_prefix=parent,
        location_ids={name: loc for name, loc in locations.items() if loc},
    )


async def _create_planned_lag(
    client: NautobotClient,
    device_id: str,
    lag_name: str,
    description: str,
    minimum_links: int,
    status_id: str,
) -> str:
    lag = await client.post(
        "dcim/interfaces/",
        data={
            "device": device_id,
            "name": lag_name,
            "type": "lag",
            "status": status_id,
            "enabled": True,
            "description": description,
            "custom_fields": {"bb_min_links": minimum_links},
        },
    )
    return str(lag["id"])


@activity.defn
async def persist_circuit_reservation(
    plan: CircuitReservationPlan,
) -> CircuitReservationPersistOutput:
    """Create Planned LAG, IP, circuit, and member bindings in Nautobot."""
    intent = plan.intent
    mutations: list[ProposedNautobotMutation] = []
    client = NautobotClient()
    async with client:
        status_id = await _resolve_status(client, PLANNED_STATUS)
        if plan.create_circuit:
            provider = await _lookup_one(
                client, "circuits/providers/", {"name": SANDBOX_PROVIDER}, SANDBOX_PROVIDER
            )
            circuit_type = await _lookup_one(
                client,
                "circuits/circuit-types/",
                {"name": SANDBOX_CIRCUIT_TYPE},
                SANDBOX_CIRCUIT_TYPE,
            )
            circuit = await client.post(
                "circuits/circuits/",
                data={
                    "cid": intent.circuit_id,
                    "provider": provider["id"],
                    "circuit_type": circuit_type["id"],
                    "status": status_id,
                    "description": (
                        f"Reserved {intent.local.device_name}:{intent.local.lag_name} to "
                        f"{intent.remote.device_name}:{intent.remote.lag_name}"
                    ),
                    "comments": _reservation_comments(intent),
                },
            )
            mutations.append(
                ProposedNautobotMutation(
                    model="circuits.circuit",
                    summary=f"Created {intent.circuit_id} as Planned",
                )
            )
        else:
            circuit = await client.get(f"circuits/circuits/{intent.circuit_uuid}/")
            await client.patch(
                f"circuits/circuits/{intent.circuit_uuid}/",
                data={"comments": _reservation_comments(intent), "status": status_id},
            )
            mutations.append(
                ProposedNautobotMutation(
                    model="circuits.circuit",
                    summary=f"Attached reservation to {intent.circuit_id}",
                )
            )
        circuit_uuid = str(circuit["id"])
        local_lag_id = await _create_planned_lag(
            client,
            intent.local.device_id,
            intent.local.lag_name,
            _lag_description(intent.remote.device_name, intent.remote.lag_name, intent.circuit_id),
            intent.minimum_links,
            status_id,
        )
        remote_lag_id = await _create_planned_lag(
            client,
            intent.remote.device_id,
            intent.remote.lag_name,
            _lag_description(intent.local.device_name, intent.local.lag_name, intent.circuit_id),
            intent.minimum_links,
            status_id,
        )
        local_port = await _lookup_one(
            client,
            "dcim/interfaces/",
            {"device": intent.local.device_id, "name": intent.local.ports[0]},
            f"Interface {intent.local.device_name}:{intent.local.ports[0]}",
        )
        remote_port = await _lookup_one(
            client,
            "dcim/interfaces/",
            {"device": intent.remote.device_id, "name": intent.remote.ports[0]},
            f"Interface {intent.remote.device_name}:{intent.remote.ports[0]}",
        )
        await client.patch(
            f"dcim/interfaces/{local_port['id']}/",
            data={
                "status": status_id,
                "lag": local_lag_id,
                "description": _member_description(
                    intent.local.lag_name,
                    intent.remote.device_name,
                    intent.remote.ports[0],
                    intent.circuit_id,
                ),
            },
        )
        await client.patch(
            f"dcim/interfaces/{remote_port['id']}/",
            data={
                "status": status_id,
                "lag": remote_lag_id,
                "description": _member_description(
                    intent.remote.lag_name,
                    intent.local.device_name,
                    intent.local.ports[0],
                    intent.circuit_id,
                ),
            },
        )
        mutations.append(
            ProposedNautobotMutation(
                model="dcim.interface",
                summary=(
                    f"Created {intent.local.lag_name}/{intent.remote.lag_name} and bound "
                    f"{intent.local.ports[0]}/{intent.remote.ports[0]} as Planned"
                ),
            )
        )
        namespace = await _lookup_one(
            client, "ipam/namespaces/", {"name": "Global"}, "Global namespace"
        )
        ensured: set[str] = set()
        await _ensure_ip_assignment(
            client,
            intent.local.ipv4_address,
            local_lag_id,
            str(namespace["id"]),
            status_id,
            ensured,
        )
        await _ensure_ip_assignment(
            client,
            intent.remote.ipv4_address,
            remote_lag_id,
            str(namespace["id"]),
            status_id,
            ensured,
        )
        mutations.append(
            ProposedNautobotMutation(
                model="ipam.ipaddress",
                summary=f"Assigned {intent.ipv4_prefix} as Planned",
            )
        )
        await _upsert_isis_interface(client, local_lag_id, intent.path_metric)
        await _upsert_isis_interface(client, remote_lag_id, intent.path_metric)
        mutations.append(
            ProposedNautobotMutation(
                model="plugins.routing.isisinterface",
                summary=f"Set Planned IS-IS metric {intent.path_metric}",
            )
        )
        local_location = plan.location_ids.get(intent.local.device_name)
        remote_location = plan.location_ids.get(intent.remote.device_name)
        if local_location and remote_location:
            await client.post(
                "circuits/circuit-terminations/",
                data={
                    "circuit": circuit_uuid,
                    "term_side": "A",
                    "location": local_location,
                },
            )
            await client.post(
                "circuits/circuit-terminations/",
                data={
                    "circuit": circuit_uuid,
                    "term_side": "Z",
                    "location": remote_location,
                },
            )
            mutations.append(
                ProposedNautobotMutation(
                    model="circuits.circuittermination",
                    summary="Created A/Z terminations at the endpoint locations",
                )
            )
    return CircuitReservationPersistOutput(
        circuit_uuid=circuit_uuid,
        written=True,
        mutations=mutations,
    )


@activity.defn
async def resolve_planned_circuit(circuit_id: str) -> PlannedCircuitObjects:
    """Load a Planned reservation written by the reserve workflow."""
    client = NautobotClient()
    async with client:
        circuit = await _lookup_one(
            client,
            "circuits/circuits/",
            {"cid": circuit_id, "depth": 1},
            f"Circuit {circuit_id!r}",
        )
        if _status_label(circuit) != PLANNED_STATUS:
            raise ApplicationError(
                f"Circuit {circuit_id} is {_status_label(circuit)}, not Planned",
                non_retryable=True,
            )
        reservation = _parse_reservation_comments(circuit.get("comments"))
        local_device = await _resolve_device(client, str(reservation["local_device"]))
        remote_device = await _resolve_device(client, str(reservation["remote_device"]))
        local_lag = await _lookup_one(
            client,
            "dcim/interfaces/",
            {"device": local_device["id"], "name": reservation["local_lag"], "depth": 1},
            f"Interface {local_device['name']}:{reservation['local_lag']}",
        )
        remote_lag = await _lookup_one(
            client,
            "dcim/interfaces/",
            {"device": remote_device["id"], "name": reservation["remote_lag"], "depth": 1},
            f"Interface {remote_device['name']}:{reservation['remote_lag']}",
        )
        member_ids: list[str] = []
        for device, ports in (
            (local_device, reservation["local_ports"]),
            (remote_device, reservation["remote_ports"]),
        ):
            for port in ports:
                member = await _lookup_one(
                    client,
                    "dcim/interfaces/",
                    {"device": device["id"], "name": port, "depth": 1},
                    f"Interface {device['name']}:{port}",
                )
                if _status_label(member) != PLANNED_STATUS:
                    raise ApplicationError(
                        f"{device['name']}:{port} is {_status_label(member)}, not Planned",
                        non_retryable=True,
                    )
                member_ids.append(str(member["id"]))
        for lag, label in ((local_lag, "local LAG"), (remote_lag, "remote LAG")):
            if _status_label(lag) != PLANNED_STATUS:
                raise ApplicationError(
                    f"{label} {lag.get('name')} is {_status_label(lag)}, not Planned",
                    non_retryable=True,
                )
        prefix = str(reservation["ipv4_prefix"])
        prefix_rows = _results(await client.get("ipam/prefixes/", params={"prefix": prefix}))
        ip_rows = []
        for address in (reservation["local_ipv4"], reservation["remote_ipv4"]):
            ip_rows.extend(
                _results(await client.get("ipam/ip-addresses/", params={"address": address}))
            )
    intent = CircuitTurnupIntent(
        circuit_id=str(circuit["cid"]),
        circuit_uuid=str(circuit["id"]),
        local=CircuitTurnupEndpoint(
            device_id=str(local_device["id"]),
            device_name=str(local_device["name"]),
            lag_name=str(reservation["local_lag"]),
            ports=list(reservation["local_ports"]),
            ipv4_address=str(reservation["local_ipv4"]),
        ),
        remote=CircuitTurnupEndpoint(
            device_id=str(remote_device["id"]),
            device_name=str(remote_device["name"]),
            lag_name=str(reservation["remote_lag"]),
            ports=list(reservation["remote_ports"]),
            ipv4_address=str(reservation["remote_ipv4"]),
        ),
        ipv4_prefix=prefix,
        minimum_links=int(reservation.get("minimum_links") or 1),
        path_metric=int(reservation["path_metric"]),
        macsec=bool(reservation.get("macsec", True)),
        xconnect=None,
        related_metrics=[],
        impacted_devices=[str(local_device["name"]), str(remote_device["name"])],
        preflight_notes=[],
    )
    return PlannedCircuitObjects(
        intent=intent,
        circuit_uuid=str(circuit["id"]),
        interface_ids=[str(local_lag["id"]), str(remote_lag["id"]), *member_ids],
        ip_ids=[str(row["id"]) for row in ip_rows],
        prefix_ids=[str(row["id"]) for row in prefix_rows],
    )


@activity.defn
def check_activation_device_state(
    activity_input: ActivationDeviceCheckInput,
) -> ActivationDeviceCheckOutput:
    """Fail activate when the reserved LAG, member, or address is already on the box."""
    connection = NetworkConnection.from_device_data(activity_input.device_data)
    if isinstance(connection, MockNetworkConnection):
        return ActivationDeviceCheckOutput.from_mock()
    diff = connection.perform_candidate_diff(
        _activation_probe_config(
            activity_input.lag_name,
            activity_input.member_port,
            activity_input.ipv4_address,
        ),
        partial=True,
    )
    return ActivationDeviceCheckOutput.from_candidate_diff(
        activity_input.device_data.name,
        activity_input.lag_name,
        activity_input.member_port,
        activity_input.ipv4_address,
        diff,
    )


@activity.defn
async def activate_planned_circuit(objects: PlannedCircuitObjects) -> NautobotMutationOutput:
    """Flip reserved objects from Planned to Active."""
    client = NautobotClient()
    async with client:
        status_id = await _resolve_status(client, ACTIVE_STATUS)
        await client.patch(
            f"circuits/circuits/{objects.circuit_uuid}/",
            data={"status": status_id},
        )
        for interface_id in objects.interface_ids:
            await client.patch(
                f"dcim/interfaces/{interface_id}/",
                data={"status": status_id},
            )
        for ip_id in objects.ip_ids:
            await client.patch(f"ipam/ip-addresses/{ip_id}/", data={"status": status_id})
        for prefix_id in objects.prefix_ids:
            await client.patch(f"ipam/prefixes/{prefix_id}/", data={"status": status_id})
    updated = [objects.circuit_uuid, *objects.interface_ids, *objects.ip_ids, *objects.prefix_ids]
    return NautobotMutationOutput(updated_ids=updated, status=ACTIVE_STATUS)


REGISTERED_ACTIVITIES = [
    resolve_drain_intent,
    set_interface_status,
    resolve_internal_backbone_intent,
    enable_backbone_interfaces,
    apply_backbone_addressing,
    activate_backbone_routing,
    build_mock_candidate_diff,
    load_render_revision_diff,
    render_proposed_interface_intent,
    perform_drain_candidate_diff,
    apply_drain_candidate,
    mock_apply_candidate,
    mock_validate_applied_intent,
    mock_validate_neighbor,
    mock_ping_rtt,
    mock_validate_routing,
    resolve_circuit_turnup_intent,
    build_circuit_turnup_plan,
    persist_circuit_turnup_intent,
    mock_validate_circuit_turnup,
    plan_circuit_reservation,
    persist_circuit_reservation,
    resolve_planned_circuit,
    check_activation_device_state,
    activate_planned_circuit,
]
