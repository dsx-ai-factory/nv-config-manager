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
"""Backend-neutral data models used by DCIM providers and services."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from nv_config_manager_dcim.errors import DCIMInvalidDataError

VALID_DCIM_EVENT_OPERATIONS = frozenset({"create", "update", "delete"})

DCIM_EVENT_CONTRACT_VERSION = "1.0"
"""Version of the provider-neutral event envelope consumed by services."""


class DCIMModel(BaseModel):
    """Immutable base model for provider-neutral DCIM contracts."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class DeviceMetadata(BaseModel):
    """Normalized metadata for an NVCM-managed device."""

    model_config = ConfigDict(validate_assignment=True)

    device_id: str
    name: str
    site: str
    platform: str | None = None
    role: str | None = None
    rack: str | None = None
    primary_ip4: str | None = None
    device_url: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_device_url(cls, value: Any) -> Any:
        """Accept the legacy Nautobot field while caching only the neutral name."""
        if not isinstance(value, Mapping):
            return value
        normalized = dict(value)
        device_url = normalized.get("device_url")
        nautobot_url = normalized.get("nautobot_url")
        if device_url and nautobot_url and device_url != nautobot_url:
            raise ValueError("device_url and nautobot_url must not conflict")
        if not device_url and nautobot_url:
            normalized["device_url"] = nautobot_url
        normalized.pop("nautobot_url", None)
        return normalized

    @property
    def nautobot_url(self) -> str | None:
        """Return the legacy field name during the compatibility window."""
        return self.device_url

    @nautobot_url.setter
    def nautobot_url(self, value: str | None) -> None:
        self.device_url = value

    def to_dict(self) -> dict[str, Any]:
        """Convert the model to its provider-neutral cache representation."""
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeviceMetadata:
        """Create metadata from a provider-neutral cache representation."""
        return cls.model_validate(data)


class DCIMSelection(DCIMModel):
    """A normalized identifier and display name for a DCIM form option."""

    id: str
    name: str


class DCIMDeviceSelection(DCIMModel):
    """A normalized device option for workflow-form selection."""

    id: str
    name: str
    platform: str | None = None


class DCIMDeviceSelectionFilter(DCIMModel):
    """Provider-neutral device constraints used to populate workflow forms."""

    sites: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    tenants: tuple[str, ...] = ()
    device_type_ids: tuple[str, ...] = ()
    manufacturers: tuple[str, ...] = ()
    platforms: tuple[str, ...] = ()
    managed_only: bool = False


class ZTPDevice(DCIMModel):
    """Normalized DCIM data required to serve a ZTP request."""

    device_id: str
    name: str
    addresses: list[str]
    platform_name: str
    firmware_version: str | None
    config_store_instance: str | None


class RenderDeviceStatus(DCIMModel):
    """The managed-device state used to decide whether to queue a render."""

    render_enabled: bool
    is_aggregate_managed: bool


class IntendedConfigurationUpdate(DCIMModel):
    """The deployable configuration metadata for one managed device."""

    device_id: str
    config_store_instance: str
    path: str
    commit_id: str
    updated: str
    updated_by: str
    commit_message: str
    template_version: str


class RenderTemplateVersion(DCIMModel):
    """The template version last recorded for one managed device."""

    device_id: str
    template_version: str | None


class RenderEventRequest(DCIMModel):
    """One device render requested by a provider-owned event handler."""

    device_id: str
    commit_message: str


class FirmwareComponent(DCIMModel):
    """One component in a normalized device firmware bundle."""

    reported_version: str | None
    file_name: str | None
    source_path: str | None


class FirmwareBundle(DCIMModel):
    """Normalized firmware and OS intent assigned to a managed device."""

    version: str
    desired_os_version: str
    components: Mapping[str, FirmwareComponent]


class ConfigurationBackupMetadata(DCIMModel):
    """Provider-normalized state for the latest device configuration backup."""

    commit_id: str | None
    deployed_commit_id: str | None
    workflow_id: str | None


class ConfigurationBackupIntent(DCIMModel):
    """The configuration-backup metadata a service asks a DCIM to record."""

    device_id: str
    config_store_url: str
    commit_id: str
    filename: str
    user: str
    commit_message: str
    workflow_id: str
    deployed_commit_id: str | None


class HostInterfaceMetadata(DCIMModel):
    """A host interface used to correlate LLDP and MAC-table observations."""

    name: str
    mac_address: str


class HostMetadata(DCIMModel):
    """Provider-neutral host identity and interface metadata."""

    device_id: str
    name: str
    tenant: str
    alias: str | None
    interfaces: tuple[HostInterfaceMetadata, ...] = ()


class NamespaceRouteDistinguisher(DCIMModel):
    """Namespace identity and route distinguishers already in use there."""

    namespace_id: str
    route_distinguishers: tuple[str, ...]


