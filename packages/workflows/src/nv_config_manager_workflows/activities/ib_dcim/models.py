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
"""Temporal payload models for InfiniBand DCIM activities."""

from nv_config_manager_dcim.models import DCIMLocationIdentifier, DCIMLocationType
from pydantic import BaseModel, field_validator

from nv_config_manager_workflows.activities.ib_dcim.normalization import (
    DEFAULT_MEMBERSHIP_TYPE,
    _normalize_membership_override,
)
from nv_config_manager_workflows.stage import StageOutput


class CreatePartitionInDCIMInput(BaseModel):
    """Parameters for recording an IB overlay partition in the configured DCIM."""

    pkey: str
    partition_name: str | None = None
    location_name: DCIMLocationIdentifier
    tenant_name: str | None = None
    membership_type: str = "full"


class CreatePartitionInDCIMOutput(StageOutput):
    """DCIM IDs for the created or reused overlay and PKey objects."""

    partition_id: str
    partition_name: str
    pkey_id: str
    pkey: str


class RecordIBPKeyInDCIMInput(BaseModel):
    """Parameters for recording an InfiniBandPKey in the configured DCIM."""

    pkey: str


class RecordIBPKeyInDCIMOutput(StageOutput):
    """DCIM ID for the created or reused InfiniBandPKey."""

    pkey_id: str
    pkey: str


class InterfaceRef(BaseModel):
    """A device/interface name pair used to look up an interface in the DCIM.

    ``membership`` is an optional per-port override ("full"/"limited"); when unset
    the caller's workflow-level default is applied.
    """

    device: str
    interface: str
    membership: str | None = None

    @field_validator("membership", mode="before")
    @classmethod
    def _normalize_membership(cls, v: object) -> str | None:
        return _normalize_membership_override(v)


class ResolvedInterface(BaseModel):
    """An interface that has been resolved to its DCIM ID and IB GUID.

    ``membership`` is the effective membership for this port (per-port override
    if supplied, otherwise the workflow default).
    """

    device: str
    interface: str
    interface_id: str
    guid: str
    membership: str = DEFAULT_MEMBERSHIP_TYPE


class ResolveInterfaceGuidsInput(BaseModel):
    """Device/interface pairs to resolve into GUIDs."""

    interfaces: list[InterfaceRef]
    default_membership: str = DEFAULT_MEMBERSHIP_TYPE


class ResolveInterfaceGuidsOutput(StageOutput):
    """Resolved interfaces with their DCIM IDs and IB GUIDs."""

    resolved: list[ResolvedInterface]


class ResolveGuidsToInterfacesInput(BaseModel):
    """A list of IB GUIDs to resolve back to DCIM interface records.

    ``guid_memberships`` is an optional per-GUID membership list index-aligned
    with ``guids``; any GUID without an entry falls back to ``default_membership``.
    """

    guids: list[str]
    default_membership: str = DEFAULT_MEMBERSHIP_TYPE
    guid_memberships: list[str] | None = None


class ResolveGuidsToInterfacesOutput(StageOutput):
    """Interfaces resolved from a list of IB GUIDs."""

    resolved: list[ResolvedInterface]


class RecordPKeyAssignmentsInput(BaseModel):
    """Parameters for creating OverlayAssignment records for a set of resolved interfaces."""

    overlay_id: str
    resolved: list[ResolvedInterface]
    membership_type: str = "full"


class RecordPKeyAssignmentsOutput(StageOutput):
    """IDs of the created or reused OverlayAssignment records."""

    assignment_ids: list[str]


class RemovePKeyAssignmentsInput(BaseModel):
    """Parameters for deleting OverlayAssignment records by interface."""

    overlay_id: str
    interface_ids: list[str]


class RemovePKeyAssignmentsOutput(StageOutput):
    """IDs of the deleted OverlayAssignment records."""

    assignment_ids_removed: list[str]
    interface_ids_not_assigned: list[str]


class CurrentAssignment(BaseModel):
    """A single OverlayAssignment record from the configured DCIM."""

    assignment_id: str
    interface_id: str
    guid: str
    membership_type: str = DEFAULT_MEMBERSHIP_TYPE


class FetchPKeyAssignmentsInput(BaseModel):
    """Overlay ID whose assignments should be fetched."""

    overlay_id: str


class FetchPKeyAssignmentsOutput(StageOutput):
    """Current OverlayAssignment records for the given overlay."""

    assignments: list[CurrentAssignment]


class SyncPKeyAssignmentsInput(BaseModel):
    """Desired state for a PKey overlay's member list."""

    overlay_id: str
    desired: list[ResolvedInterface]
    membership_type: str = "full"


class SyncPKeyAssignmentsOutput(StageOutput):
    """Counts of assignments added, removed, and left unchanged during sync."""

    added: list[str]
    removed: list[str]
    unchanged: list[str]


class CleanupEmptyPartitionInput(BaseModel):
    """Parameters for reconciling the DCIM after a PKey partition empties out.

    ``ufm_partition_empty`` is the verified UFM state (404 or zero members). The
    Provider PKey/Overlay records are only deleted when UFM agrees the partition
    is empty, so untracked UFM-only members cannot be silently orphaned.
    """

    overlay_id: str
    overlay_name: str
    pkey_id: str
    pkey: str
    ufm_partition_empty: bool = False


class CleanupEmptyPartitionOutput(StageOutput):
    """Result of the post-removal DCIM reconciliation."""

    partition_empty: bool
    pkey_deleted: bool
    overlay_deleted: bool


class ResolveIBSiteForHostInput(BaseModel):
    """Inputs for resolving the Site for a UFM host."""

    host: str


class ResolveIBSiteForHostOutput(StageOutput):
    """UFM device + Site context for an IB PKey operation."""

    ufm_device_id: str
    ufm_device_name: str
    ufm_device_primary_ip: str | None
    location_id: str
    location_name: str
    location_type: DCIMLocationType | None = None


class ResolveIBContextInput(BaseModel):
    """Inputs for resolving the DCIM context of an IB PKey operation."""

    host: str
    pkey: str


class ResolveIBContextOutput(StageOutput):
    """UFM device, Site, and overlay context for an IB PKey operation."""

    ufm_device_id: str
    ufm_device_name: str
    ufm_device_primary_ip: str | None
    location_id: str
    location_name: str
    location_type: DCIMLocationType | None = None
    overlay_id: str
    overlay_name: str
    pkey_id: str
    pkey: str
