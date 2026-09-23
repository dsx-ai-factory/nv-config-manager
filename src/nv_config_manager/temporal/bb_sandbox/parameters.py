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
"""Parameter APIs used by the local Backbone sandbox workflow forms."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from temporalio.exceptions import ApplicationError

from nv_config_manager.temporal.client.nautobot import NautobotClient

BACKBONE_DEVICE_ROLE = "Backbone Router"
DEFAULT_P2P_PREFIX_ROLE = "BB-P2P"

router = APIRouter(prefix="/parameter/bb-sandbox", tags=["parameters"])


class BackboneDeviceParameter(BaseModel):
    """Backbone device choice."""

    id: str
    name: str


class BackboneCircuitParameter(BaseModel):
    """Circuit choice."""

    id: str
    cid: str
    status: str | None = None


class BackboneInterfaceParameter(BaseModel):
    """Interface choice with enough state for the form to explain filtering."""

    id: str
    name: str
    type: str | None = None
    status: str | None = None


class NextLagParameter(BaseModel):
    """Suggested LAG name for two devices."""

    lag_name: str


class NextPrefixParameter(BaseModel):
    """Suggested child prefix from a role-tagged allocation pool."""

    role: str
    prefix: str
    prefix_length: int
    parent_prefix: str


def _results(response: Any) -> list[dict[str, Any]]:
    if isinstance(response, dict):
        return list(response.get("results") or [])
    return list(response or [])


def _nested_name(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("name") or value.get("value")
    return str(value) if value else None


async def _role(client: NautobotClient, name: str) -> dict[str, Any]:
    roles = await client.get_all("extras/roles/", params={"name": name})
    if len(roles) != 1:
        raise HTTPException(
            status_code=404,
            detail=f"Expected exactly one Nautobot role named {name!r}; found {len(roles)}.",
        )
    return roles[0]


@router.get("/devices")
async def get_backbone_devices() -> list[BackboneDeviceParameter]:
    """Return devices carrying the sandbox Backbone Router role."""
    client = NautobotClient()
    try:
        async with client:
            role = await _role(client, BACKBONE_DEVICE_ROLE)
            devices = await client.get_all("dcim/devices/", params={"role": role["id"], "depth": 1})
    except ApplicationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return sorted(
        [
            BackboneDeviceParameter(id=str(device["id"]), name=str(device["name"]))
            for device in devices
            if device.get("name")
        ],
        key=lambda device: device.name.casefold(),
    )


@router.get("/circuits")
async def get_backbone_circuits(
    status: Annotated[str | None, Query()] = None,
) -> list[BackboneCircuitParameter]:
    """Return circuit IDs available in the Nautobot sandbox."""
    client = NautobotClient()
    try:
        async with client:
            circuits = await client.get_all("circuits/circuits/", params={"depth": 1})
    except ApplicationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    results = [
        BackboneCircuitParameter(
            id=str(circuit["id"]),
            cid=str(circuit["cid"]),
            status=_nested_name(circuit.get("status")),
        )
        for circuit in circuits
        if circuit.get("cid")
    ]
    if status:
        wanted = status.casefold()
        results = [circuit for circuit in results if (circuit.status or "").casefold() == wanted]
    return sorted(results, key=lambda circuit: circuit.cid.casefold())


@router.get("/devices/{device_id}/interfaces")
async def get_backbone_interfaces(
    device_id: str,
    purpose: Annotated[Literal["drain", "lag-member"], Query()] = "drain",
) -> list[BackboneInterfaceParameter]:
    """Return interfaces eligible for a drain or as new LAG members."""
    client = NautobotClient()
    try:
        async with client:
            interfaces = await client.get_all(
                "dcim/interfaces/", params={"device": device_id, "depth": 1}
            )
    except ApplicationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    if purpose == "lag-member":
        interfaces = [
            interface
            for interface in interfaces
            if _nested_name(interface.get("type")) != "LAG"
            and str(_nested_name(interface.get("type")) or "").casefold() != "lag"
            and not interface.get("lag")
        ]
    return sorted(
        [
            BackboneInterfaceParameter(
                id=str(interface["id"]),
                name=str(interface["name"]),
                type=_nested_name(interface.get("type")),
                status=_nested_name(interface.get("status")),
            )
            for interface in interfaces
            if interface.get("name")
        ],
        key=lambda interface: interface.name.casefold(),
    )


@router.get("/next-lag")
async def get_next_backbone_lag(
    local_device_id: str,
    remote_device_id: str,
) -> NextLagParameter:
    """Return the first ``aeN`` from 100 unused on both devices."""
    if local_device_id == remote_device_id:
        raise HTTPException(status_code=400, detail="Local and remote devices must be distinct.")
    client = NautobotClient()
    try:
        async with client:
            lag_name = await client.get_next_common_lag_name(local_device_id, remote_device_id)
    except ApplicationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return NextLagParameter(lag_name=lag_name)


@router.get("/next-prefix")
async def get_next_backbone_prefix(
    prefix_length: Annotated[int, Query()],
    role: str = DEFAULT_P2P_PREFIX_ROLE,
) -> NextPrefixParameter:
    """Return the next free child prefix from a role-tagged Nautobot pool."""
    if prefix_length not in (31, 127):
        raise HTTPException(status_code=422, detail="prefix_length must be 31 or 127.")
    client = NautobotClient()
    try:
        async with client:
            prefix, parent = await client.get_next_available_prefix(
                role_name=role,
                prefix_length=prefix_length,
            )
    except ApplicationError as error:
        status_code = (
            404 if "No available" in str(error) or "Expected exactly" in str(error) else 400
        )
        raise HTTPException(status_code=status_code, detail=str(error)) from error
    return NextPrefixParameter(
        role=role,
        prefix=prefix,
        prefix_length=prefix_length,
        parent_prefix=parent,
    )