class DeviceVRF(DCIMModel):
    """A VRF assigned to a device."""

    vrf_id: str
    vrf_name: str


class IntendedNeighborDevice(DCIMModel):
    """The modeled remote device for an intended interface connection."""

    name: str
    serial: str | None = None
    role: str | None = None
    rack: str | None = None
    position: int | None = None


class IntendedInterfaceNeighbor(DCIMModel):
    """Normalized intended cable peer data for one local interface."""

    name: str
    tags: tuple[str, ...] = ()
    connected_interface_name: str | None = None
    connected_interface_mac: str | None = None
    connected_device: IntendedNeighborDevice | None = None


class IBNeighbor(DCIMModel):
    """The modeled far end of an InfiniBand switch interface."""

    device_name: str
    interface_name: str


class IBSwitchTopology(DCIMModel):
    """Provider-normalized modeled topology for managed InfiniBand switches."""

    switch_names: Mapping[str, str]
    intended_neighbors: Mapping[str, Mapping[str, IBNeighbor]]


class IBInterfaceGuid(DCIMModel):
    """The current GUID recorded for one compute-side InfiniBand interface."""

    interface_id: str
    device_name: str
    interface_name: str
    guid: str


class SpectrumXVRF(DCIMModel):
    """A Spectrum-X VRF and the interfaces currently attached to it."""

    vrf_id: str
    name: str
    namespace: str
    site: str
    route_distinguisher: str
    interfaces: tuple[str, ...]


class IBPKeyPartition(DCIMModel):
    """A provider-normalized InfiniBand PKey and optional overlay partition."""

    pkey_id: str
    pkey: str
    partition_id: str | None = None
    partition_name: str | None = None


class IBPKeyAssignment(DCIMModel):
    """A provider-normalized InfiniBand PKey membership assignment."""

    assignment_id: str
    interface_id: str
    guid: str
    membership_type: str


class IBHostSite(DCIMModel):
    """The managed InfiniBand device and site that own a UFM host."""

    device_id: str
    device_name: str
    device_primary_ip: str | None
    site_id: str
    site_name: str


class IBPKeyContext(DCIMModel):
    """The InfiniBand PKey partition context resolved for a UFM host."""

    host_site: IBHostSite
    overlay_id: str
    overlay_name: str
    pkey_id: str
    pkey: str


class IBPKeyCleanup(DCIMModel):
    """The provider result of reconciling an empty InfiniBand PKey partition."""

    partition_empty: bool
    pkey_deleted: bool
    overlay_deleted: bool
    remaining_assignments: int = 0


class DCIMChangeEvent(DCIMModel):
    """A provider-neutral DCIM change delivered to an NVCM service."""

    provider: str
    operation: str
    object_type: str
    object_id: str
    timestamp: str
    actor: str = "system"
    record: dict[str, Any] | None = None
    changed_fields: tuple[str, ...] = ()
    correlation_id: str | None = None
    contract_version: str = DCIM_EVENT_CONTRACT_VERSION

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DCIMChangeEvent:
        """Parse and validate a generic provider-neutral event payload."""
        required = ("provider", "operation", "object_type", "object_id", "timestamp")
        missing = [field for field in required if not str(data.get(field, "")).strip()]
        if missing:
            raise DCIMInvalidDataError(
                "DCIM event is missing required field(s): " + ", ".join(missing)
            )
        if str(data.get("contract_version", "")).strip() != DCIM_EVENT_CONTRACT_VERSION:
            raise DCIMInvalidDataError("Unsupported DCIM event contract version")
        operation = str(data["operation"])
        if operation not in VALID_DCIM_EVENT_OPERATIONS:
            raise DCIMInvalidDataError("Invalid DCIM event operation")
        record = data.get("record")
        if record is not None and not isinstance(record, Mapping):
            raise DCIMInvalidDataError("DCIM event record must be an object or null")
        changed_fields = data.get("changed_fields", ())
        if not isinstance(changed_fields, (list, tuple)) or not all(
            isinstance(field, str) for field in changed_fields
        ):
            raise DCIMInvalidDataError("DCIM event changed_fields must be a list of strings")
        correlation_id = data.get("correlation_id")
        if correlation_id is not None and not isinstance(correlation_id, str):
            raise DCIMInvalidDataError("DCIM event correlation_id must be a string")
        return cls(
            provider=str(data["provider"]),
            operation=operation,
            object_type=str(data["object_type"]),
            object_id=str(data["object_id"]),
            timestamp=str(data["timestamp"]),
            actor=str(data.get("actor", "system")),
            record=dict(record) if record is not None else None,
            changed_fields=tuple(changed_fields),
            correlation_id=correlation_id,
            contract_version=DCIM_EVENT_CONTRACT_VERSION,
        )
