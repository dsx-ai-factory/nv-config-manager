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
"""Versioned public contracts for NVCM DCIM providers."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any, Protocol, runtime_checkable

from nv_config_manager_dcim.models import (
    ConfigurationBackupIntent,
    ConfigurationBackupMetadata,
    DCIMChangeEvent,
    DCIMDeviceSelection,
    DCIMDeviceSelectionFilter,
    DCIMModel,
    DCIMSelection,
    DeviceMetadata,
    DeviceVRF,
    FirmwareBundle,
    HostMetadata,
    IBHostSite,
    IBInterfaceGuid,
    IBPKeyAssignment,
    IBPKeyCleanup,
    IBPKeyContext,
    IBPKeyPartition,
    IBSwitchTopology,
    IntendedConfigurationUpdate,
    IntendedInterfaceNeighbor,
    NamespaceRouteDistinguisher,
    RenderDeviceStatus,
    RenderEventRequest,
    RenderTemplateVersion,
    SpectrumXVRF,
    ZTPDevice,
)
from nv_config_manager_dcim.render import RenderData, RenderDataRequest
from nv_config_manager_dcim.workflow_models import (
    DeviceInventoryFilter,
    HostDeviceData,
    InterfaceData,
    NetworkDeviceData,
    OSImageVersions,
)

DCIM_PROVIDER_API_VERSION = "1.0"
"""The provider API version required by this SDK release."""

ProviderSettings = Mapping[str, Any]
"""Provider-owned settings supplied by an application at its boundary."""


class DCIMProviderMetadata(DCIMModel):
    """Identity and compatibility information published by a provider."""

    name: str
    display_name: str
    provider_version: str
    supported_api_versions: tuple[str, ...]


@runtime_checkable
class DCIMClient(Protocol):
    """One broad, provider-owned DCIM client used by all NVCM services.

    Every operation takes and returns SDK models only. A provider raises
    ``DCIMOperationNotSupportedError`` for an operation it does not support.
    """

    async def __aenter__(self) -> DCIMClient: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None: ...
    async def close(self) -> None: ...

    def is_valid_device_id(self, value: str) -> bool:
        """Return whether value is a provider-canonical device identifier."""
        ...

    def is_valid_location_id(self, value: str) -> bool:
        """Return whether value is a provider-canonical location identifier."""
        ...

    async def get_device_metadata(self, device_id: str) -> DeviceMetadata | None: ...
    async def get_location_metadata(self, location_id: str) -> DCIMSelection | None: ...
    async def get_managed_device_metadata(self, page_size: int = 100) -> list[DeviceMetadata]: ...
    def get_device_ui_url(self, device_id: str) -> str: ...
    async def get_ztp_device(self, device_id: str) -> ZTPDevice: ...
    async def get_device_serial(self, device_id: str) -> str: ...
    async def mark_ztp_device_provisioned(self, device_id: str) -> None: ...
    async def get_render_data(self, request: RenderDataRequest) -> RenderData: ...
    async def get_render_device_status(self, device_id: str) -> RenderDeviceStatus | None: ...
    async def get_render_enabled_device_ids(
        self, is_aggregate_managed: bool | None
    ) -> list[str]: ...
    async def get_render_template_versions(self) -> list[RenderTemplateVersion]: ...
    async def upsert_intended_configuration(self, update: IntendedConfigurationUpdate) -> None: ...
    async def update_render_template_version(
        self, device_id: str, template_version: str
    ) -> None: ...
    async def get_dhcp_site_options(self) -> dict[str, object]: ...
    async def get_dhcp_contexts(
        self, is_aggregate_managed: bool | None = None
    ) -> dict[str, dict[str, object]]: ...
    async def get_dhcp_static_data(self) -> list[dict[str, object]]: ...
    async def get_dhcp_auto_subnets(
        self, family: int = 4, is_aggregate_managed: bool | None = None
    ) -> list[dict[str, object]]: ...
    async def get_intended_interface_neighbors(
        self, device_id: str
    ) -> list[IntendedInterfaceNeighbor]: ...
    async def list_locations(self, location_types: tuple[str, ...] = ()) -> list[DCIMSelection]: ...
    async def list_tenants(self, managed_only: bool = False) -> list[DCIMSelection]: ...
    async def list_roles(self, managed_only: bool = False) -> list[DCIMSelection]: ...
    async def list_namespace_tags(self, location: str | None = None) -> list[str]: ...
    async def list_overlays(
        self, location: str | None = None, isolation_type: str | None = None
    ) -> list[DCIMSelection]: ...
    async def list_statuses(self, content_type: str | None = None) -> list[DCIMSelection]: ...
    async def list_devices(
        self, filters: DCIMDeviceSelectionFilter
    ) -> list[DCIMDeviceSelection]: ...
    async def get_device_selection_by_name(self, name: str) -> DCIMDeviceSelection: ...
    async def get_backup_enabled_device_ids(self, is_aggregate_managed: bool) -> set[str]: ...
    async def get_configuration_backup_metadata(
        self, device_id: str
    ) -> ConfigurationBackupMetadata | None: ...
    async def record_configuration_backup(self, intent: ConfigurationBackupIntent) -> None: ...
    async def get_os_image_versions(self, device_id: str) -> OSImageVersions: ...
    async def set_intended_os_image(self, device_id: str, desired_firmware: str) -> None: ...
    async def get_firmware_bundle(
        self, device_id: str, bundle_version: str | None = None
    ) -> FirmwareBundle: ...
    async def set_device_firmware_intent(
        self, device_id: str, bundle_version: str, desired_os_version: str
    ) -> None: ...
    async def find_host_devices_by_mac(self, mac_address: str) -> list[HostDeviceData]: ...
    async def get_host_device(self, device_id: str) -> HostDeviceData: ...
    async def update_dpu_device_inventory(
        self, device_id: str, serial: str, interface_macs: Mapping[str, str]
    ) -> HostDeviceData: ...
    async def get_interface_hosts_by_mac(self, mac_addresses: list[str]) -> list[InterfaceData]: ...
    async def get_network_device(self, device_id: str) -> NetworkDeviceData: ...
    async def get_network_devices(
        self, filters: DeviceInventoryFilter
    ) -> list[NetworkDeviceData]: ...
    async def get_host_devices(self, filters: DeviceInventoryFilter) -> list[HostDeviceData]: ...
    async def get_host_metadata_by_macs(self, mac_addresses: list[str]) -> list[HostMetadata]: ...
    async def get_host_metadata_by_names(self, device_names: list[str]) -> list[HostMetadata]: ...
    async def get_namespace_route_distinguishers(
        self, site: str, namespace_tag: str
    ) -> list[NamespaceRouteDistinguisher]: ...
    async def get_connected_switch_port_by_remote_mac(
        self, mac_address: str
    ) -> tuple[NetworkDeviceData, str]: ...
    async def has_recorded_config_drift(self, device_id: str) -> bool: ...
    async def get_device_vrfs(self, device_id: str) -> list[DeviceVRF]: ...
    async def get_device_interfaces(self, device_id: str) -> list[InterfaceData]: ...
    async def assign_vrf_to_device(self, device_id: str, vrf_id: str) -> None: ...
    async def assign_vrf_to_interface(self, interface_id: str, vrf_id: str | None) -> None: ...
    async def get_device_password_mapping_users(self, device_id: str) -> set[str]: ...
    async def get_device_secret_versions(self, device_id: str) -> Mapping[str, str]: ...
    async def get_device_password_secret_names(self, device_id: str) -> Mapping[str, str]: ...
    async def get_ib_switch_topology(self, switch_device_ids: list[str]) -> IBSwitchTopology: ...
    async def get_ib_interface_guids(
        self, device_interface_pairs: set[tuple[str, str]]
    ) -> list[IBInterfaceGuid]: ...
    async def get_ib_interface_guid(self, interface_id: str) -> IBInterfaceGuid: ...
    async def set_ib_interface_guid(self, interface_id: str, guid: str) -> None: ...
    async def get_spectrum_x_vrfs(
        self, overlay_name: str, site: str, namespace: str | None = None
    ) -> list[SpectrumXVRF]: ...
    async def delete_spectrum_x_vrf(self, vrf_id: str, vnid: int) -> None: ...
    async def delete_spectrum_x_overlay_if_unused(self, overlay_name: str, site: str) -> bool: ...
    async def provision_spectrum_x_vrf(
        self,
        namespaces: list[str],
        route_distinguisher: str,
        vnid: int,
        overlay_name: str,
        site: str,
        tenant: str,
    ) -> None: ...
    async def reconcile_spectrum_x_overlay_assignments(
        self,
        overlay_name: str | None,
        site: str,
        device_id: str,
        interface_ids: list[str],
        device_interface_ids: list[str],
    ) -> tuple[int, int]: ...
    async def remove_unmapped_device_vrfs(
        self, device_id: str, vrf_ids: list[str]
    ) -> list[str]: ...
    async def ensure_ib_pkey_partition(
        self,
        pkey: str,
        partition_name: str,
        location_name: str,
        tenant_name: str | None,
        membership_type: str,
    ) -> IBPKeyPartition: ...
    async def ensure_orphan_ib_pkey(self, pkey: str) -> IBPKeyPartition: ...
    async def get_ib_interface_records(
        self, device_interface_pairs: list[tuple[str, str]]
    ) -> list[IBInterfaceGuid]: ...
    async def find_ib_interfaces_by_guids(self, guids: list[str]) -> list[IBInterfaceGuid]: ...
    async def ensure_ib_pkey_assignments(
        self, overlay_id: str, assignments: list[tuple[str, str, str]]
    ) -> list[str]: ...
    async def remove_ib_pkey_assignments(
        self, overlay_id: str, interface_ids: list[str]
    ) -> tuple[list[str], list[str]]: ...
    async def get_ib_pkey_assignments(self, overlay_id: str) -> list[IBPKeyAssignment]: ...
    async def sync_ib_pkey_assignments(
        self, overlay_id: str, desired_assignments: list[tuple[str, str, str]]
    ) -> tuple[list[str], list[str], list[str]]: ...
    async def canonicalize_ib_host(self, host: str) -> str: ...
    async def resolve_ib_host_site(self, host: str) -> IBHostSite: ...
    async def resolve_ib_pkey_context(
        self, host: str, pkey: str, *, create_overlay_for_orphan: bool = False
    ) -> IBPKeyContext: ...
    async def cleanup_ib_pkey_partition(
        self, overlay_id: str, overlay_name: str, pkey_id: str, pkey: str, ufm_partition_empty: bool
    ) -> IBPKeyCleanup: ...


@runtime_checkable
class DCIMProvider(Protocol):
    """Factory and lifecycle contract for one installed DCIM provider."""

    metadata: DCIMProviderMetadata

    def validate_settings(self, settings: ProviderSettings) -> None: ...
    def create_client(self, settings: ProviderSettings) -> DCIMClient: ...


@runtime_checkable
class DCIMEventProvider(Protocol):
    """Optional adapter for provider-native event envelopes."""

    def normalize_event(self, payload: Mapping[str, Any]) -> DCIMChangeEvent: ...


class DCIMRenderEventHandler(Protocol):
    """Provider-owned handler identifying renders affected by one event."""

    async def __call__(
        self, event: DCIMChangeEvent, client: DCIMClient
    ) -> Iterable[RenderEventRequest]: ...


@runtime_checkable
class DCIMRenderEventRegistry(Protocol):
    """Registration surface consumed by a service-owned render dispatcher."""

    def register_render_event_handler(
        self, object_type: str, handler: DCIMRenderEventHandler
    ) -> None: ...


@runtime_checkable
class DCIMRenderEventProvider(Protocol):
    """Optional event registration capability."""

    def register_render_event_handlers(self, registry: DCIMRenderEventRegistry) -> None: ...


@runtime_checkable
class NautobotMCPClient(Protocol):
    """Optional adapter for deliberately Nautobot-specific MCP tools."""

    async def close(self) -> None: ...
    async def graphql_query(
        self, query: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]: ...
    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any: ...


@runtime_checkable
class NautobotMCPProvider(Protocol):
    """Optional provider capability for the Nautobot MCP tool surface."""

    def create_nautobot_mcp_client(
        self, settings: ProviderSettings, headers: Callable[[], dict[str, str]]
    ) -> NautobotMCPClient: ...
