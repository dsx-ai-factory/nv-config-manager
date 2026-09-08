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
"""Built-in Nautobot reference implementation of the DCIM provider API."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any, Self
from uuid import UUID

from nv_config_manager_dcim.api import (
    DCIMClient,
    DCIMProviderMetadata,
    DCIMRenderEventRegistry,
    ProviderSettings,
)
from nv_config_manager_dcim.errors import (
    DCIMConflictError,
    DCIMInvalidDataError,
    DCIMNotFoundError,
    DCIMProviderConfigurationError,
)
from nv_config_manager_dcim.models import (
    DCIMChangeEvent,
    DCIMDeviceSelection,
    DCIMDeviceSelectionFilter,
    DCIMSelection,
    DeviceMetadata,
    IntendedConfigurationUpdate,
    IntendedInterfaceNeighbor,
    IntendedNeighborDevice,
    RenderDeviceStatus,
    RenderTemplateVersion,
    ZTPDevice,
)
from nv_config_manager_dcim.render import RenderData, RenderDataExtension, RenderDataRequest

from nv_config_manager_dcim_nautobot_2x.client import NautobotException
from nv_config_manager_dcim_nautobot_2x.dhcp import NautobotDHCPOperations
from nv_config_manager_dcim_nautobot_2x.events import register_render_event_handlers
from nv_config_manager_dcim_nautobot_2x.queries import load_graphql_query
from nv_config_manager_dcim_nautobot_2x.render import build_render_data
from nv_config_manager_dcim_nautobot_2x.workflow import NautobotWorkflowClient

logger = logging.getLogger(__name__)

_ZTP_DEVICE_QUERY = load_graphql_query("provider/devices.graphql", "GetZTPDevice")
_RENDER_DEVICE_STATUS_QUERY = load_graphql_query(
    "provider/devices.graphql", "GetRenderDeviceStatus"
)
_RENDER_ENABLED_DEVICES_QUERY = load_graphql_query(
    "provider/devices.graphql", "ListRenderEnabledDevices"
)
_RENDER_TEMPLATE_VERSIONS_QUERY = load_graphql_query(
    "provider/devices.graphql", "ListRenderTemplateVersions"
)
_MANAGED_DEVICES_QUERY = load_graphql_query(
    "provider/events.graphql", "ListManagedRenderEnabledDevices"
)
_VRF_AFFECTED_DEVICES_QUERY = load_graphql_query(
    "provider/events.graphql", "ListVRFAffectedDevices"
)
_IP_ADDRESS_AFFECTED_DEVICES_QUERY = load_graphql_query(
    "provider/events.graphql", "ListIPAddressAffectedDevices"
)
_AUTONOMOUS_SYSTEM_AFFECTED_DEVICES_QUERY = load_graphql_query(
    "provider/events.graphql", "ListAutonomousSystemAffectedDevices"
)
_BGP_PEERING_AFFECTED_DEVICES_QUERY = load_graphql_query(
    "provider/events.graphql", "ListBGPPeeringAffectedDevices"
)
_BGP_ROUTING_INSTANCE_AFFECTED_DEVICE_QUERY = load_graphql_query(
    "provider/events.graphql", "GetBGPRoutingInstanceAffectedDevice"
)
_INTENDED_INTERFACE_NEIGHBORS_QUERY = load_graphql_query(
    "provider/events.graphql", "GetIntendedInterfaceNeighbors"
)
_PARAMETER_LOCATIONS_QUERY = load_graphql_query("provider/parameters.graphql", "ListLocations")
_LOCATION_BY_ID_QUERY = load_graphql_query("provider/parameters.graphql", "GetLocationById")
_PARAMETER_TENANTS_QUERY = load_graphql_query("provider/parameters.graphql", "ListTenants")
_PARAMETER_ROLES_QUERY = load_graphql_query("provider/parameters.graphql", "ListRoles")
_PARAMETER_MANAGED_TENANTS_QUERY = load_graphql_query(
    "provider/parameters.graphql", "ListManagedTenants"
)
_PARAMETER_MANAGED_ROLES_QUERY = load_graphql_query(
    "provider/parameters.graphql", "ListManagedRoles"
)
_PARAMETER_NAMESPACE_TAGS_QUERY = load_graphql_query(
    "provider/parameters.graphql", "ListNamespaceTags"
)
_PARAMETER_STATUSES_QUERY = load_graphql_query("provider/parameters.graphql", "ListStatuses")
_PARAMETER_DEVICES_QUERY = load_graphql_query("provider/parameters.graphql", "ListDeviceSelections")
_PARAMETER_DEVICE_BY_NAME_QUERY = load_graphql_query(
    "provider/parameters.graphql", "GetDeviceSelectionByName"
)
_DEVICE_METADATA_QUERY = load_graphql_query("provider/devices.graphql", "GetDeviceMetadata")
_MANAGED_DEVICE_METADATA_QUERY = load_graphql_query(
    "provider/devices.graphql", "ListManagedDeviceMetadata"
)
_DEVICE_SERIAL_QUERY = load_graphql_query("provider/devices.graphql", "GetDeviceSerial")
_RENDER_DATA_QUERY = load_graphql_query("query_config_data_by_device_id_v2.graphql")
_LOCATION_DATA_QUERY = load_graphql_query("query_location_data.graphql")

_NAUTOBOT_CONNECTION_KEYS = ("server", "token", "public_url", "verify")
_INTENDED_CONFIGURATION_PATH = "plugins/nv-config-manager/intendedconfig/"


def _is_existing_intended_configuration_error(error: NautobotException) -> bool:
    """Return whether Nautobot rejected a create for its one-per-device record."""
    message = str(error)
    return "returned 400" in message and "Intended Config Settings with this Device id" in message


def _parse_verify(value: object) -> bool | str:
    """Normalize a provider-owned TLS verification setting."""
    if isinstance(value, bool):
        return value
    if value is None:
        return True
    normalized = str(value).strip()
    if not normalized:
        return True
    lowered = normalized.lower()
    if lowered in {"true", "yes", "1"}:
        return True
    if lowered in {"false", "no", "0"}:
        return False
    return normalized


def _is_canonical_uuid(value: str) -> bool:
    """Return whether a value is a canonical UUID string."""
    try:
        return str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _tag_names(tags: object) -> tuple[str, ...]:
    """Extract tag names from a Nautobot GraphQL collection."""
    if not isinstance(tags, list):
        return ()
    return tuple(
        str(tag["name"]) for tag in tags if isinstance(tag, dict) and tag.get("name") is not None
    )


def _nautobot_connection_settings(settings: ProviderSettings) -> dict[str, str | bool]:
    """Validate and normalize explicit Nautobot provider settings."""
    values = {key: settings.get(key) for key in _NAUTOBOT_CONNECTION_KEYS}
    missing = [key for key in ("server", "token") if not str(values[key] or "").strip()]
    if missing:
        raise DCIMProviderConfigurationError(
            'DCIM provider "nautobot-2x" requires ' + ", ".join(missing)
        )
    return {
        "server": str(values["server"]),
        "token": str(values["token"]),
        "public_url": str(values["public_url"]) if values["public_url"] else "",
        "verify": _parse_verify(values["verify"]),
    }


def _render_enabled_ids(devices: list[dict[str, Any] | None]) -> list[str]:
    """Extract unique render-enabled device IDs from a Nautobot GraphQL response."""
    device_ids: list[str] = []
    for device in devices:
        if (
            device
            and (device.get("configmanagerdevicestatus") or {}).get("render_enabled")
            and device["id"] not in device_ids
        ):
            device_ids.append(device["id"])
    return device_ids


def _metadata_from_nautobot_graphql(data: dict[str, Any]) -> DeviceMetadata:
    """Normalize a Nautobot device GraphQL result into provider API metadata."""
    site = None
    location = data.get("location")
    while location:
        if location.get("location_type", {}).get("name") == "Site":
            site = location["name"]
            break
        location = location.get("parent")

    return DeviceMetadata(
        device_id=data["id"],
        name=data["name"],
        site=site or "Unknown",
        platform=platform.get("name") if (platform := data.get("platform")) else None,
        role=role.get("name") if (role := data.get("role")) else None,
        rack=rack.get("name") if (rack := data.get("rack")) else None,
        primary_ip4=primary_ip4.get("host") if (primary_ip4 := data.get("primary_ip4")) else None,
    )


class NautobotDCIMClient(NautobotDHCPOperations, NautobotWorkflowClient):
    """One broad Nautobot implementation of the SDK's DCIM client contract."""

    def __init__(
        self,
        nautobot_url: str,
        token: str,
        verify: bool | str = True,
        public_url: str | None = None,
        headers: dict[str, str] | Callable[[], dict[str, str]] | None = None,
    ) -> None:
        """Initialize the reference client with its user-facing base URL."""
        NautobotWorkflowClient.__init__(
            self,
            {"server": nautobot_url, "token": token, "verify": verify, "headers": headers},
        )
        self._public_url = (public_url or nautobot_url).rstrip("/")

    @classmethod
    def from_settings(cls, settings: ProviderSettings) -> Self:
        """Create the reference client from explicit provider settings."""
        connection_config = _nautobot_connection_settings(settings)
        return cls(
            nautobot_url=str(connection_config["server"]),
            token=str(connection_config["token"]),
            verify=connection_config["verify"],
            public_url=str(connection_config["public_url"]) or None,
        )

    @staticmethod
    def is_valid_device_id(value: str) -> bool:
        """Return whether a device identifier is a canonical Nautobot UUID."""
        return _is_canonical_uuid(value)

    @staticmethod
    def is_valid_location_id(value: str) -> bool:
        """Return whether a location identifier is a canonical Nautobot UUID."""
        return _is_canonical_uuid(value)

    async def get_location_metadata(self, location_id: str) -> DCIMSelection | None:
        """Return normalized metadata for one location UUID."""
        result = await self.graphql_query(_LOCATION_BY_ID_QUERY, {"id": location_id})
        if result.get("errors"):
            message = result["errors"][0].get("message", "Invalid location query")
            raise DCIMInvalidDataError(str(message))
        location = (result.get("data") or {}).get("location")
        if location is None:
            return None
        selections = self._parameter_selections([location], "location")
        return selections[0]

    async def get_device_metadata(self, device_id: str) -> DeviceMetadata | None:
        """Return normalized metadata for a Nautobot device UUID."""
        result = await self.graphql_query(
            _DEVICE_METADATA_QUERY,
            {"id": device_id},
        )
        device_data = result.get("data", {}).get("device")
        if not device_data:
            logger.warning("Device %s not found in Nautobot", device_id)
            return None
        return _metadata_from_nautobot_graphql(device_data)

    async def get_intended_interface_neighbors(
        self, device_id: str
    ) -> list[IntendedInterfaceNeighbor]:
        """Return Nautobot's intended interface-neighbor records for one device."""
        result = await self.graphql_query(
            _INTENDED_INTERFACE_NEIGHBORS_QUERY,
            {"device_id": device_id},
        )
        interfaces = result.get("data", {}).get("interfaces")
        if not isinstance(interfaces, list):
            raise DCIMInvalidDataError(
                f"Nautobot returned invalid intended interface data for {device_id}"
            )
        normalized: list[IntendedInterfaceNeighbor] = []
        for interface in interfaces:
            if not isinstance(interface, dict) or not isinstance(interface.get("name"), str):
                raise DCIMInvalidDataError("Nautobot returned invalid intended interface data")
            connected_interface = interface.get("connected_interface")
            if not isinstance(connected_interface, dict):
                normalized.append(
                    IntendedInterfaceNeighbor(
                        name=interface["name"],
                        tags=_tag_names(interface.get("tags")),
                    )
                )
                continue
            device = connected_interface.get("device")
            if not isinstance(device, dict):
                module = connected_interface.get("module")
                device = module.get("device") if isinstance(module, dict) else None
            if not isinstance(device, dict) or not isinstance(device.get("name"), str):
                raise DCIMInvalidDataError("Nautobot returned invalid intended connected device")
            role = device.get("role")
            rack = device.get("rack")
            normalized.append(
                IntendedInterfaceNeighbor(
                    name=interface["name"],
                    tags=_tag_names(interface.get("tags")),
                    connected_interface_name=(
                        str(connected_interface["name"])
                        if connected_interface.get("name") is not None
                        else None
                    ),
                    connected_interface_mac=(
                        str(connected_interface["mac_address"])
                        if connected_interface.get("mac_address") is not None
                        else None
                    ),
                    connected_device=IntendedNeighborDevice(
                        name=device["name"],
                        serial=str(device["serial"]) if device.get("serial") is not None else None,
                        role=str(role["name"]) if isinstance(role, dict) else None,
                        rack=str(rack["name"]) if isinstance(rack, dict) else None,
                        position=device.get("position"),
                    ),
                )
            )
        return normalized

    @staticmethod
    def _parameter_selections(data: object, label: str) -> list[DCIMSelection]:
        """Validate a provider list response and normalize its form options."""
        if not isinstance(data, list):
            raise DCIMInvalidDataError(f"Nautobot returned invalid {label} data")
        selections: list[DCIMSelection] = []
        for item in data:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("id"), str)
                or not isinstance(item.get("name"), str)
            ):
                raise DCIMInvalidDataError(f"Nautobot returned invalid {label} data")
            selections.append(DCIMSelection(id=item["id"], name=item["name"]))
        return selections

    async def list_locations(self, location_types: tuple[str, ...] = ()) -> list[DCIMSelection]:
        """Return locations suitable for workflow form choices."""
        result = await self.graphql_query(
            _PARAMETER_LOCATIONS_QUERY,
            {"location_types": list(location_types) or None},
        )
        return self._parameter_selections((result.get("data") or {}).get("locations"), "location")

    async def _list_managed_choices(self, field: str) -> list[DCIMSelection]:
        """Collect distinct managed-device tenant or role choices across all pages."""
        query = (
            _PARAMETER_MANAGED_TENANTS_QUERY
            if field == "tenant"
            else _PARAMETER_MANAGED_ROLES_QUERY
        )
        selections: dict[str, DCIMSelection] = {}
        page_size = 1000
        offset = 0
        while True:
            result = await self.graphql_query(query, {"limit": page_size, "offset": offset})
            managed_devices = (result.get("data") or {}).get("config_manager_devices")
            if not isinstance(managed_devices, list):
                raise DCIMInvalidDataError(f"Nautobot returned invalid managed {field} data")
            if not managed_devices:
                break
            for entry in managed_devices:
                choice = (entry.get("device") or {}).get(field) if isinstance(entry, dict) else None
                if not isinstance(choice, dict) or not isinstance(choice.get("name"), str):
                    continue
                choice_id = str(choice.get("id") or choice["name"])
                selections[choice_id] = DCIMSelection(id=choice_id, name=choice["name"])
            if len(managed_devices) < page_size:
                break
            offset += page_size
        return list(selections.values())

    async def list_tenants(self, managed_only: bool = False) -> list[DCIMSelection]:
        """Return tenant form choices, optionally scoped to managed devices."""
        if managed_only:
            return await self._list_managed_choices("tenant")
        result = await self.graphql_query(_PARAMETER_TENANTS_QUERY)
        return self._parameter_selections((result.get("data") or {}).get("tenants"), "tenant")

    async def list_roles(self, managed_only: bool = False) -> list[DCIMSelection]:
        """Return role form choices, optionally scoped to managed devices."""
        if managed_only:
            return await self._list_managed_choices("role")
        result = await self.graphql_query(_PARAMETER_ROLES_QUERY)
        return self._parameter_selections((result.get("data") or {}).get("roles"), "role")

    async def list_namespace_tags(self, location: str | None = None) -> list[str]:
        """Return the distinct namespace tag names at an optional location."""
        result = await self.graphql_query(_PARAMETER_NAMESPACE_TAGS_QUERY, {"location": location})
        namespaces = (result.get("data") or {}).get("namespaces")
        if not isinstance(namespaces, list):
            raise DCIMInvalidDataError("Nautobot returned invalid namespace tag data")
        tags: set[str] = set()
        for namespace in namespaces:
            if not isinstance(namespace, dict) or not isinstance(namespace.get("tags"), list):
                raise DCIMInvalidDataError("Nautobot returned invalid namespace tag data")
            for tag in namespace["tags"]:
                if not isinstance(tag, dict):
                    raise DCIMInvalidDataError("Nautobot returned invalid namespace tag data")
                tag_name = tag.get("name")
                if isinstance(tag_name, str) and tag_name:
                    tags.add(tag_name)
        return sorted(tags)

    async def list_overlays(
        self, location: str | None = None, isolation_type: str | None = None
    ) -> list[DCIMSelection]:
        """Return overlay form choices using the Nautobot overlays plugin."""
        params = {
            key: value
            for key, value in {"location": location, "isolation_type": isolation_type}.items()
            if value
        }
        overlays = await self.get_all("plugins/overlays/overlays/", params=params)
        return sorted(
            self._parameter_selections(overlays, "overlay"), key=lambda overlay: overlay.name
        )

    async def list_statuses(self, content_type: str | None = None) -> list[DCIMSelection]:
        """Return status form choices applicable to an optional content type."""
        result = await self.graphql_query(
            _PARAMETER_STATUSES_QUERY,
            {"content_types": [content_type] if content_type else None},
        )
        return self._parameter_selections((result.get("data") or {}).get("statuses"), "status")

    async def list_devices(self, filters: DCIMDeviceSelectionFilter) -> list[DCIMDeviceSelection]:
        """Return device choices matching the normalized workflow form filters."""
        variables: dict[str, list[str] | bool] = {}
        field_values = {
            "site": filters.sites,
            "status": filters.statuses,
            "role": filters.roles,
            "tenant": filters.tenants,
            "device_type_id": filters.device_type_ids,
            "manufacturer": filters.manufacturers,
            "platform": filters.platforms,
        }
        for field, values in field_values.items():
            if values:
                variables[field] = list(values)
        if filters.managed_only:
            variables["managed_only"] = True

        try:
            result = await self.graphql_query(_PARAMETER_DEVICES_QUERY, variables)
        except Exception as exc:  # noqa: BLE001 - translate provider query failures at the boundary
            raise DCIMInvalidDataError(f"Unable to query DCIM devices: {exc}") from exc
        if result.get("errors"):
            message = result["errors"][0].get("message", "Invalid device query")
            raise DCIMInvalidDataError(str(message))
        devices = (result.get("data") or {}).get("devices")
        if not isinstance(devices, list):
            raise DCIMInvalidDataError("Nautobot returned invalid device choice data")
        selections: list[DCIMDeviceSelection] = []
        for device in devices:
            if not isinstance(device, dict) or not isinstance(device.get("id"), str):
                raise DCIMInvalidDataError("Nautobot returned invalid device choice data")
            name = device.get("name")
            if not isinstance(name, str) or not name:
                continue
            platform_name = (device.get("platform") or {}).get("name")
            selections.append(
                DCIMDeviceSelection(
                    id=device["id"],
                    name=name,
                    platform=platform_name.lower().replace(" ", "-")
                    if isinstance(platform_name, str) and platform_name
                    else None,
                )
            )
        return selections

    async def get_device_selection_by_name(self, name: str) -> DCIMDeviceSelection:
        """Return exactly one device form choice matched by its name."""
        result = await self.graphql_query(_PARAMETER_DEVICE_BY_NAME_QUERY, {"name": name})
        if result.get("errors"):
            message = result["errors"][0].get("message", "Invalid device query")
            raise DCIMInvalidDataError(str(message))
        devices = (result.get("data") or {}).get("devices")
        if not isinstance(devices, list):
            raise DCIMInvalidDataError("Nautobot returned invalid device choice data")
        if not devices:
            raise DCIMNotFoundError(f"Device with name '{name}' not found")
        if len(devices) > 1:
            raise DCIMConflictError(
                f"Multiple devices found with name '{name}'. Please use device ID directly."
            )
        device = devices[0]
        if (
            not isinstance(device, dict)
            or not isinstance(device.get("id"), str)
            or not isinstance(device.get("name"), str)
        ):
            raise DCIMInvalidDataError("Nautobot returned invalid device choice data")
        return DCIMDeviceSelection(id=device["id"], name=device["name"])

    async def get_managed_device_metadata(self, page_size: int = 100) -> list[DeviceMetadata]:
        """Return normalized metadata for every NVCM-managed Nautobot device."""
        devices: list[DeviceMetadata] = []
        offset = 0

        try:
            while True:
                result = await self.graphql_query(
                    _MANAGED_DEVICE_METADATA_QUERY, {"limit": page_size, "offset": offset}
                )
                managed_devices = result.get("data", {}).get("config_manager_devices", [])
                if not managed_devices:
                    break

                for managed_device in managed_devices:
                    device_data = managed_device.get("device")
                    if not device_data:
                        continue
                    try:
                        devices.append(_metadata_from_nautobot_graphql(device_data))
                    except Exception as exc:  # noqa: BLE001 - skip malformed records as before
                        logger.error("Failed to parse device %s: %s", device_data.get("id"), exc)

                if len(managed_devices) < page_size:
                    break
                offset += page_size
        except Exception as exc:  # noqa: BLE001 - return completed pages as before
            logger.error("Failed to get all devices: %s", exc)

        return devices

    def get_device_ui_url(self, device_id: str) -> str:
        """Build the user-facing Nautobot device URL."""
        return f"{self._public_url}/dcim/devices/{device_id}/"

    async def get_ztp_device(self, device_id: str) -> ZTPDevice:
        """Return normalized boot metadata for a Nautobot managed device."""
        result = await self.graphql_query(_ZTP_DEVICE_QUERY, variables={"id": device_id})
        managed_device = result.get("data", {}).get("config_manager_device")
        if managed_device is None:
            raise DCIMNotFoundError(f"No DCIM device data found for {device_id}.")

        try:
            device = managed_device["device"]
            addresses = sorted(
                {
                    address["host"]
                    for interface in device["interfaces"]
                    for address in interface["ip_addresses"]
                }
            )
            config_context = device.get("config_context") or {}
            firmware_version = config_context.get("intended-firmware", {}).get("version")
            intended_config = managed_device.get("intended_config")
            config_store_instance = (
                intended_config["config_store_instance"] if intended_config else None
            )
            return ZTPDevice(
                device_id=device["id"],
                name=device["name"],
                addresses=addresses,
                platform_name=device["platform"]["name"],
                firmware_version=firmware_version,
                config_store_instance=config_store_instance,
            )
        except (KeyError, TypeError) as exc:
            raise DCIMInvalidDataError(
                f"Nautobot returned incomplete ZTP device data for {device_id}"
            ) from exc

    async def get_device_serial(self, device_id: str) -> str:
        """Return a Nautobot device serial number for ZTP validation."""
        result = await self.graphql_query(
            _DEVICE_SERIAL_QUERY,
            variables={"id": device_id},
        )
        serial = result.get("data", {}).get("device", {}).get("serial")
        if not serial:
            raise DCIMNotFoundError(f"No serial found in DCIM for {device_id}.")
        return str(serial)

    async def mark_ztp_device_provisioned(self, device_id: str) -> None:
        """Mark a Nautobot device provisioned after successful ZTP."""
        await self.set_status_provisioned(device_id)

    async def get_render_data(self, request: RenderDataRequest) -> RenderData:
        """Return the Nautobot data set consumed by the template engine."""
        device_id = request.device_id
        device_data = await self.graphql_query(
            _RENDER_DATA_QUERY,
            {"id": device_id, "id_str": device_id},
        )
        try:
            location = device_data["data"]["device"]["location"]
            while location["location_type"]["name"] != "Site":
                location = location["parent"]
            location_name = location["name"]
        except (KeyError, TypeError) as exc:
            raise DCIMInvalidDataError(
                f"Nautobot returned incomplete render location data for {device_id}"
            ) from exc

        location_data = await self.graphql_query(_LOCATION_DATA_QUERY, {"location": location_name})
        render_data = build_render_data(device_data, location_data)
        plugin_data: dict[str, RenderDataExtension] = {}
        device = device_data["data"]["device"]
        device_name = str(device["name"])
        config_context = device.get("config_context") or {}

        for name, requirement in request.plugin_data_requirements.items():
            parameters = requirement.parameters
            graphql_query = parameters.get("graphql_query")
            context_keys = parameters.get("config_context_keys")
            if isinstance(graphql_query, str):
                data = await self.graphql_query(
                    graphql_query,
                    {"id": device_id, "hostname": device_name},
                )
                schema = "nv-config-manager/nautobot-graphql"
            elif isinstance(context_keys, list) and all(
                isinstance(key, str) for key in context_keys
            ):
                data = {
                    "config_context": {
                        key: config_context[key] for key in context_keys if key in config_context
                    }
                }
                schema = "nv-config-manager/nautobot-config-context"
            else:
                raise DCIMInvalidDataError(
                    f"Unsupported render-data requirement '{name}' for the Nautobot provider"
                )
            plugin_data[name] = RenderDataExtension(schema=schema, version=1, data=data)

        return render_data.model_copy(update={"plugin_data": plugin_data})

    async def get_render_device_status(self, device_id: str) -> RenderDeviceStatus | None:
        """Return the managed-device status needed before queueing a render."""
        result = await self.graphql_query(_RENDER_DEVICE_STATUS_QUERY, {"id": device_id})
        status = result.get("data", {}).get("config_manager_device")
        if status is None:
            return None
        return RenderDeviceStatus(
            render_enabled=bool(status.get("render_enabled")),
            is_aggregate_managed=bool(status.get("is_aggregate_managed")),
        )

    async def get_render_enabled_device_ids(self, is_aggregate_managed: bool | None) -> list[str]:
        """Return all render-enabled managed devices in one environment scope."""
        variables: dict[str, Any] = {}
        if is_aggregate_managed is not None:
            variables["is_aggregate_managed"] = is_aggregate_managed
        result = await self.graphql_query(_RENDER_ENABLED_DEVICES_QUERY, variables)
        devices = result.get("data", {}).get("config_manager_devices", [])
        return [str(device["id"]) for device in devices]

    async def get_render_template_versions(self) -> list[RenderTemplateVersion]:
        """Return the render template version stored for every managed device."""
        result = await self.graphql_query(_RENDER_TEMPLATE_VERSIONS_QUERY)
        devices = result.get("data", {}).get("config_manager_devices", [])
        return [
            RenderTemplateVersion(
                device_id=str(entry["device"]["id"]),
                template_version=(entry.get("intended_config") or {}).get("template_version"),
            )
            for entry in devices
            if entry.get("device")
        ]

    async def upsert_intended_configuration(self, update: IntendedConfigurationUpdate) -> None:
        """Persist render metadata through the Nautobot plugin's upsert endpoint."""
        data = {
            "device_id": update.device_id,
            "config_store_instance": update.config_store_instance,
            "path": update.path,
            "commit_id": update.commit_id,
            "updated": update.updated,
            "updated_by": update.updated_by,
            "commit_message": update.commit_message,
            "template_version": update.template_version,
        }
        try:
            await self.post(_INTENDED_CONFIGURATION_PATH, data)
        except NautobotException as exc:
            if not _is_existing_intended_configuration_error(exc):
                raise
            await self.patch(
                f"{_INTENDED_CONFIGURATION_PATH}{update.device_id}/",
                {key: value for key, value in data.items() if key != "device_id"},
            )

    async def update_render_template_version(self, device_id: str, template_version: str) -> None:
        """Update the template version stored by the Nautobot plugin."""
        await self.patch(
            f"plugins/nv-config-manager/intendedconfig/{device_id}/",
            {"template_version": template_version},
        )

    async def get_render_enabled_devices_matching(self, filters: Mapping[str, Any]) -> list[str]:
        """Resolve Nautobot-managed devices matching a provider event filter."""
        result = await self.graphql_query(_MANAGED_DEVICES_QUERY, dict(filters))
        return _render_enabled_ids(result.get("data", {}).get("devices", []))

    async def get_render_enabled_devices_for_vrf(self, vrf_id: str) -> list[str]:
        """Resolve Nautobot-managed devices affected by a VRF event."""
        result = await self.graphql_query(_VRF_AFFECTED_DEVICES_QUERY, {"id": vrf_id})
        devices = result.get("data", {}).get("vrf", {}).get("devices", [])
        return _render_enabled_ids(devices)

    async def get_render_enabled_devices_for_ip_address(self, ip_address_id: str) -> list[str]:
        """Resolve Nautobot-managed devices affected by an IP-address event."""
        result = await self.graphql_query(_IP_ADDRESS_AFFECTED_DEVICES_QUERY, {"id": ip_address_id})
        interfaces = result.get("data", {}).get("ip_address", {}).get("interfaces", [])
        return _render_enabled_ids([interface.get("device") for interface in interfaces])

    async def get_render_enabled_devices_for_autonomous_system(self, asn: str) -> list[str]:
        """Resolve Nautobot-managed devices affected by an autonomous-system event."""
        result = await self.graphql_query(_AUTONOMOUS_SYSTEM_AFFECTED_DEVICES_QUERY, {"id": [asn]})
        instances = result.get("data", {}).get("bgp_routing_instances", [])
        return _render_enabled_ids([instance.get("device") for instance in instances])

    async def get_render_enabled_devices_for_bgp_peering(self, peering_id: str) -> list[str]:
        """Resolve Nautobot-managed devices affected by a BGP peering event."""
        result = await self.graphql_query(_BGP_PEERING_AFFECTED_DEVICES_QUERY, {"id": peering_id})
        endpoints = result.get("data", {}).get("bgp_peering", {}).get("endpoints", [])
        return _render_enabled_ids(
            [endpoint.get("routing_instance", {}).get("device") for endpoint in endpoints]
        )

    async def get_render_enabled_device_for_bgp_routing_instance(
        self, routing_instance_id: str
    ) -> str | None:
        """Resolve one Nautobot-managed device for a routing-instance event."""
        result = await self.graphql_query(
            _BGP_ROUTING_INSTANCE_AFFECTED_DEVICE_QUERY, {"id": routing_instance_id}
        )
        device = result.get("data", {}).get("bgp_routing_instance", {}).get("device")
        device_ids = _render_enabled_ids([device])
        return device_ids[0] if device_ids else None

    async def get_module_bay_parent_device_id(self, module_bay_id: str) -> str:
        """Return the parent device of a Nautobot module bay for a cable event."""
        module_bay = await self.get(f"dcim/module-bays/{module_bay_id}/")
        try:
            return str(module_bay["parent_device"]["id"])
        except (KeyError, TypeError) as exc:
            raise DCIMInvalidDataError(
                f"Nautobot returned incomplete module bay data for {module_bay_id}"
            ) from exc

    async def get_cable_termination_device_id(self, termination: Mapping[str, Any]) -> str:
        """Resolve a Nautobot cable termination to its owning device.

        Changelog payloads may contain an expanded termination or only its REST
        object reference. The provider owns that shape difference.
        """
        current = termination
        visited_endpoints: set[str] = set()
        while True:
            device = current.get("device")
            if isinstance(device, Mapping) and device.get("id"):
                return str(device["id"])

            module = current.get("module")
            if isinstance(module, Mapping):
                parent_module_bay = module.get("parent_module_bay")
                if isinstance(parent_module_bay, Mapping) and parent_module_bay.get("id"):
                    return await self.get_module_bay_parent_device_id(str(parent_module_bay["id"]))

            url = current.get("url")
            if not isinstance(url, str) or not url:
                raise DCIMInvalidDataError("Nautobot cable termination has no device or REST URL")
            endpoint = url.split("/api/", maxsplit=1)[-1].lstrip("/")
            if endpoint in visited_endpoints:
                raise DCIMInvalidDataError(
                    f"Nautobot cable termination contains a reference cycle at {endpoint}"
                )
            visited_endpoints.add(endpoint)
            resolved = await self.get(endpoint)
            if not isinstance(resolved, Mapping):
                raise DCIMInvalidDataError("Nautobot returned an invalid cable termination")
            current = resolved

    async def set_status_provisioned(self, device_id: str) -> None:
        """Legacy Nautobot method retained while ZTP completes migration."""
        await self.patch(f"dcim/devices/{device_id}/", {"status": "Provisioned"})


