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
"""Nautobot Client for Temporal workflows.

Extends the base NautobotClient with temporal-specific methods for
device queries, workflow integration, and NVIDIA Config Manager plugin interactions.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast

from nv_config_manager_dcim import (
    ConfigurationBackupIntent,
    ConfigurationBackupMetadata,
    DeviceVRF,
    FirmwareBundle,
    FirmwareComponent,
    HostInterfaceMetadata,
    HostMetadata,
    IBHostSite,
    IBInterfaceGuid,
    IBNeighbor,
    IBPKeyAssignment,
    IBPKeyCleanup,
    IBPKeyContext,
    IBPKeyPartition,
    IBSwitchTopology,
    NamespaceRouteDistinguisher,
    Platform,
    SpectrumXVRF,
)
from nv_config_manager_dcim.errors import (
    DCIMConnectivityError,
    DCIMError,
    DCIMInvalidDataError,
    DCIMNotFoundError,
)
from nv_config_manager_dcim.workflow_models import (
    DeviceInventoryFilter,
    HostDeviceData,
    InterfaceData,
    NetworkDeviceData,
    OSImageVersions,
)

from nv_config_manager_dcim_nautobot_2x.client import NautobotClient as BaseNautobotClient
from nv_config_manager_dcim_nautobot_2x.client import NautobotException
from nv_config_manager_dcim_nautobot_2x.queries import (
    load_graphql_query,
    load_graphql_selection,
    render_graphql_fields_template,
)
from nv_config_manager_dcim_nautobot_2x.workflow_models import (
    host_device_from_nautobot_graphql,
    interface_from_nautobot_graphql,
    network_device_from_nautobot_graphql,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class NautobotWorkflowError(DCIMError):
    """Provider error compatible with former Temporal error call sites."""

    def __init__(self, message: str, *, non_retryable: bool = False) -> None:
        super().__init__(message)
        self.non_retryable = non_retryable


# This name avoids a mechanical rewrite of the provider implementation while
# ensuring the provider does not expose or depend on Temporal exceptions.
ApplicationError = NautobotWorkflowError

# Re-export for backward compatibility
__all__ = ["NautobotClient", "NautobotException", "NautobotWorkflowClient"]

CONFIG_MANAGER_BACKUP_CONFIG_PATH = "plugins/nv-config-manager/backupconfig"

# REST API base path for the nautobot_app_overlays plugin (Overlay/VXLAN models).
OVERLAYS_PLUGIN_BASE = "plugins/overlays"

_BACKUP_ENABLED_DEVICES_QUERY = load_graphql_query(
    "workflow/operations.graphql", "GetBackupEnabledDevices"
)
_BACKUP_SUPPORTED_PLATFORMS = {"Arista EOS", "Cumulus Linux", "Juniper Junos", "NV-OS"}
_BACKUP_SUPPORTED_STATUSES = {"Provisioned", "Active"}

_OS_IMAGE_DEVICE_QUERY = load_graphql_query("workflow/operations.graphql", "GetOSImageDevice")
_OS_IMAGE_DESIRED_QUERY = load_graphql_query("workflow/operations.graphql", "GetOSImageDesired")
_DEVICE_CONFIG_CONTEXT_QUERY = load_graphql_query(
    "workflow/operations.graphql", "GetDeviceConfigContext"
)
_IB_SWITCH_TOPOLOGY_QUERY = load_graphql_query("workflow/infiniband.graphql", "GetIBSwitchTopology")
_IB_INTERFACE_GUIDS_QUERY = load_graphql_query("workflow/infiniband.graphql", "GetIBInterfaceGUIDs")
_IB_INTERFACES_BY_GUID_QUERY = load_graphql_query(
    "workflow/infiniband.graphql", "GetIBInterfacesByGUID"
)
_IB_PKEY_RESOLVE_BY_NAME_QUERY = load_graphql_query(
    "workflow/infiniband.graphql", "ResolveIBPKeyDeviceByName"
)
_IB_PKEY_RESOLVE_BY_IP_QUERY = load_graphql_query(
    "workflow/infiniband.graphql", "ResolveIBPKeyDeviceByIP"
)
_SPECTRUM_X_VRFS_QUERY = load_graphql_query("workflow/infiniband.graphql", "GetSpectrumXVRFs")

_SPECTRUM_X_ISOLATION_TYPE = "spectrum_x_vrf"
_SPECTRUM_X_VXLAN_L3_VNI_TYPE = "l3"
_SPECTRUM_X_DEFAULT_STATUS_NAME = "Active"
_IB_PKEY_ISOLATION_TYPE = "ib_pkey"
_IB_PKEY_SITE_LOCATION_TYPE = "Site"
_IB_PKEY_IPV4_PATTERN = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
_IB_PKEY_VALUE_PATTERN = re.compile(r"^0[xX][0-9a-fA-F]{1,4}$")
_NAUTOBOT_PLATFORM_NAMES = {
    Platform.ARISTA_EOS: "Arista EOS",
    Platform.CUMULUS_LINUX: "Cumulus Linux",
    Platform.NV_OS: "NV-OS",
    Platform.MLNX_OS: "MLNX-OS",
    Platform.JUNIPER_JUNOS: "Juniper Junos",
    Platform.UFM: "UFM",
}


def _canonicalize_pkey_value(value: str) -> str | None:
    """Return a canonical PKey value when its input is valid."""
    if not value or not _IB_PKEY_VALUE_PATTERN.match(value):
        return None
    return f"0x{int(value, 16):04x}"


DeviceVrfInfo = DeviceVRF
"""Backward-compatible alias for the provider-neutral device VRF model."""


class NautobotWorkflowClient(BaseNautobotClient):
    """Async Nautobot Client for Temporal workflows.

    Extends the base NautobotClient with methods specific to temporal
    workflows, including device queries, NVIDIA Config Manager plugin interactions, and
    VRF management.
    """

    def __init__(self, connection_config: Mapping[str, Any]) -> None:
        """Initialize the built-in provider client from normalized settings."""
        super().__init__(
            nautobot_url=str(connection_config["server"]),
            token=str(connection_config["token"]),
            verify=connection_config["verify"],
            headers=connection_config.get("headers"),
        )

    async def graphql_query(
        self, query: str, variables: dict[str, Any] | None = None, timeout: int | None = 10
    ) -> dict[str, Any]:
        """Execute a graphql query with Temporal-specific error handling."""
        logger.info("Sending GraphQL query to Nautobot")
        try:
            return await super().graphql_query(query, variables, timeout)
        except NautobotException as e:
            raise ApplicationError(str(e), non_retryable=True) from e

    async def get_backup_enabled_device_ids(self, is_aggregate_managed: bool) -> set[str]:
        """Return backup-eligible devices using the built-in provider's data model."""
        try:
            response = await self.graphql_query(
                _BACKUP_ENABLED_DEVICES_QUERY,
                {"is_aggregate_managed": is_aggregate_managed},
            )
        except ApplicationError as exc:
            raise DCIMConnectivityError("Could not load backup-enabled devices") from exc
        try:
            return {
                str(device["id"])
                for entry in response["data"]["config_manager_devices"]
                if (device := entry["device"])
                and (device.get("status") or {}).get("name") in _BACKUP_SUPPORTED_STATUSES
                and (device.get("platform") or {}).get("name") in _BACKUP_SUPPORTED_PLATFORMS
            }
        except (KeyError, TypeError) as exc:
            raise DCIMInvalidDataError(
                "Nautobot returned invalid backup-enabled device data"
            ) from exc

    async def get_os_image_versions(self, device_id: str) -> OSImageVersions:
        """Return firmware intent and target data using the built-in data model."""
        try:
            intended_data = await self.graphql_query(_OS_IMAGE_DEVICE_QUERY, {"id": device_id})
            device = intended_data["data"]["device"]
            platform = device["platform"]["name"]
            intended_firmware = device["config_context"]["intended-firmware"]["version"]
            ztp_ipv4_address = device["config_context"]["ztp"]["ipv4"][0]
            location = device["location"]
            site_id = (
                location["id"]
                if location["location_type"]["name"] == "Site"
                else location["parent"]["id"]
            )
            role = device["role"]["name"].lower().replace(" ", "-")
            desired_data = await self.graphql_query(_OS_IMAGE_DESIRED_QUERY, {"id": site_id})
            desired_firmware = desired_data["data"]["config_contexts"][0]["data"][
                "firmware-targets"
            ][role][platform]
            return OSImageVersions(
                intended_firmware=intended_firmware,
                desired_firmware=desired_firmware,
                ztp_address=ztp_ipv4_address,
            )
        except (KeyError, IndexError, TypeError) as exc:
            raise DCIMInvalidDataError("Nautobot returned invalid OS image data") from exc

    async def set_intended_os_image(self, device_id: str, desired_firmware: str) -> None:
        """Persist the intended OS image through the built-in provider's context model."""
        await self.merge_config_context(
            device_id,
            {"intended-firmware": {"version": desired_firmware}},
        )

    async def _get_nautobot_device_config_context(self, device_id: str) -> dict[str, Any]:
        """Read the built-in provider's internal configuration context representation."""
        try:
            result = await self.graphql_query(_DEVICE_CONFIG_CONTEXT_QUERY, {"id": device_id})
            return cast(dict[str, Any], result["data"]["device"]["config_context"])
        except (KeyError, TypeError) as exc:
            raise DCIMInvalidDataError(
                "Nautobot returned invalid device configuration context"
            ) from exc

    async def get_device_password_mapping_users(self, device_id: str) -> set[str]:
        """Map Nautobot configuration context to password-rotation user intent."""
        config_context = await self._get_nautobot_device_config_context(device_id)
        password_mappings = config_context.get("password_mappings")
        if not isinstance(password_mappings, dict):
            return set()
        return {str(username) for username in password_mappings}

    async def get_device_secret_versions(self, device_id: str) -> Mapping[str, str]:
        """Map Nautobot configuration context to normalized secret-version intent."""
        config_context = await self._get_nautobot_device_config_context(device_id)
        secret_versions = config_context.get("secrets_versions")
        if not isinstance(secret_versions, dict):
            return {}
        return {
            str(secret_type): str(version)
            for secret_type, version in secret_versions.items()
            if isinstance(secret_type, str) and version is not None
        }

    async def get_device_password_secret_names(self, device_id: str) -> Mapping[str, str]:
        """Map Nautobot password mappings to username-to-secret-name intent."""
        config_context = await self._get_nautobot_device_config_context(device_id)
        password_mappings = config_context.get("password_mappings")
        if not isinstance(password_mappings, dict):
            return {}
        result: dict[str, str] = {}
        for username, user_config in password_mappings.items():
            if not isinstance(username, str) or not isinstance(user_config, dict):
                continue
            password_type = user_config.get("password", "")
            rotation = user_config.get("rotation", "")
            result[username] = f"{password_type}_{rotation}"
        return result

    async def get_ib_switch_topology(self, switch_device_ids: list[str]) -> IBSwitchTopology:
        """Read Nautobot's modeled neighbors for the requested IB switch devices."""
        data = await self.graphql_query(
            _IB_SWITCH_TOPOLOGY_QUERY, {"device_ids": switch_device_ids}
        )
        switch_names: dict[str, str] = {}
        intended_neighbors: dict[str, dict[str, IBNeighbor]] = {}
        for device in (data.get("data") or {}).get("devices") or []:
            device_id = str(device.get("id") or "")
            if not device_id:
                continue
            switch_names[device_id] = str(device.get("name") or "")
            neighbors: dict[str, IBNeighbor] = {}
            for interface in device.get("interfaces") or []:
                connected = interface.get("connected_interface") or {}
                local_name = str(interface.get("name") or "")
                device_name = str((connected.get("device") or {}).get("name") or "")
                interface_name = str(connected.get("name") or "")
                if local_name and device_name and interface_name:
                    neighbors[local_name] = IBNeighbor(
                        device_name=device_name,
                        interface_name=interface_name,
                    )
            intended_neighbors[device_id] = neighbors
        return IBSwitchTopology(
            switch_names=switch_names,
            intended_neighbors=intended_neighbors,
        )

    async def get_ib_interface_guids(
        self, device_interface_pairs: set[tuple[str, str]]
    ) -> list[IBInterfaceGuid]:
        """Resolve current GUIDs for the requested device/interface pairs."""
        if not device_interface_pairs:
            return []
        device_names = sorted({device_name for device_name, _ in device_interface_pairs})
        data = await self.graphql_query(_IB_INTERFACE_GUIDS_QUERY, {"device_names": device_names})
        requested_pairs = {
            (device.lower(), interface) for device, interface in device_interface_pairs
        }
        result: list[IBInterfaceGuid] = []
        for device in (data.get("data") or {}).get("devices") or []:
            device_name = str(device.get("name") or "")
            if not device_name:
                continue
            for interface in device.get("interfaces") or []:
                interface_name = str(interface.get("name") or "")
                if (device_name.lower(), interface_name) not in requested_pairs:
                    continue
                result.append(
                    IBInterfaceGuid(
                        interface_id=str(interface.get("id") or ""),
                        device_name=device_name,
                        interface_name=interface_name,
                        guid=str(interface.get("cf_ib_guid") or ""),
                    )
                )
        return result

    async def get_ib_interface_guid(self, interface_id: str) -> IBInterfaceGuid:
        """Read a Nautobot interface and normalize its existing IB GUID."""
        data = await self.get(f"dcim/interfaces/{interface_id}/")
        device = data.get("device") or {}
        return IBInterfaceGuid(
            interface_id=interface_id,
            device_name=str(device.get("display") or device.get("name") or ""),
            interface_name=str(data.get("name") or ""),
            guid=str((data.get("custom_fields") or {}).get("ib_guid") or ""),
        )

    async def set_ib_interface_guid(self, interface_id: str, guid: str) -> None:
        """Persist an IB GUID through Nautobot's interface custom field."""
        await self.patch(
            f"dcim/interfaces/{interface_id}/",
            data={"custom_fields": {"ib_guid": guid}},
        )

    async def get_firmware_bundle(
        self, device_id: str, bundle_version: str | None = None
    ) -> FirmwareBundle:
        """Map the built-in provider's context data to normalized firmware intent."""
        config_context = await self._get_nautobot_device_config_context(device_id)
        firmware_bundles = config_context.get("firmware_bundles")
        selected_version = bundle_version or config_context.get("firmware_bundle_version")
        if not isinstance(firmware_bundles, dict) or not selected_version:
            raise DCIMNotFoundError("No firmware bundle is assigned to the device")
        bundle = firmware_bundles.get(selected_version)
        if not isinstance(bundle, dict):
            raise DCIMNotFoundError(f"Firmware bundle {selected_version!r} was not found")

        raw_components = bundle.get("firmware", {})
        if not isinstance(raw_components, dict):
            raise DCIMInvalidDataError("Nautobot returned invalid firmware bundle components")
        components = {
            str(name).lower(): FirmwareComponent(
                reported_version=(
                    str(component["reported_version"])
                    if isinstance(component, dict) and component.get("reported_version")
                    else None
                ),
                file_name=(
                    str(component["file"])
                    if isinstance(component, dict) and component.get("file")
                    else None
                ),
                source_path=(
                    str(component["s3_path"])
                    if isinstance(component, dict) and component.get("s3_path")
                    else None
                ),
            )
            for name, component in raw_components.items()
        }
        nv_os = bundle.get("nv_os", {})
        desired_os_version = str(nv_os.get("version", "")) if isinstance(nv_os, dict) else ""
        return FirmwareBundle(
            version=str(selected_version),
            desired_os_version=desired_os_version,
            components=components,
        )

    async def set_device_firmware_intent(
        self, device_id: str, bundle_version: str, desired_os_version: str
    ) -> None:
        """Persist firmware intent through the built-in provider's context representation."""
        await self.merge_config_context(
            device_id,
            {
                "firmware_bundle_version": bundle_version,
                "intended-firmware": {"version": desired_os_version},
            },
        )

    async def get_device(self, fields: str, device_id: str) -> Any:
        """Query device by device UUID."""
        query = render_graphql_fields_template("device_by_id.graphql", fields)
        data = await self.graphql_query(query, {"id": device_id})
        return data["data"]["device"]

    async def get_network_device(self, device_id: str) -> NetworkDeviceData:
        """Get a network device by ID."""
        device = await self.get_device(
            load_graphql_selection("network_device_fields.graphql"), device_id
        )
        return network_device_from_nautobot_graphql(device)

    async def get_host_device(self, device_id: str) -> HostDeviceData:
        """Get a host device by ID."""
        device = await self.get_device(
            load_graphql_selection("host_device_fields.graphql"), device_id
        )
        return host_device_from_nautobot_graphql(device)

    @staticmethod
    def _normalize_to_list(value: str | list[str]) -> list[str]:
        """Convert a single value or list to a list."""
        return [value] if isinstance(value, str) else value

    def _build_device_filter_variables(
        self,
        site: str | list[str] | None,
        status: str | list[str] | None,
        role: str | list[str] | None,
        tenant: str | list[str] | None,
        device_type_id: str | list[str] | None,
        mac_address: str | list[str] | None,
        device_ids: str | list[str] | None,
        platform: Platform | list[Platform] | None,
        managed_only: bool | None,
    ) -> dict[str, list[str] | bool]:
        """Build variables dictionary for device filtering."""
        variables: dict[str, list[str] | bool] = {}

        if site:
            variables["site"] = self._normalize_to_list(site)
        if status:
            variables["status"] = self._normalize_to_list(status)
        if role:
            variables["role"] = self._normalize_to_list(role)
        if tenant:
            variables["tenant"] = self._normalize_to_list(tenant)
        if device_type_id:
            variables["device_type_id"] = self._normalize_to_list(device_type_id)
        if mac_address:
            variables["mac_address"] = self._normalize_to_list(mac_address)
        if device_ids:
            variables["device_ids"] = self._normalize_to_list(device_ids)
        if platform:
            variables["platform"] = (
                [_NAUTOBOT_PLATFORM_NAMES[p] for p in platform]
                if isinstance(platform, list | tuple)
                else [_NAUTOBOT_PLATFORM_NAMES[platform]]
            )
        if managed_only is not None:
            variables["managed_only"] = managed_only

        if not variables:
            raise NautobotException("Must apply at least one filter.")

        return variables

    @staticmethod
    def _deduplicate_devices(devices_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Validate that a device result contains no duplicate names."""
        device_names: set[str] = set()
        duplicates: set[str] = set()

        for device_data in devices_data:
            device_name = device_data["name"]
            if device_name in device_names:
                duplicates.add(device_name)
            else:
                device_names.add(device_name)

        if duplicates:
            raise NautobotException(f"Duplicate device names in nautobot: {duplicates}")

        return devices_data

    async def get_devices(  # pylint: disable=too-many-arguments
        self,
        fields: str,
        site: str | list[str] | None = None,
        status: str | list[str] | None = None,
        role: str | list[str] | None = None,
        tenant: str | list[str] | None = None,
        device_type_id: str | list[str] | None = None,
        mac_address: str | list[str] | None = None,
        device_ids: str | list[str] | None = None,
        platform: Platform | list[Platform] | None = None,
        managed_only: bool | None = None,
    ) -> list[dict[str, Any]]:
        """Return a filtered list of devices."""
        variables = self._build_device_filter_variables(
            site,
            status,
            role,
            tenant,
            device_type_id,
            mac_address,
            device_ids,
            platform,
            managed_only,
        )

        query = render_graphql_fields_template("devices_by_filter.graphql", fields)

        data = await self.graphql_query(query, variables)
        return self._deduplicate_devices(data["data"]["devices"])

    async def get_network_devices(self, filters: DeviceInventoryFilter) -> list[NetworkDeviceData]:
        """Get network devices."""
        fields = load_graphql_selection("network_devices_fields.graphql")
        # Remove filtering parameters that are not supported by get_devices
        device_status_filters = {
            "render_enabled": filters.render_enabled,
            "deploy_enabled": filters.deploy_enabled,
            "backup_enabled": filters.backup_enabled,
            "ztp_enabled": filters.ztp_enabled,
        }

        device_data_list = await self.get_devices(
            fields,
            site=filters.site,
            status=filters.statuses,
            role=filters.roles,
            tenant=filters.tenant,
            device_type_id=filters.device_type_ids,
            mac_address=filters.mac_addresses,
            device_ids=filters.device_ids,
            platform=filters.platforms,
            managed_only=filters.managed_only,
        )
        devices = [network_device_from_nautobot_graphql(data) for data in device_data_list]
        active_filters = {
            attribute: value
            for attribute, value in device_status_filters.items()
            if value is not None
        }
        if active_filters:
            devices = [
                device
                for device in devices
                if all(
                    getattr(device, attribute) == value
                    for attribute, value in active_filters.items()
                )
            ]
        return devices

    async def get_host_devices(self, filters: DeviceInventoryFilter) -> list[HostDeviceData]:
        """Get host devices."""
        fields = load_graphql_selection("host_device_fields.graphql")
        device_data_list = await self.get_devices(
            fields,
            site=filters.site,
            status=filters.statuses,
            role=filters.roles,
            tenant=filters.tenant,
            device_type_id=filters.device_type_ids,
            mac_address=filters.mac_addresses,
            device_ids=filters.device_ids,
            platform=filters.platforms,
            managed_only=filters.managed_only,
        )
        return [host_device_from_nautobot_graphql(data) for data in device_data_list]

    async def get_host_metadata_by_macs(self, mac_addresses: list[str]) -> list[HostMetadata]:
        """Return provider-neutral host metadata for the supplied MAC addresses."""
        data = await self.graphql_query(
            load_graphql_query("workflow/operations.graphql", "GetHostMetadataByMacs"),
            {"macs": mac_addresses},
        )
        return [
            HostMetadata(
                device_id=device["id"],
                name=device["name"],
                tenant=device["tenant"]["name"],
                alias=device.get("cf_alias"),
                interfaces=tuple(
                    HostInterfaceMetadata(
                        name=interface["name"], mac_address=interface["mac_address"]
                    )
                    for interface in device["interfaces"]
                    if interface.get("mac_address")
                ),
            )
            for device in data["data"]["devices"]
        ]

    async def get_host_metadata_by_names(self, device_names: list[str]) -> list[HostMetadata]:
        """Return provider-neutral host metadata for the supplied device names."""
        data = await self.graphql_query(
            load_graphql_query("workflow/operations.graphql", "GetHostMetadataByNames"),
            {"names": device_names},
        )
        return [
            HostMetadata(
                device_id=device["id"],
                name=device["name"],
                tenant=device["tenant"]["name"],
                alias=device.get("cf_alias"),
            )
            for device in data["data"]["devices"]
        ]

    async def get_namespace_route_distinguishers(
        self, site: str, namespace_tag: str
    ) -> list[NamespaceRouteDistinguisher]:
        """Return route distinguisher state for namespaces selected by site and tag."""
        data = await self.graphql_query(
            load_graphql_query("workflow/operations.graphql", "GetNamespaceRouteDistinguishers"),
            {"tag": namespace_tag, "location": site},
        )
        return [
            NamespaceRouteDistinguisher(
                namespace_id=namespace["id"],
                route_distinguishers=tuple(vrf["rd"] for vrf in namespace["vrfs"] if vrf.get("rd")),
            )
            for namespace in data["data"]["namespaces"]
        ]

    async def get_interfaces_by_mac(self, mac_addresses: list[str]) -> list[InterfaceData]:
        """Get a list of interfaces by MAC addresses."""
        interfaces = []
        data = await self.graphql_query(
            load_graphql_query("workflow/operations.graphql", "GetInterfacesByMac"),
            {"mac_address": mac_addresses},
        )
        for interface_data in data["data"]["interfaces"]:
            try:
                interfaces.append(interface_from_nautobot_graphql(interface_data))
            except ApplicationError:
                logger.exception("Error parsing interface %s", interface_data["id"])
                continue
        return interfaces

    async def get_device_interfaces(self, device_id: str) -> list[InterfaceData]:
        """Get interfaces for a device."""
        data = await self.graphql_query(
            load_graphql_query("workflow/operations.graphql", "GetDeviceInterfaces"),
            {"device_id": [device_id]},
        )
        return [
            interface_from_nautobot_graphql(interface) for interface in data["data"]["interfaces"]
        ]

    async def get_device_vrfs(self, device_id: str) -> list[DeviceVrfInfo]:
        """Get VRFs assigned to a device."""
        data = await self.graphql_query(
            load_graphql_query("workflow/operations.graphql", "GetDeviceVRFs"),
            {"device_id": device_id},
        )
        device_data = data["data"]["device"]
        if not device_data:
            raise ApplicationError(f"Device {device_id} not found")

        return [
            DeviceVrfInfo(vrf_id=vrf["id"], vrf_name=vrf["name"]) for vrf in device_data["vrfs"]
        ]

    async def get_connected_switch_port_by_remote_mac(
        self, mac_address: str
    ) -> tuple[NetworkDeviceData, str]:
        """Resolve a switch and connected port from a remote MAC address."""
        data = await self.graphql_query(
            load_graphql_query("workflow/operations.graphql", "GetConnectedSwitchPortByRemoteMac"),
            {"mac": [mac_address]},
        )
        if not data["data"]["interfaces"]:
            raise ApplicationError(f"No interfaces found for MAC {mac_address}")
        try:
            connected_interface = data["data"]["interfaces"][0]["connected_interface"]
            device = await self.get_network_device(connected_interface["device"]["id"])
            return device, connected_interface["name"]
        except KeyError as error:
            raise ApplicationError(f"No connected interface found for MAC {mac_address}") from error

    async def has_recorded_config_drift(self, device_id: str) -> bool:
        """Compare intended and deployed revisions using Nautobot plugin records."""
        data = await self.graphql_query(
            load_graphql_query("workflow/operations.graphql", "GetRecordedConfigDrift"),
            {"id": device_id},
        )
        device_data = data["data"]["config_manager_device"]
        intended_config = device_data["intended_config"]
        backup_config = device_data["backup_config"]
        intended_commit_id = intended_config["commit_id"] if intended_config else None
        deployed_commit_id = backup_config["deployed_commit_id"] if backup_config else None
        return intended_commit_id != deployed_commit_id

    async def load_config_manager_plugin_backup_config(self, device_id: str) -> dict[str, Any]:
        """Load the most recent NVIDIA Config Manager backup data."""
        request_url = self._rest_url(f"{CONFIG_MANAGER_BACKUP_CONFIG_PATH}/{device_id}/")
        session = await self._ensure_session()
        async with session.get(
            request_url,
            headers=self._resolve_headers(),
        ) as rsp:
            if rsp.status == 404:
                # No existing backup to reference
                return {}
            rsp.raise_for_status()
            return cast(dict[str, Any], await rsp.json())

    async def get_configuration_backup_metadata(
        self, device_id: str
    ) -> ConfigurationBackupMetadata | None:
        """Return backup metadata without exposing the Nautobot plugin schema."""
        data = await self.load_config_manager_plugin_backup_config(device_id)
        if not data:
            return None
        return ConfigurationBackupMetadata(
            commit_id=str(data["commit_id"]) if data.get("commit_id") is not None else None,
            deployed_commit_id=(
                str(data["deployed_commit_id"])
                if data.get("deployed_commit_id") not in (None, "")
                else None
            ),
            workflow_id=str(data["workflow_id"]) if data.get("workflow_id") is not None else None,
        )

    async def update_config_manager_plugin_backup_config(  # pylint: disable=too-many-arguments
        self,
        device_id: str,
        config_store_instance: str,
        commit_id: str | int,
        path: str,
        user: str,
        commit_message: str,
        workflow_id: str,
        deployed_commit_id: str | int | None = None,
    ) -> None:
        """Update NVIDIA Config Manager NB Plugin with backup metadata."""
        # TODO: Include config target and type enum
        data = {
            "device_id": device_id,
            "config_store_instance": config_store_instance,
            "path": path,
            "commit_id": commit_id,
            "updated": datetime.now(UTC).isoformat(),
            "updated_by": user,
            "commit_message": commit_message,
            "workflow_id": workflow_id,
            # Plugin has a not-null constraint on this field, empty string is valid
            "deployed_commit_id": deployed_commit_id or "",
        }
        session = await self._ensure_session()
        async with session.post(
            f"{self.rest_endpoint}{CONFIG_MANAGER_BACKUP_CONFIG_PATH}/",
            json=data,
            headers=self._resolve_headers(),
        ) as rsp:
            rsp.raise_for_status()

    async def record_configuration_backup(self, intent: ConfigurationBackupIntent) -> None:
        """Map generic configuration-backup intent to the Nautobot plugin API."""
        await self.update_config_manager_plugin_backup_config(
            intent.device_id,
            intent.config_store_url,
            intent.commit_id,
            intent.filename,
            intent.user,
            intent.commit_message,
            intent.workflow_id,
            intent.deployed_commit_id,
        )

    async def update_interface(self, interface_id: str, data: Any) -> InterfaceData:
        """Update an interface resource."""
        patch_data = await self.patch(path=f"dcim/interfaces/{interface_id}/", data=data)
        return interface_from_nautobot_graphql(patch_data)

    async def assign_vrf_to_interface(self, interface_id: str, vrf_id: str | None) -> None:
        """Assign a VRF by translating the intent to a Nautobot interface update."""
        await self.update_interface(interface_id, data={"vrf": vrf_id})

    async def update_host_device(self, device_id: str, data: Any) -> HostDeviceData:
        """Update a host device resource."""
        patch_data = await self.patch(path=f"dcim/devices/{device_id}/", data=data)
        return host_device_from_nautobot_graphql(patch_data)

    async def find_host_devices_by_mac(self, mac_address: str) -> list[HostDeviceData]:
        """Find host devices by their management MAC address."""
        return await self.get_host_devices(DeviceInventoryFilter(mac_addresses=[mac_address]))

    async def update_dpu_device_inventory(
        self, device_id: str, serial: str, interface_macs: Mapping[str, str]
    ) -> HostDeviceData:
        """Update DPU inventory fields using Nautobot device and interface resources."""
        device = await self.update_host_device(device_id=device_id, data={"serial": serial})
        device.interfaces = [
            await self.update_interface(
                interface_id=interface_id, data={"mac_address": mac_address}
            )
            for interface_id, mac_address in interface_macs.items()
        ]
        return device

    async def get_interface_hosts_by_mac(self, mac_addresses: list[str]) -> list[InterfaceData]:
        """Resolve interface host information for cable-validation results."""
        return await self.get_interfaces_by_mac(mac_addresses)

    async def create_vrf(self, data: Any) -> Any:
        """Create a VRF resource."""
        return await self.post("ipam/vrfs/", data=data)

    async def delete_vrf(self, uuid: str) -> None:
        """Delete a VRF resource."""
        await self.delete(f"ipam/vrfs/{uuid}/")

    async def assign_vrf_to_device(self, device_id: str, vrf_id: str) -> Any:
        """Assign a VRF to a device."""
        return await self.post(
            "ipam/vrf-device-assignments/", data={"device": device_id, "vrf": vrf_id}
        )

    async def lookup_id_by_name(self, path: str, name: str) -> str | None:
        """Return the UUID of a Nautobot object matched by name, or None if not found.

        Raises NautobotException if more than one object matches, to prevent silently
        binding to the wrong ID when names are not globally unique.
        """
        data = await self.get(path, params={"name": name})
        results = data.get("results", [])
        count = data.get("count", len(results))
        if count > 1:
            raise NautobotException(f"Ambiguous name '{name}' at {path}: {count} objects match")
        return cast(str, results[0]["id"]) if results else None

    async def _require_id_by_name(
        self,
        path: str,
        name: str,
        error_message: str,
        *,
        non_retryable: bool = False,
    ) -> str:
        """Look up a Nautobot object ID, raising the caller's established error if absent."""
        object_id = await self.lookup_id_by_name(path, name)
        if object_id is None:
            raise ApplicationError(error_message, non_retryable=non_retryable)
        return object_id

    async def create_overlay(self, data: Any) -> Any:
        """Create an Overlay in the overlays plugin."""
        return await self.post(f"{OVERLAYS_PLUGIN_BASE}/overlays/", data=data)

    async def find_overlay(self, name: str, location_id: str) -> dict[str, Any] | None:
        """Return an existing Overlay matching name + location, or None.

        Raises NautobotException if more than one overlay matches, to prevent
        silently binding to the wrong overlay.
        """
        data = await self.get(
            f"{OVERLAYS_PLUGIN_BASE}/overlays/",
            params={"name": name, "location": location_id},
        )
        results = data.get("results", [])
        count = data.get("count", len(results))
        if count > 1:
            raise NautobotException(
                f"Ambiguous overlay: {count} overlays match name={name!r} location={location_id!r}"
            )
        return cast(dict[str, Any], results[0]) if results else None

    async def get_overlay(self, overlay_id: str) -> dict[str, Any]:
        """Get an Overlay by ID, including its related VXLANs and assignments."""
        return cast(
            dict[str, Any],
            await self.get(f"{OVERLAYS_PLUGIN_BASE}/overlays/{overlay_id}/", params={"depth": 1}),
        )

    async def delete_overlay(self, overlay_id: str) -> None:
        """Delete an Overlay."""
        await self.delete(f"{OVERLAYS_PLUGIN_BASE}/overlays/{overlay_id}/")

    async def create_vxlan(self, data: Any) -> Any:
        """Create a VXLAN in the overlays plugin."""
        return await self.post(f"{OVERLAYS_PLUGIN_BASE}/vxlans/", data=data)

    async def get_vxlans_by_vnid(self, vnid: int) -> list[dict[str, Any]]:
        """Return overlay-plugin VXLANs with the given VNI (namespace resolved via depth)."""
        return await self.get_all(
            f"{OVERLAYS_PLUGIN_BASE}/vxlans/", params={"vnid": vnid, "depth": 1}
        )

    async def get_vxlans_by_overlay(self, overlay_id: str, depth: int = 0) -> list[dict[str, Any]]:
        """Return overlay-plugin VXLANs bound to the given overlay."""
        params: dict[str, Any] = {"overlay": overlay_id}
        if depth:
            params["depth"] = depth
        return await self.get_all(f"{OVERLAYS_PLUGIN_BASE}/vxlans/", params=params)

    async def delete_vxlan(self, vxlan_id: str) -> None:
        """Delete a VXLAN."""
        await self.delete(f"{OVERLAYS_PLUGIN_BASE}/vxlans/{vxlan_id}/")

    async def get_spectrum_x_vrfs(
        self, overlay_name: str, site: str, namespace: str | None = None
    ) -> list[SpectrumXVRF]:
        """Read overlay VRFs without exposing Nautobot overlay-plugin resources."""
        overlay = await self.find_overlay(overlay_name, site)
        if not overlay:
            return []
        vxlans = await self.get_vxlans_by_overlay(str(overlay["id"]), depth=1)
        if namespace:
            vxlans = [
                vxlan for vxlan in vxlans if (vxlan.get("namespace") or {}).get("name") == namespace
            ]
        vrf_ids = [str(vxlan["vrf"]["id"]) for vxlan in vxlans if vxlan.get("vrf")]
        if not vrf_ids:
            return []
        data = await self.graphql_query(_SPECTRUM_X_VRFS_QUERY, {"ids": vrf_ids})
        return [
            SpectrumXVRF(
                vrf_id=vrf["id"],
                name=vrf["name"],
                namespace=vrf["namespace"]["name"],
                site=vrf["namespace"]["location"]["name"],
                route_distinguisher=vrf["rd"],
                interfaces=tuple(
                    f"{interface['device']['name']}:{interface['name']}"
                    for interface in vrf["interfaces"]
                ),
            )
            for vrf in data["data"]["vrfs"]
        ]

    async def delete_spectrum_x_vrf(self, vrf_id: str, vnid: int) -> None:
        """Delete an overlay VRF, its assignments, and its matching L3 VXLANs."""
        vxlans = await self.get_vxlans_by_vnid(vnid)
        for vxlan in vxlans:
            if (vxlan.get("vrf") or {}).get("id") == vrf_id:
                await self.delete_vxlan(str(vxlan["id"]))
        assignments = await self.get_all(
            f"{OVERLAYS_PLUGIN_BASE}/overlay-assignments/",
            params={"assigned_object_id": vrf_id, "depth": 1},
        )
        for assignment in assignments:
            await self.delete(f"{OVERLAYS_PLUGIN_BASE}/overlay-assignments/{assignment['id']}/")
        await self.delete_vrf(vrf_id)

    async def delete_spectrum_x_overlay_if_unused(self, overlay_name: str, site: str) -> bool:
        """Delete a Spectrum-X overlay only after its VXLANs are gone."""
        overlay = await self.find_overlay(overlay_name, site)
        if not overlay:
            return False
        if await self.get_vxlans_by_overlay(str(overlay["id"])):
            return False
        await self.delete_overlay(str(overlay["id"]))
        return True

    @staticmethod
    def _related_object_id(value: Any) -> str | None:
        """Extract a related object ID from Nautobot's REST representation."""
        if isinstance(value, dict):
            object_id = value.get("id")
            return str(object_id) if object_id else None
        return str(value) if value else None

    async def _get_overlay_assignments(self, assigned_object_id: str) -> list[dict[str, Any]]:
        """Read the overlay assignments currently attached to one object."""
        return await self.get_all(
            f"{OVERLAYS_PLUGIN_BASE}/overlay-assignments/",
            params={"assigned_object_id": assigned_object_id, "depth": 1},
        )

    async def _get_overlay_assignment_isolation_type(
        self, assignment: dict[str, Any]
    ) -> str | None:
        """Return the isolation type for an overlay assignment."""
        assignment_overlay = assignment.get("overlay")
        if isinstance(assignment_overlay, dict) and assignment_overlay.get("isolation_type"):
            return str(assignment_overlay["isolation_type"])

        assignment_overlay_id = self._related_object_id(assignment_overlay)
        if not assignment_overlay_id:
            return None
        isolation_type = (await self.get_overlay(assignment_overlay_id)).get("isolation_type")
        return str(isolation_type) if isolation_type else None

    async def _delete_overlay_assignments(self, assignment_ids: list[str]) -> int:
        """Delete overlay assignments and return the count removed."""
        for assignment_id in assignment_ids:
            await self.delete(f"{OVERLAYS_PLUGIN_BASE}/overlay-assignments/{assignment_id}/")
        return len(assignment_ids)

    async def _get_overlay_assignment_status_id(self) -> str:
        """Return the default status ID used for overlay assignments."""
        return await self._require_id_by_name(
            "extras/statuses/",
            _SPECTRUM_X_DEFAULT_STATUS_NAME,
            f"Status {_SPECTRUM_X_DEFAULT_STATUS_NAME} not found for overlay assignment",
        )

    async def _create_overlay_assignment(
        self,
        target_overlay_id: str,
        assigned_object_type: str,
        assigned_object_id: str,
        status_id: str,
    ) -> dict[str, Any]:
        """Create one overlay-plugin assignment."""
        return cast(
            dict[str, Any],
            await self.post(
                f"{OVERLAYS_PLUGIN_BASE}/overlay-assignments/",
                data={
                    "overlay": target_overlay_id,
                    "assigned_object_type": assigned_object_type,
                    "assigned_object_id": assigned_object_id,
                    "status": status_id,
                },
            ),
        )

    async def provision_spectrum_x_vrf(
        self,
        namespaces: list[str],
        route_distinguisher: str,
        vnid: int,
        overlay_name: str,
        site: str,
        tenant: str,
    ) -> None:
        """Provision Spectrum-X overlay, VRF, VXLAN, and VRF assignments."""
        vrf_name = f"SpXTenant{vnid}"
        vrfs_created: list[dict[str, Any]] = []
        vxlans_created: list[dict[str, Any]] = []
        assignments_created: list[dict[str, Any]] = []

        status_id = await self._require_id_by_name(
            "extras/statuses/",
            _SPECTRUM_X_DEFAULT_STATUS_NAME,
            f"Status '{_SPECTRUM_X_DEFAULT_STATUS_NAME}' not found in DCIM",
        )
        tenant_id = await self._require_id_by_name(
            "tenancy/tenants/", tenant, f"Tenant '{tenant}' not found in DCIM"
        )

        overlay = await self.find_overlay(overlay_name, site)
        if overlay:
            existing_tenant = (overlay.get("tenant") or {}).get("id")
            if existing_tenant != tenant_id:
                raise ApplicationError(
                    f"Overlay '{overlay_name}' exists but belongs to tenant "
                    f"{existing_tenant!r}, expected {tenant_id!r}"
                )
            overlay_id = str(overlay["id"])
        else:
            overlay = await self.create_overlay(
                data={
                    "name": overlay_name,
                    "location": site,
                    "tenant": tenant_id,
                    "isolation_type": _SPECTRUM_X_ISOLATION_TYPE,
                    "status": status_id,
                }
            )
            overlay_id = str(overlay["id"])

        try:
            for namespace in namespaces:
                vrf = cast(
                    dict[str, Any],
                    await self.create_vrf(
                        data={
                            "name": vrf_name,
                            "rd": route_distinguisher,
                            "namespace": namespace,
                            "tenant": tenant_id,
                        }
                    ),
                )
                vrfs_created.append(vrf)
                assignments_created.append(
                    await self._create_overlay_assignment(
                        overlay_id,
                        "ipam.vrf",
                        str(vrf["id"]),
                        status_id,
                    )
                )
                vxlans_created.append(
                    cast(
                        dict[str, Any],
                        await self.create_vxlan(
                            data={
                                "vnid": vnid,
                                "name": vrf_name,
                                "vni_type": _SPECTRUM_X_VXLAN_L3_VNI_TYPE,
                                "namespace": namespace,
                                "vrf": vrf["id"],
                                "overlay": overlay_id,
                                "status": status_id,
                            }
                        ),
                    )
                )
        except Exception as error:
            logger.exception("Failed to provision VPC", exc_info=error)
            for vxlan in vxlans_created:
                try:
                    await self.delete_vxlan(str(vxlan["id"]))
                except Exception:
                    logger.exception("Failed to delete VXLAN %s during rollback", vxlan["id"])
            for assignment in assignments_created:
                try:
                    await self._delete_overlay_assignments([str(assignment["id"])])
                except Exception:
                    logger.exception(
                        "Failed to delete overlay assignment %s during rollback",
                        assignment["id"],
                    )
            for vrf in vrfs_created:
                try:
                    await self.delete_vrf(str(vrf["id"]))
                except Exception:
                    logger.exception("Failed to delete VRF %s during rollback", vrf["id"])
            raise ApplicationError("Failed to provision VPC") from error

    async def reconcile_spectrum_x_overlay_assignments(
        self,
        overlay_name: str | None,
        site: str,
        device_id: str,
        interface_ids: list[str],
        device_interface_ids: list[str],
    ) -> tuple[int, int]:
        """Make Spectrum-X device and interface assignments match provider intent."""
        created = 0
        removed = 0
        status_id: str | None = None
        target_overlay_id: str | None = None

        if overlay_name is not None:
            overlay = await self.find_overlay(overlay_name, site)
            if not overlay:
                raise ApplicationError(f"Overlay {overlay_name} not found in site {site}")
            if overlay.get("isolation_type") != _SPECTRUM_X_ISOLATION_TYPE:
                raise ApplicationError(
                    f"Overlay {overlay_name} in site {site} is not a "
                    f"{_SPECTRUM_X_ISOLATION_TYPE} overlay"
                )
            target_overlay_id = str(overlay["id"])

        device_assignments = await self._get_overlay_assignments(device_id)
        if target_overlay_id is not None and not any(
            self._related_object_id(assignment.get("overlay")) == target_overlay_id
            for assignment in device_assignments
        ):
            status_id = await self._get_overlay_assignment_status_id()
            await self._create_overlay_assignment(
                target_overlay_id, "dcim.device", device_id, status_id
            )
            created += 1

        assignments_by_interface: dict[str, list[dict[str, Any]]] = {}
        for interface_id in interface_ids:
            interface_assignments = await self._get_overlay_assignments(interface_id)
            assignments_by_interface[interface_id] = interface_assignments
            stale_ids: list[str] = []
            for assignment in interface_assignments:
                if self._related_object_id(assignment.get("overlay")) == target_overlay_id:
                    continue
                isolation_type = await self._get_overlay_assignment_isolation_type(assignment)
                if isolation_type == _SPECTRUM_X_ISOLATION_TYPE:
                    stale_ids.append(str(assignment["id"]))

            if target_overlay_id is not None and not any(
                self._related_object_id(assignment.get("overlay")) == target_overlay_id
                for assignment in interface_assignments
            ):
                if status_id is None:
                    status_id = await self._get_overlay_assignment_status_id()
                await self._create_overlay_assignment(
                    target_overlay_id, "dcim.interface", interface_id, status_id
                )
                created += 1
                interface_assignments.append(
                    {"id": "created", "overlay": {"id": target_overlay_id}}
                )
            removed += await self._delete_overlay_assignments(stale_ids)
            assignments_by_interface[interface_id] = [
                assignment
                for assignment in interface_assignments
                if str(assignment["id"]) not in stale_ids
            ]

        # Rebuild the complete active interface state so retries also remove
        # device assignments made unused by a prior partial attempt.
        for interface_id in device_interface_ids:
            if interface_id not in assignments_by_interface:
                assignments_by_interface[interface_id] = await self._get_overlay_assignments(
                    interface_id
                )

        active_overlay_ids = {
            overlay_id
            for assignments in assignments_by_interface.values()
            for assignment in assignments
            if (overlay_id := self._related_object_id(assignment.get("overlay"))) is not None
        }
        for assignment in device_assignments:
            overlay_id = self._related_object_id(assignment.get("overlay"))
            if overlay_id is None or overlay_id in active_overlay_ids:
                continue
            isolation_type = await self._get_overlay_assignment_isolation_type(assignment)
            if isolation_type == _SPECTRUM_X_ISOLATION_TYPE:
                removed += await self._delete_overlay_assignments([str(assignment["id"])])

        return created, removed

    async def remove_unmapped_device_vrfs(self, device_id: str, vrf_ids: list[str]) -> list[str]:
        """Remove device VRF associations not used by any device interface."""
        if not vrf_ids:
            return []

        interfaces = await self.get_device_interfaces(device_id)
        mapped_vrf_ids = {interface.vrf_id for interface in interfaces if interface.vrf_id}
        removed_vrf_ids: list[str] = []

        for vrf_id in dict.fromkeys(vrf_ids):
            if vrf_id in mapped_vrf_ids:
                continue

            assignments = await self.get_all(
                "ipam/vrf-device-assignments/",
                params={"device": device_id, "vrf": vrf_id},
            )
            for assignment in assignments:
                if (
                    self._related_object_id(assignment.get("device")) == device_id
                    and self._related_object_id(assignment.get("vrf")) == vrf_id
                ):
                    await self.delete(f"ipam/vrf-device-assignments/{assignment['id']}/")

            # Preserve a stable result when a retry follows a partial attempt.
            removed_vrf_ids.append(vrf_id)

        return removed_vrf_ids

    async def ensure_ib_pkey_partition(
        self,
        pkey: str,
        partition_name: str,
        location_name: str,
        tenant_name: str | None,
        membership_type: str,
    ) -> IBPKeyPartition:
        """Create or reuse a Nautobot overlay and its InfiniBand PKey record."""
        location_id = await self._require_id_by_name(
            "dcim/locations/",
            location_name,
            f"Location '{location_name}' not found in DCIM",
            non_retryable=True,
        )

        tenant_id: str | None = None
        if tenant_name:
            tenant_id = await self._require_id_by_name(
                "tenancy/tenants/",
                tenant_name,
                f"Tenant '{tenant_name}' not found in DCIM",
                non_retryable=True,
            )

        status_id = await self._require_id_by_name(
            "extras/statuses/",
            _SPECTRUM_X_DEFAULT_STATUS_NAME,
            f"Status '{_SPECTRUM_X_DEFAULT_STATUS_NAME}' not found in DCIM",
            non_retryable=True,
        )

        overlay = await self.find_overlay(partition_name, location_id)
        if overlay:
            partition_id = str(overlay["id"])
        else:
            overlay_data: dict[str, Any] = {
                "name": partition_name,
                "location": location_id,
                "isolation_type": _IB_PKEY_ISOLATION_TYPE,
                "status": status_id,
            }
            if tenant_id:
                overlay_data["tenant"] = tenant_id
            overlay = await self.create_overlay(overlay_data)
            partition_id = str(overlay["id"])

        response = await self.get(
            f"{OVERLAYS_PLUGIN_BASE}/pkeys/",
            params={"pkey": pkey, "overlay": partition_id},
        )
        existing = response.get("results", [])
        if existing:
            pkey_id = str(existing[0]["id"])
        else:
            pkey_data: dict[str, Any] = {
                "pkey": pkey,
                "name": f"PKey-{pkey}",
                "overlay": partition_id,
                "membership_type": membership_type,
                "status": status_id,
            }
            if tenant_id:
                pkey_data["tenant"] = tenant_id
            pkey_record = await self.post(f"{OVERLAYS_PLUGIN_BASE}/pkeys/", data=pkey_data)
            pkey_id = str(pkey_record["id"])
        return IBPKeyPartition(
            pkey_id=pkey_id,
            pkey=pkey,
            partition_id=partition_id,
            partition_name=partition_name,
        )

    async def ensure_orphan_ib_pkey(self, pkey: str) -> IBPKeyPartition:
        """Create or reuse a PKey record with no overlay assignment."""
        response = await self.get(f"{OVERLAYS_PLUGIN_BASE}/pkeys/", params={"pkey": pkey})
        orphan_pkeys = [item for item in response.get("results", []) if item.get("overlay") is None]
        if len(orphan_pkeys) > 1:
            details = ", ".join(
                f"id={item.get('id', '<missing>')}, name={item.get('name', '<missing>')}"
                for item in orphan_pkeys
            )
            raise ApplicationError(
                f"Multiple orphan InfiniBandPKey rows found for {pkey}: {details}",
                non_retryable=True,
            )
        if orphan_pkeys:
            pkey_id = str(orphan_pkeys[0]["id"])
        else:
            status_id = await self._require_id_by_name(
                "extras/statuses/",
                _SPECTRUM_X_DEFAULT_STATUS_NAME,
                f"Status '{_SPECTRUM_X_DEFAULT_STATUS_NAME}' not found in DCIM",
                non_retryable=True,
            )
            pkey_record = await self.post(
                f"{OVERLAYS_PLUGIN_BASE}/pkeys/",
                data={"name": f"PKey-{pkey}", "pkey": pkey, "status": status_id},
            )
            pkey_id = str(pkey_record["id"])
        return IBPKeyPartition(pkey_id=pkey_id, pkey=pkey)

    async def get_ib_interface_records(
        self, device_interface_pairs: list[tuple[str, str]]
    ) -> list[IBInterfaceGuid]:
        """Read named IB interfaces using Nautobot's REST filter semantics."""
        records: list[IBInterfaceGuid] = []
        for device_name, interface_name in device_interface_pairs:
            response = await self.get(
                "dcim/interfaces/",
                params={"device": device_name, "name": interface_name},
            )
            for interface in response.get("results", []):
                records.append(
                    IBInterfaceGuid(
                        interface_id=str(interface["id"]),
                        device_name=device_name,
                        interface_name=interface_name,
                        guid=str((interface.get("custom_fields") or {}).get("ib_guid") or ""),
                    )
                )
        return records

    async def find_ib_interfaces_by_guids(self, guids: list[str]) -> list[IBInterfaceGuid]:
        """Find IB interfaces by their GUID custom field."""
        data = await self.graphql_query(_IB_INTERFACES_BY_GUID_QUERY, {"guids": guids})
        return [
            IBInterfaceGuid(
                interface_id=str(interface.get("id") or ""),
                device_name=str((interface.get("device") or {}).get("name") or ""),
                interface_name=str(interface.get("name") or ""),
                guid=str(interface.get("cf_ib_guid") or ""),
            )
            for interface in (data.get("data") or {}).get("interfaces") or []
        ]

    async def _find_ib_pkey_assignment(
        self, overlay_id: str, interface_id: str
    ) -> dict[str, Any] | None:
        """Return the PKey assignment for one interface, when it exists."""
        response = await self.get(
            f"{OVERLAYS_PLUGIN_BASE}/overlay-assignments/",
            params={"overlay": overlay_id, "assigned_object_id": interface_id},
        )
        results = response.get("results", [])
        return cast(dict[str, Any], results[0]) if results else None

    async def ensure_ib_pkey_assignments(
        self,
        overlay_id: str,
        assignments: list[tuple[str, str, str]],
    ) -> list[str]:
        """Create or update PKey membership assignments for the supplied interfaces."""
        status_id = await self._require_id_by_name(
            "extras/statuses/",
            _SPECTRUM_X_DEFAULT_STATUS_NAME,
            f"Status '{_SPECTRUM_X_DEFAULT_STATUS_NAME}' not found in DCIM",
            non_retryable=True,
        )
        assignment_ids: list[str] = []
        for interface_id, guid, membership_type in assignments:
            existing = await self._find_ib_pkey_assignment(overlay_id, interface_id)
            if existing:
                assignment_id = str(existing["id"])
                current_membership = str(existing.get("membership_type") or "full").strip().lower()
                if current_membership != membership_type:
                    await self.patch(
                        f"{OVERLAYS_PLUGIN_BASE}/overlay-assignments/{assignment_id}/",
                        data={"membership_type": membership_type},
                    )
                assignment_ids.append(assignment_id)
                continue
            assignment = await self.post(
                f"{OVERLAYS_PLUGIN_BASE}/overlay-assignments/",
                data={
                    "overlay": overlay_id,
                    "assigned_object_type": "dcim.interface",
                    "assigned_object_id": interface_id,
                    "guid": guid,
                    "membership_type": membership_type,
                    "status": status_id,
                },
            )
            assignment_ids.append(str(assignment["id"]))
        return assignment_ids

    async def remove_ib_pkey_assignments(
        self, overlay_id: str, interface_ids: list[str]
    ) -> tuple[list[str], list[str]]:
        """Remove PKey assignments for the supplied interface IDs."""
        removed: list[str] = []
        unassigned: list[str] = []
        for interface_id in interface_ids:
            existing = await self._find_ib_pkey_assignment(overlay_id, interface_id)
            if not existing:
                unassigned.append(interface_id)
                continue
            assignment_id = str(existing["id"])
            await self.delete(f"{OVERLAYS_PLUGIN_BASE}/overlay-assignments/{assignment_id}/")
            removed.append(assignment_id)
        return removed, unassigned

    async def get_ib_pkey_assignments(self, overlay_id: str) -> list[IBPKeyAssignment]:
        """Return current overlay-plugin PKey assignments for an overlay."""
        items = await self.get_all(
            f"{OVERLAYS_PLUGIN_BASE}/overlay-assignments/",
            params={"overlay": overlay_id},
        )
        return [
            IBPKeyAssignment(
                assignment_id=str(item["id"]),
                interface_id=str(item.get("assigned_object_id") or ""),
                guid=str(item.get("guid") or ""),
                membership_type=str(item.get("membership_type") or "full").strip().lower(),
            )
            for item in items
        ]

    async def sync_ib_pkey_assignments(
        self,
        overlay_id: str,
        desired_assignments: list[tuple[str, str, str]],
    ) -> tuple[list[str], list[str], list[str]]:
        """Reconcile PKey assignments to a normalized desired interface membership list."""
        status_id = await self._require_id_by_name(
            "extras/statuses/",
            _SPECTRUM_X_DEFAULT_STATUS_NAME,
            f"Status '{_SPECTRUM_X_DEFAULT_STATUS_NAME}' not found in DCIM",
            non_retryable=True,
        )
        current_assignments = await self.get_ib_pkey_assignments(overlay_id)
        current_by_interface = {
            assignment.interface_id: assignment for assignment in current_assignments
        }
        desired_by_interface = {
            interface_id: (guid, membership_type)
            for interface_id, guid, membership_type in desired_assignments
        }
        added: list[str] = []
        removed: list[str] = []
        unchanged: list[str] = []

        for interface_id, assignment in current_by_interface.items():
            if interface_id not in desired_by_interface:
                await self.delete(
                    f"{OVERLAYS_PLUGIN_BASE}/overlay-assignments/{assignment.assignment_id}/"
                )
                removed.append(assignment.assignment_id)
                continue
            unchanged.append(assignment.assignment_id)
            _, membership_type = desired_by_interface[interface_id]
            if assignment.membership_type != membership_type:
                await self.patch(
                    f"{OVERLAYS_PLUGIN_BASE}/overlay-assignments/{assignment.assignment_id}/",
                    data={"membership_type": membership_type},
                )

        missing_assignments = [
            (interface_id, guid, membership_type)
            for interface_id, (guid, membership_type) in desired_by_interface.items()
            if interface_id not in current_by_interface
        ]
        for interface_id, guid, membership_type in missing_assignments:
            assignment = await self.post(
                f"{OVERLAYS_PLUGIN_BASE}/overlay-assignments/",
                data={
                    "overlay": overlay_id,
                    "assigned_object_type": "dcim.interface",
                    "assigned_object_id": interface_id,
                    "guid": guid,
                    "membership_type": membership_type,
                    "status": status_id,
                },
            )
            added.append(str(assignment["id"]))
        return added, removed, unchanged

    @staticmethod
    def _normalize_ib_pkey(value: str) -> str:
        """Canonicalize an InfiniBand PKey to four lowercase hexadecimal digits."""
        canonical_value = _canonicalize_pkey_value(value)
        if canonical_value is None:
            raise ApplicationError(
                f"pkey {value!r} does not match required format (e.g. '0x8001')",
                non_retryable=True,
            )
        return canonical_value

    @staticmethod
    def _walk_ib_location_chain(location: dict[str, Any] | None) -> list[dict[str, Any]]:
        """Return the device location followed by each of its parents."""
        chain: list[dict[str, Any]] = []
        current = location
        while current:
            chain.append(current)
            current = current.get("parent")
        return chain

    @staticmethod
    def _find_ib_site(chain: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Return the nearest Site-typed location in an InfiniBand location chain."""
        for location in chain:
            if (location.get("location_type") or {}).get("name") == _IB_PKEY_SITE_LOCATION_TYPE:
                return location
        return None

    @staticmethod
    def _find_ib_pkey_matches(
        chain: list[dict[str, Any]], canonical_pkey: str
    ) -> list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]:
        """Find all overlay PKey records in a location chain matching a PKey."""
        matches: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
        for location in chain:
            for overlay in location.get("overlays") or []:
                for pkey_record in overlay.get("pkeys") or []:
                    stored = str(pkey_record.get("pkey") or "")
                    if _canonicalize_pkey_value(stored) == canonical_pkey:
                        matches.append((location, overlay, pkey_record))
        return matches

    async def _find_ib_pkey_device(self, host: str) -> dict[str, Any]:
        """Resolve a UFM host name or IPv4 address to exactly one Nautobot device."""
        if _IB_PKEY_IPV4_PATTERN.match(host):
            data = await self.graphql_query(_IB_PKEY_RESOLVE_BY_IP_QUERY, {"ip": [host]})
            devices = [
                device
                for ip_record in (data.get("data") or {}).get("ip_addresses") or []
                for interface in ip_record.get("interfaces") or []
                if (device := interface.get("device"))
            ]
            attempted = "IPv4 address"
        else:
            data = await self.graphql_query(_IB_PKEY_RESOLVE_BY_NAME_QUERY, {"host": [host]})
            devices = (data.get("data") or {}).get("devices") or []
            attempted = "name"

        if not devices:
            raise ApplicationError(
                f"UFM device {host!r} not found in Nautobot (tried as {attempted})",
                non_retryable=True,
            )

        by_id = {device["id"]: device for device in devices}
        if len(by_id) > 1:
            raise ApplicationError(
                f"Multiple UFM devices match {host!r}: {sorted(by_id.keys())}",
                non_retryable=True,
            )
        device = next(iter(by_id.values()))
        if (device.get("role") or {}).get("name") != "UFM":
            raise ApplicationError(
                f"Device {device['name']!r} is not assigned the UFM role in Nautobot",
                non_retryable=True,
            )
        return device

    @classmethod
    def _ib_host_site_from_device(cls, device: dict[str, Any]) -> IBHostSite:
        """Translate a Nautobot device and its hierarchy to the public host-site model."""
        chain = cls._walk_ib_location_chain(device.get("location") or {})
        site = cls._find_ib_site(chain)
        if site is None:
            chain_repr = " -> ".join(
                f"{location.get('name', '?')}:"
                f"{(location.get('location_type') or {}).get('name', '?')}"
                for location in chain
            )
            raise ApplicationError(
                f"No {_IB_PKEY_SITE_LOCATION_TYPE}-typed location in hierarchy for device "
                f"{device.get('name')!r}: {chain_repr}",
                non_retryable=True,
            )
        return IBHostSite(
            device_id=str(device["id"]),
            device_name=str(device["name"]),
            device_primary_ip=(device.get("primary_ip4") or {}).get("host"),
            site_id=str(site["id"]),
            site_name=str(site["name"]),
        )

    async def canonicalize_ib_host(self, host: str) -> str:
        """Resolve a UFM host to its primary address when one is modeled."""
        device = await self._find_ib_pkey_device(host)
        return str((device.get("primary_ip4") or {}).get("host") or device["name"])

    async def resolve_ib_host_site(self, host: str) -> IBHostSite:
        """Resolve the managed device and Site that owns a UFM host."""
        return self._ib_host_site_from_device(await self._find_ib_pkey_device(host))

    async def _find_orphan_ib_pkey(self, pkey: str) -> dict[str, Any] | None:
        """Return an unassigned PKey record, rejecting ambiguous provider state."""
        response = await self.get(f"{OVERLAYS_PLUGIN_BASE}/pkeys/", params={"pkey": pkey})
        orphan_pkeys = [item for item in response.get("results", []) if item.get("overlay") is None]
        if len(orphan_pkeys) > 1:
            details = ", ".join(
                f"id={item.get('id', '<missing>')}, name={item.get('name', '<missing>')}"
                for item in orphan_pkeys
            )
            raise ApplicationError(
                f"Multiple orphan InfiniBandPKey rows found for {pkey}: {details}",
                non_retryable=True,
            )
        return cast(dict[str, Any], orphan_pkeys[0]) if orphan_pkeys else None

    async def _ensure_ib_pkey_orphan_overlay(
        self,
        *,
        pkey: str,
        orphan_pkey_id: str,
        location: dict[str, Any],
        tenant_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Create or reuse the provider overlay required to attach an orphan PKey."""
        overlay_name = f"ib-pkey-overlay-{pkey}"
        location_id = str(location["id"])
        overlay = await self.find_overlay(overlay_name, location_id)
        if overlay is None:
            status_id = await self._require_id_by_name(
                "extras/statuses/",
                _SPECTRUM_X_DEFAULT_STATUS_NAME,
                f"Status '{_SPECTRUM_X_DEFAULT_STATUS_NAME}' not found in DCIM",
                non_retryable=True,
            )
            overlay = cast(
                dict[str, Any],
                await self.create_overlay(
                    {
                        "name": overlay_name,
                        "location": location_id,
                        "tenant": tenant_id,
                        "isolation_type": _IB_PKEY_ISOLATION_TYPE,
                        "status": status_id,
                        "description": f"Auto-created for orphan PKey {pkey} during member-add",
                    }
                ),
            )

        pkey_record = cast(
            dict[str, Any], await self.get(f"{OVERLAYS_PLUGIN_BASE}/pkeys/{orphan_pkey_id}/")
        )
        raw_overlay = pkey_record.get("overlay")
        current_overlay_id = raw_overlay["id"] if isinstance(raw_overlay, dict) else raw_overlay
        if current_overlay_id is None:
            pkey_record = cast(
                dict[str, Any],
                await self.patch(
                    f"{OVERLAYS_PLUGIN_BASE}/pkeys/{orphan_pkey_id}/",
                    data={"overlay": overlay["id"]},
                ),
            )
        elif current_overlay_id != overlay["id"]:
            raise ApplicationError(
                f"PKey {pkey!r} (id={orphan_pkey_id}) is already linked to "
                f"Overlay {current_overlay_id!r}; refusing to relink to {overlay['id']!r}. "
                "Unlink the PKey from the other Overlay or use a different PKey value.",
                non_retryable=True,
            )
        return overlay, pkey_record

    async def resolve_ib_pkey_context(
        self, host: str, pkey: str, *, create_overlay_for_orphan: bool = False
    ) -> IBPKeyContext:
        """Resolve a UFM host and PKey to provider-neutral partition context."""
        canonical_pkey = self._normalize_ib_pkey(pkey)
        device = await self._find_ib_pkey_device(host)
        host_site = self._ib_host_site_from_device(device)
        device_location = cast(dict[str, Any], device.get("location") or {})
        matches = self._find_ib_pkey_matches(
            self._walk_ib_location_chain(device_location), canonical_pkey
        )
        if len(matches) > 1:
            candidates = ", ".join(
                f"{location.get('name', '<unnamed>')}/{overlay.get('name', '<unnamed>')}"
                for location, overlay, _ in matches
            )
            raise ApplicationError(
                f"PKey {canonical_pkey!r} ambiguous near location "
                f"{device_location.get('name') or '<unknown>'!r}: matches [{candidates}]. "
                "Resolve the duplicate PKey/Overlay entries in Nautobot before retrying.",
                non_retryable=True,
            )
        if matches:
            _, overlay, pkey_record = matches[0]
        elif create_overlay_for_orphan:
            orphan = await self._find_orphan_ib_pkey(canonical_pkey)
            if orphan is None:
                raise ApplicationError(
                    f"PKey {canonical_pkey!r} not found in Nautobot. Run the IB PKey Creation "
                    "workflow first to register the partition.",
                    non_retryable=True,
                )
            tenant_id = (device.get("tenant") or {}).get("id")
            if not tenant_id:
                raise ApplicationError(
                    f"Device {device.get('name')!r} has no Tenant set; cannot auto-create Overlay "
                    f"for orphan PKey {canonical_pkey}. Set Tenant on the device or pre-create an "
                    f"Overlay and link PKey {canonical_pkey} to it.",
                    non_retryable=True,
                )
            overlay, pkey_record = await self._ensure_ib_pkey_orphan_overlay(
                pkey=canonical_pkey,
                orphan_pkey_id=str(orphan["id"]),
                location=device_location,
                tenant_id=str(tenant_id),
            )
        else:
            raise ApplicationError(
                f"PKey {canonical_pkey!r} not found at or above location "
                f"{device_location.get('name') or '<unknown>'!r}",
                non_retryable=True,
            )

        return IBPKeyContext(
            host_site=host_site,
            overlay_id=str(overlay["id"]),
            overlay_name=str(overlay["name"]),
            pkey_id=str(pkey_record["id"]),
            pkey=canonical_pkey,
        )

    async def _delete_ib_pkey_resource_if_present(self, path: str, description: str) -> None:
        """Delete one provider resource, treating a retry-after-404 as successful."""
        try:
            await self.delete(path)
        except NautobotException as error:
            if error.status_code != 404:
                raise
            logger.info("%s already absent in Nautobot; treating as cleaned", description)

    async def cleanup_ib_pkey_partition(
        self,
        overlay_id: str,
        overlay_name: str,
        pkey_id: str,
        pkey: str,
        ufm_partition_empty: bool,
    ) -> IBPKeyCleanup:
        """Remove stale provider PKey state after UFM confirms a partition is empty."""
        assignments = await self.get(
            f"{OVERLAYS_PLUGIN_BASE}/overlay-assignments/", params={"overlay": overlay_id}
        )
        remaining_assignments = assignments.get("results", [])
        if remaining_assignments or not ufm_partition_empty:
            return IBPKeyCleanup(
                partition_empty=False,
                pkey_deleted=False,
                overlay_deleted=False,
                remaining_assignments=len(remaining_assignments),
            )

        await self._delete_ib_pkey_resource_if_present(
            f"{OVERLAYS_PLUGIN_BASE}/pkeys/{pkey_id}/", f"InfiniBandPKey {pkey_id}"
        )
        overlay_deleted = False
        if overlay_name == f"ib-pkey-overlay-{pkey}":
            pkeys = await self.get(f"{OVERLAYS_PLUGIN_BASE}/pkeys/", params={"overlay": overlay_id})
            if not pkeys.get("results", []):
                await self._delete_ib_pkey_resource_if_present(
                    f"{OVERLAYS_PLUGIN_BASE}/overlays/{overlay_id}/", f"Overlay {overlay_id}"
                )
                overlay_deleted = True
        return IBPKeyCleanup(
            partition_empty=True,
            pkey_deleted=True,
            overlay_deleted=overlay_deleted,
        )

    async def merge_config_context(self, device_id: str, data: Any) -> None:
        """Merge config context data with existing data."""
        device_data = await self.get(f"dcim/devices/{device_id}/", params={"depth": 1})
        config_context = device_data.get("local_config_context_data") or {}
        config_context.update(data)
        await self.patch(
            f"dcim/devices/{device_id}/",
            data={"local_config_context_data": config_context},
        )


class NautobotClient(NautobotWorkflowClient):
    """Deprecated direct provider constructor retained for provider unit tests.

    NVCM services construct the selected broad SDK client through provider
    discovery. This class intentionally accepts only explicit provider
    settings and never reads NVCM configuration.
    """

    def __init__(self, settings: Mapping[str, Any] | None = None) -> None:
        """Create a direct provider client for isolated provider tests."""
        super().__init__(
            settings
            or {
                "server": "https://nautobot.example.com",
                "token": "DUMMY",
                "verify": True,
            }
        )

    def get_device_ui_url(self, device_id: str) -> str:
        """Return the direct provider's user-facing device URL."""
        return f"{self.nautobot_url.rstrip('/')}/dcim/devices/{device_id}/"
