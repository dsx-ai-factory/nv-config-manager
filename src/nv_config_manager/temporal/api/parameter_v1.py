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
"""Parameter Routes for populating Workflow UI Forms."""

from collections.abc import Mapping
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.dcim import (
    DCIMDeviceSelectionFilter,
    create_dcim_client,
)
from nv_config_manager.dcim.errors import DCIMConflictError, DCIMInvalidDataError, DCIMNotFoundError
from nv_config_manager.temporal.common.mixins.device import Platform
from nv_config_manager.temporal.ngc.activities.diagnostics import get_available_commands

logger = get_logger(__name__, category=LogCategory.TEMPORAL_API)


class Device(BaseModel):
    """Device data for dropdown population."""

    id: str
    name: str
    platform: str | None = None


class DeviceInterface(BaseModel):
    """Device interface data for dropdown population."""

    id: str
    name: str


class Location(BaseModel):
    """Site data for dropdown population."""

    id: str
    name: str


class Secret(BaseModel):
    """Secret data for dropdown population."""

    name: str
    description: str | None = None


router = APIRouter(prefix="/parameter", tags=["parameters"])


@router.get("/site")
async def get_sites() -> list[Location]:
    """Return a list of NVIDIA Config Manager-managed sites."""
    client = create_dcim_client()
    async with client:
        sites = await client.list_locations(("Site",))

    return [Location(id=site.id, name=site.name) for site in sites]


@router.get("/location")
async def get_locations(
    location_type: Annotated[list[str] | None, Query()] = None,
) -> list[Location]:
    """Return a list of NVIDIA Config Manager-managed sites."""
    client = create_dcim_client()
    async with client:
        locations = await client.list_locations(tuple(location_type or ()))

    return [Location(id=location.id, name=location.name) for location in locations]


class Tenant(BaseModel):
    """Tenant data for dropdown population."""

    id: str
    name: str


class Role(BaseModel):
    """Role data for dropdown population."""

    id: str
    name: str


class Tag(BaseModel):
    """Tag data for dropdown population."""

    id: str
    name: str


class Overlay(BaseModel):
    """Overlay data for dropdown population."""

    id: str
    name: str


@router.get("/tenant")
async def get_tenants(
    managed_only: Annotated[
        bool, Query(description="Limit to tenants with managed devices")
    ] = False,
) -> list[Tenant]:
    """Return a list of tenants. Default: all. With managed_only=true: only those with managed devices."""
    client = create_dcim_client()
    async with client:
        tenants = await client.list_tenants(managed_only)
    return [Tenant(id=tenant.id, name=tenant.name) for tenant in tenants]


@router.get("/role")
async def get_roles(
    managed_only: Annotated[bool, Query(description="Limit to roles with managed devices")] = False,
) -> list[Role]:
    """Return a list of roles. Default: all. With managed_only=true: only those with managed devices."""
    client = create_dcim_client()
    async with client:
        roles = await client.list_roles(managed_only)
    return [Role(id=role.id, name=role.name) for role in roles]


@router.get("/namespace-tag")
async def get_namespace_tags(
    location: Annotated[
        str | None, Query(description="Limit to namespace tags at this location")
    ] = None,
) -> list[Tag]:
    """Return the configured DCIM provider's namespace tag choices."""
    client = create_dcim_client()

    try:
        async with client:
            tag_names = await client.list_namespace_tags(location)
    except DCIMInvalidDataError as exc:
        raise HTTPException(
            status_code=500,
            detail="Malformed DCIM namespace tag response.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Failed to query DCIM namespace tags.",
        ) from exc
    return [Tag(id=name, name=name) for name in tag_names]


@router.get("/overlay")
async def get_overlays(
    location: Annotated[str | None, Query(description="Limit to overlays at this location")] = None,
    isolation_type: Annotated[
        str | None, Query(description="Limit to overlays with this isolation type")
    ] = None,
) -> list[Overlay]:
    """Return overlays, optionally filtered by location and isolation type."""
    client = create_dcim_client()
    try:
        async with client:
            overlays = await client.list_overlays(location, isolation_type)
    except DCIMInvalidDataError as exc:
        raise HTTPException(
            status_code=500,
            detail="Malformed DCIM overlay response.",
        ) from exc
    except Exception as exc:
        logger.exception("Failed to query DCIM overlays", exc_info=exc)
        raise HTTPException(
            status_code=500,
            detail="Failed to query DCIM overlays.",
        ) from exc
    return [Overlay(id=overlay.id, name=overlay.name) for overlay in overlays]


class Status(BaseModel):
    """Status data for dropdown population."""

    id: str
    name: str


@router.get("/status")
async def get_statuses(
    content_type: Annotated[
        str | None,
        Query(description="Filter by content type (e.g. dcim.device, circuits.circuit)"),
    ] = None,
) -> list[Status]:
    """Return a list of statuses. Optional content_type filters to that object type."""
    client = create_dcim_client()
    async with client:
        statuses = await client.list_statuses(content_type)

    return [Status(id=status.id, name=status.name) for status in statuses]