class NautobotProvider:
    """Built-in provider that defines the reference API behavior."""

    metadata = DCIMProviderMetadata(
        name="nautobot-2x",
        display_name="Nautobot 2.x",
        provider_version="1.0.0",
        supported_api_versions=("1.0",),
    )

    def validate_settings(self, settings: ProviderSettings) -> None:
        """Validate explicit Nautobot provider settings."""
        _nautobot_connection_settings(settings)

    def create_client(self, settings: ProviderSettings) -> DCIMClient:
        """Create the configured Nautobot reference client."""
        return NautobotDCIMClient.from_settings(settings)

    def register_render_event_handlers(self, registry: DCIMRenderEventRegistry) -> None:
        """Register Nautobot changelog event interpretation with the dispatcher."""
        register_render_event_handlers(registry)

    def create_nautobot_mcp_client(
        self,
        settings: ProviderSettings,
        headers: Callable[[], dict[str, str]],
    ) -> NautobotDCIMClient:
        """Create the optional Nautobot MCP adapter with caller auth."""
        connection_config = _nautobot_connection_settings(settings)
        return NautobotDCIMClient(
            nautobot_url=str(connection_config["server"]),
            token="",
            verify=connection_config["verify"],
            public_url=str(connection_config["public_url"]) or None,
            headers=headers,
        )

    def normalize_event(self, payload: Mapping[str, Any]) -> DCIMChangeEvent:
        """Translate the Nautobot changelog publisher's legacy message shape.

        The publisher still emits its historical payload during the migration;
        all NVCM consumers receive the public event model after this boundary.
        """
        try:
            record = payload.get("record")
            if record is not None and not isinstance(record, Mapping):
                raise TypeError("record is not an object")
            request = payload.get("request", {})
            if not isinstance(request, Mapping):
                raise TypeError("request is not an object")
            operation = str(payload["event"])
            if operation not in {"create", "update", "delete"}:
                raise ValueError("event is not a supported operation")
            changed_fields = payload.get("changed_fields", ())
            if not isinstance(changed_fields, (list, tuple)) or not all(
                isinstance(field, str) for field in changed_fields
            ):
                raise TypeError("changed_fields is not a list of strings")
            correlation_id = payload.get("correlation_id")
            if correlation_id is not None and not isinstance(correlation_id, str):
                raise TypeError("correlation_id is not a string")
            return DCIMChangeEvent(
                provider=self.metadata.name,
                operation=operation,
                object_type=str(payload["model"]),
                object_id=str(record.get("id", "")) if record else "",
                timestamp=str(payload["@timestamp"]),
                actor=str(request.get("user", "system")),
                record=dict(record) if record is not None else None,
                changed_fields=tuple(changed_fields),
                correlation_id=correlation_id,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DCIMInvalidDataError("Invalid Nautobot changelog event") from exc
