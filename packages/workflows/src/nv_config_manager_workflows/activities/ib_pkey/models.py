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
"""Input and output models for InfiniBand PKey activities."""

from __future__ import annotations

from typing import Any

from nv_config_manager_dcim.models import DCIMLocationIdentifier, DCIMLocationType
from pydantic import BaseModel, field_validator

from nv_config_manager_workflows.activities.ib_pkey.normalization import (
    DEFAULT_MEMBERSHIP_TYPE,
    _normalize_membership_override,
)
from nv_config_manager_workflows.stage.models import StageOutput

PKEY_MIN = 0x0001
PKEY_MAX = 0x7FFE
PKEY_RESERVED = {0x7FFF}


class ValidatePKeyInput(BaseModel):
    """Check whether a specific PKey is free, or find the next available one."""

    host: str
    site: str | None = None
    pkey: str | None = None
    pkey_min: int = PKEY_MIN
    pkey_max: int = PKEY_MAX


class ValidatePKeyOutput(StageOutput):
    """Resolved PKey value and whether it was auto-assigned."""

    pkey: str
    auto_assigned: bool
    existing_pkeys: list[str]


class CreatePKeyInput(BaseModel):
    """Parameters for creating a new PKey partition on UFM."""

    host: str
    site: str | None = None
    pkey: str
    ip_over_ib: bool = True
    # Deprecated: UFM auto-generates the management PKey (index0) on init; retained
    # for back-compat but no longer sent to UFM.
    index0: bool | None = None


class CreatePKeyOutput(StageOutput):
    """Confirmation that the PKey partition was created."""

    pkey: str
    created: bool


class VerifyPKeyInput(BaseModel):
    """Parameters for verifying a PKey exists on UFM after creation."""

    host: str
    site: str | None = None
    pkey: str


class VerifyPKeyOutput(StageOutput):
    """Result of a post-creation PKey existence check."""

    pkey: str
    verified: bool
    pkey_data: dict[str, Any]


class AddGuidsInput(BaseModel):
    """Parameters for adding port GUIDs to an existing PKey partition.

    ``memberships`` is index-aligned with ``guids`` (one "full"/"limited" per
    GUID). The activity merges these into the partition's current members and
    issues a single PUT, since UFM's Add endpoint cannot set per-GUID membership.
    """

    host: str
    site: str | None = None
    pkey: str
    guids: list[str]
    memberships: list[str]
    ip_over_ib: bool = True
    # Deprecated: UFM auto-generates the management PKey (index0) on init; retained
    # for back-compat but no longer sent to UFM.
    index0: bool | None = None


class AddGuidsOutput(StageOutput):
    """GUIDs that were added to the PKey."""

    pkey: str
    guids_added: list[str]


class SetGuidsInput(BaseModel):
    """Parameters for atomically setting a PKey's exact GUID membership.

    ``memberships`` is index-aligned with ``guids`` (one "full"/"limited" per
    GUID), the per-port form UFM's Set endpoint (PUT) accepts via the
    ``memberships`` array.
    """

    host: str
    site: str | None = None
    pkey: str
    guids: list[str]
    memberships: list[str]
    ip_over_ib: bool = True
    # Deprecated: UFM auto-generates the management PKey (index0) on init; retained
    # for back-compat but no longer sent to UFM.
    index0: bool | None = None


class SetGuidsOutput(StageOutput):
    """The exact GUID set the PKey was reset to."""

    pkey: str
    guids_set: list[str]
    memberships_set: list[str]


class VerifyPKeyMembersInput(BaseModel):
    """Parameters for checking that expected GUIDs appear in a PKey's member list.

    When ``expected_memberships`` is supplied it is index-aligned with
    ``expected_guids`` and each GUID's membership on UFM is verified too.
    """

    host: str
    site: str | None = None
    pkey: str
    expected_guids: list[str]
    expected_memberships: list[str] | None = None
    exact: bool = False


class VerifyPKeyMembersOutput(StageOutput):
    """Result of a PKey membership verification."""

    pkey: str
    verified: bool
    present_guids: list[str]
    missing_guids: list[str]


class FetchPKeyMembersInput(BaseModel):
    """Parameters for retrieving the current GUID member list of a PKey."""

    host: str
    site: str | None = None
    pkey: str


class FetchPKeyMembersOutput(StageOutput):
    """Current state of a PKey partition on UFM."""

    pkey: str
    exists: bool = True
    guids: list[str]
    memberships: list[str] = []
    ip_over_ib: bool | None = None


class RemoveGuidsInput(BaseModel):
    """Parameters for removing specific port GUIDs from a PKey partition."""

    host: str
    site: str | None = None
    pkey: str
    guids: list[str]


class RemoveGuidsOutput(StageOutput):
    """GUIDs that were removed from the PKey."""

    pkey: str
    guids_removed: list[str]


class VerifyPKeyMembersAbsentInput(BaseModel):
    """Parameters for checking that a list of GUIDs is NOT present in a PKey."""

    host: str
    site: str | None = None
    pkey: str
    forbidden_guids: list[str]


class VerifyPKeyMembersAbsentOutput(StageOutput):
    """Result of a PKey membership-removal verification.

    ``partition_exists`` is False when UFM 404s (the partition is gone), and
    ``remaining_member_count`` reports how many members UFM still holds. Together
    they let the delete workflow decide whether the partition is truly empty
    before reconciling the DCIM, rather than inferring it from DCIM state alone.
    """

    pkey: str
    verified: bool
    still_present_guids: list[str]
    partition_exists: bool = True
    remaining_member_count: int | None = None


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