@router.get("/device")
async def get_devices(  # pylint: disable=R0913,R0914
    site: Annotated[list[str] | None, Query()] = None,
    status: Annotated[list[str] | None, Query()] = None,
    role: Annotated[list[str] | None, Query()] = None,
    tenant: Annotated[list[str] | None, Query()] = None,
    device_type_id: Annotated[list[str] | None, Query()] = None,
    manufacturer: Annotated[list[str] | None, Query()] = None,
    platform: Annotated[list[str] | None, Query()] = None,
    managed_only: Annotated[
        bool, Query(description="Limit to NVIDIA Config Manager-managed devices")
    ] = False,
) -> list[Device]:
    """Return a list of filtered devices."""
    filters = DCIMDeviceSelectionFilter(
        sites=tuple(site or ()),
        statuses=tuple(status or ()),
        roles=tuple(role or ()),
        tenants=tuple(tenant or ()),
        device_type_ids=tuple(device_type_id or ()),
        manufacturers=tuple(manufacturer or ()),
        platforms=tuple(platform or ()),
        managed_only=managed_only,
    )
    if not any(
        (
            filters.sites,
            filters.statuses,
            filters.roles,
            filters.tenants,
            filters.device_type_ids,
            filters.manufacturers,
            filters.platforms,
            filters.managed_only,
        )
    ):
        raise HTTPException(status_code=400, detail="Must apply at least one filter.")

    client = create_dcim_client()
    try:
        async with client:
            devices = await client.list_devices(filters)
    except DCIMInvalidDataError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return [
        Device(
            id=device.id,
            name=device.name,
            platform=device.platform,
        )
        for device in devices
    ]


@router.get("/device/{device_id}/interfaces", responses={400: {"description": "Bad Request"}})
async def get_device_interfaces(device_id: str) -> list[DeviceInterface]:
    """Return the interfaces belonging to a device."""
    client = create_dcim_client()
    async with client:
        provider_interfaces = await client.get_device_interfaces(device_id)

    interfaces = [
        DeviceInterface(id=interface.id, name=interface.name)
        for interface in provider_interfaces
        if interface.name
    ]
    return sorted(interfaces, key=lambda interface: interface.name.casefold())


class CommandEntry(BaseModel):
    """A single command in the diagnostics catalog."""

    name: str
    description: str


@router.get("/diagnostics/commands")
async def get_diagnostics_commands(
    platform: Annotated[list[str] | None, Query()] = None,
) -> list[CommandEntry]:
    """Return the diagnostics command catalog for the requested platform(s).

    Pass one or more ``?platform=<slug>`` query params (e.g. ``cumulus-linux``).
    When no platform is given, commands from all platforms are returned.
    Commands that appear on multiple platforms are deduplicated by name.
    """
    if platform:
        platforms_to_check: list[Platform] = []
        for p in platform:
            try:
                platforms_to_check.append(Platform(p))
            except ValueError:
                pass
    else:
        platforms_to_check = list(Platform)

    seen: dict[str, str] = {}
    for plat in platforms_to_check:
        for name, description in get_available_commands(plat).items():
            if name not in seen:
                seen[name] = description

    return [CommandEntry(name=name, description=desc) for name, desc in sorted(seen.items())]


@router.get("/device/{device_id}/secrets")
async def get_device_secrets(device_id: str) -> list[Secret]:
    """Return a list of secrets available for a device.

    Args:
        device_id: The UUID of the device to fetch secrets from

    Returns:
        List of secrets returned by the configured DCIM provider
    """
    client = create_dcim_client()
    async with client:
        secret_versions = await client.get_device_secret_versions(device_id)

    secrets = []
    if isinstance(secret_versions, Mapping):
        for secret_name, version in secret_versions.items():
            # Format as "secret_name_version" (e.g., "tacacs_key_r1")
            formatted_name = f"{secret_name}_{version}"
            secrets.append(
                Secret(name=formatted_name, description=f"{secret_name} version {version}")
            )

    return secrets


@router.get(
    "/device/{device_id}/secret_types",
    summary="Get Secret Types for Device",
)
async def get_device_users_with_versions(device_id: str) -> list[str]:
    """Get available secret types from the configured DCIM provider.

    Args: device_id: The UUID of the device.

    Returns:
        List of secret types plus versions from config context.
    """
    client = create_dcim_client()
    async with client:
        secrets_versions = await client.get_device_secret_versions(device_id)

    secret_types = []
    for secret_type, version in secrets_versions.items():
        # Return formatted name like "tacacs_key_r1"
        secret_types.append(f"{secret_type}_{version}")

    if len(secret_types) == 0:
        raise HTTPException(
            status_code=400,
            detail=f"No secrets versions defined for device {device_id}.",
        )

    return secret_types


@router.get("/device/{device_id}/password_users")
async def get_device_password_users(device_id: str) -> list[Secret]:
    """Get available password users and their secret names for a device.

    Args:
        device_id: The UUID of the device.

    Returns:
        List of password users with their provider-normalized secret names.
    """
    client = create_dcim_client()
    async with client:
        password_mappings = await client.get_device_password_secret_names(device_id)

    if not password_mappings or not isinstance(password_mappings, dict):
        raise HTTPException(
            status_code=400,
            detail=f"No password mappings defined for device {device_id}.",
        )

    # Convert to Secret objects - use username as the name
    secrets = []
    for username, secret_name in password_mappings.items():
        # Filtering out the nv-config-manager service account temporarily.
        if username != "svc-ngc-cfa-nv-config-manager":
            secrets.append(Secret(name=username, description=f"{username} ({secret_name})"))
    return secrets


@router.get(
    "/device-id",
    summary="Get Device ID by Name",
)
async def get_device_id_by_name(device_name: str) -> Device:
    """Convert device hostname to device ID.

    Args:
        device_name: The hostname/name of the device.

    Returns:
        Device object with id and name.

    Raises:
        HTTPException: If device not found or multiple devices match.
    """
    client = create_dcim_client()
    try:
        async with client:
            device = await client.get_device_selection_by_name(device_name)
    except DCIMNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (DCIMConflictError, DCIMInvalidDataError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Device(id=device.id, name=device.name)
