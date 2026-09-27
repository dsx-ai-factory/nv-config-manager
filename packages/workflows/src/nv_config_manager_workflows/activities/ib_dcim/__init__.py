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
"""Public activity facade for InfiniBand overlay management."""

from __future__ import annotations

import logging
from uuid import UUID

from temporalio import activity
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.activities.dcim import dcim_client_session
from nv_config_manager_workflows.activities.ib_dcim.models import (
    CleanupEmptyPartitionInput,
    CleanupEmptyPartitionOutput,
    CreatePartitionInDCIMInput,
    CreatePartitionInDCIMOutput,
    CurrentAssignment,
    DCIMLocationIdentifier,  # noqa: F401 - compatibility re-export
    DCIMLocationType,  # noqa: F401 - compatibility re-export
    FetchPKeyAssignmentsInput,
    FetchPKeyAssignmentsOutput,
    InterfaceRef,  # noqa: F401 - compatibility re-export
    RecordIBPKeyInDCIMInput,
    RecordIBPKeyInDCIMOutput,
    RecordPKeyAssignmentsInput,
    RecordPKeyAssignmentsOutput,
    RemovePKeyAssignmentsInput,
    RemovePKeyAssignmentsOutput,
    ResolvedInterface,
    ResolveGuidsToInterfacesInput,
    ResolveGuidsToInterfacesOutput,
    ResolveIBContextInput,
    ResolveIBContextOutput,
    ResolveIBSiteForHostInput,
    ResolveIBSiteForHostOutput,
    ResolveInterfaceGuidsInput,
    ResolveInterfaceGuidsOutput,
    SyncPKeyAssignmentsInput,
    SyncPKeyAssignmentsOutput,
)
from nv_config_manager_workflows.activities.ib_dcim.normalization import (
    DEFAULT_MEMBERSHIP_TYPE,  # noqa: F401 - compatibility re-export
    normalize_membership_type,
)
from nv_config_manager_workflows.activities.ib_dcim.resolution import (
    SITE_LOCATION_TYPE_NAME,  # noqa: F401 - compatibility re-export
    _index_resolved_interfaces,
    _normalize_ib_guid,
)

log = logging.getLogger(__name__)

# Keep the former private seam patchable through the service compatibility alias.
_dcim_workflow_client = dcim_client_session


@activity.defn
async def create_partition_in_dcim(
    input: CreatePartitionInDCIMInput,
) -> CreatePartitionInDCIMOutput:
    """Create an Overlay and InfiniBandPKey record in the configured DCIM."""
    partition_name = input.partition_name or f"ib-pkey-{input.pkey}"

    async with _dcim_workflow_client() as client:
        partition = await client.ensure_ib_pkey_partition(
            input.pkey,
            partition_name,
            input.location_name,
            input.tenant_name,
            input.membership_type,
        )

    return CreatePartitionInDCIMOutput(
        partition_id=str(partition.partition_id),
        partition_name=str(partition.partition_name),
        pkey_id=partition.pkey_id,
        pkey=partition.pkey,
        display=f"Partition '{partition.partition_name}' and PKey {partition.pkey} recorded in DCIM",
    )


# Retain the activity registered in 1.x workflow histories during the transition.
CreatePartitionInNautobotInput = CreatePartitionInDCIMInput
CreatePartitionInNautobotOutput = CreatePartitionInDCIMOutput


@activity.defn(name="create_partition_in_nautobot")
async def create_partition_in_nautobot(
    input: CreatePartitionInDCIMInput,
) -> CreatePartitionInDCIMOutput:
    """Execute the legacy activity name using the provider-neutral implementation."""
    return await create_partition_in_dcim(input)


@activity.defn
async def record_ib_pkey_in_dcim(
    input: RecordIBPKeyInDCIMInput,
) -> RecordIBPKeyInDCIMOutput:
    """Record an InfiniBandPKey in the configured DCIM."""
    async with _dcim_workflow_client() as client:
        partition = await client.ensure_orphan_ib_pkey(input.pkey)

    return RecordIBPKeyInDCIMOutput(
        pkey_id=partition.pkey_id,
        pkey=partition.pkey,
        display=f"PKey {partition.pkey} recorded in DCIM (id={partition.pkey_id})",
    )


# Retain the activity registered in 1.x workflow histories during the transition.
RecordIBPKeyInNautobotInput = RecordIBPKeyInDCIMInput
RecordIBPKeyInNautobotOutput = RecordIBPKeyInDCIMOutput


@activity.defn(name="record_ib_pkey_in_nautobot")
async def record_ib_pkey_in_nautobot(
    input: RecordIBPKeyInDCIMInput,
) -> RecordIBPKeyInDCIMOutput:
    """Execute the legacy activity name using the provider-neutral implementation."""
    return await record_ib_pkey_in_dcim(input)


@activity.defn
async def resolve_interface_guids(
    input: ResolveInterfaceGuidsInput,
) -> ResolveInterfaceGuidsOutput:
    """Resolve DCIM interface records to their IB GUIDs."""
    resolved: list[ResolvedInterface] = []

    async with _dcim_workflow_client() as client:
        records = await client.get_ib_interface_records(
            [(reference.device, reference.interface) for reference in input.interfaces]
        )
        records_by_name = {
            (record.device_name, record.interface_name): record for record in records
        }
        for ref in input.interfaces:
            record = records_by_name.get((ref.device, ref.interface))
            if record is None:
                raise ApplicationError(
                    f"Interface '{ref.interface}' on device '{ref.device}' not found in DCIM",
                    non_retryable=True,
                )

            guid = record.guid

            if not guid:
                raise ApplicationError(
                    f"Interface '{ref.interface}' on device '{ref.device}' "
                    "has no IB GUID set in DCIM",
                    non_retryable=True,
                )

            resolved.append(
                ResolvedInterface(
                    device=ref.device,
                    interface=ref.interface,
                    interface_id=record.interface_id,
                    guid=guid,
                    membership=ref.membership or input.default_membership,
                )
            )
            log.info(
                "Resolved %s/%s → GUID %s (id=%s)",
                ref.device,
                ref.interface,
                guid,
                record.interface_id,
            )

    return ResolveInterfaceGuidsOutput(
        resolved=resolved,
        display=f"Resolved {len(resolved)} interface GUID(s) from DCIM",
    )


@activity.defn
async def resolve_guids_to_interfaces(
    input: ResolveGuidsToInterfacesInput,
) -> ResolveGuidsToInterfacesOutput:
    """Reverse-lookup IB GUIDs to DCIM interface records.

    Each input GUID must map to exactly one DCIM interface. Missing or duplicate matches raise a
    non-retryable error so the caller can surface the problem directly.
    """
    if not input.guids:
        return ResolveGuidsToInterfacesOutput(
            resolved=[],
            display="No GUIDs to resolve",
        )

    deduped = sorted({_normalize_ib_guid(g) for g in input.guids if _normalize_ib_guid(g)})
    if not deduped:
        raise ApplicationError("All provided GUIDs were empty", non_retryable=True)

    membership_by_guid: dict[str, str] = {}
    if input.guid_memberships is not None:
        if len(input.guid_memberships) != len(input.guids):
            raise ApplicationError(
                f"guid_memberships length ({len(input.guid_memberships)}) must match "
                f"guids length ({len(input.guids)})",
                non_retryable=True,
            )
        for guid, membership in zip(input.guids, input.guid_memberships, strict=True):
            key = _normalize_ib_guid(guid)
            if key:
                membership_by_guid[key] = membership

    async with _dcim_workflow_client() as client:
        records = await client.find_ib_interfaces_by_guids(deduped)

    interfaces = [
        {
            "id": record.interface_id,
            "name": record.interface_name,
            "cf_ib_guid": record.guid,
            "device": {"name": record.device_name},
        }
        for record in records
    ]
    by_guid = _index_resolved_interfaces(interfaces, input.default_membership, membership_by_guid)

    missing = [g for g in deduped if g not in by_guid]
    if missing:
        raise ApplicationError(
            f"No DCIM interface found for GUID(s): {missing}",
            non_retryable=True,
        )

    resolved = [by_guid[g] for g in deduped]
    for r in resolved:
        log.info(
            "Resolved GUID %s → %s/%s (id=%s)",
            r.guid,
            r.device,
            r.interface,
            r.interface_id,
        )

    return ResolveGuidsToInterfacesOutput(
        resolved=resolved,
        display=f"Resolved {len(resolved)} GUID(s) to DCIM interface(s)",
    )


@activity.defn
async def record_pkey_assignments(
    input: RecordPKeyAssignmentsInput,
) -> RecordPKeyAssignmentsOutput:
    """Create OverlayAssignment records in the DCIM for each resolved interface."""

    async with _dcim_workflow_client() as client:
        assignment_ids = await client.ensure_ib_pkey_assignments(
            input.overlay_id,
            [
                (
                    resolved.interface_id,
                    resolved.guid,
                    normalize_membership_type(resolved.membership or input.membership_type),
                )
                for resolved in input.resolved
            ],
        )

    return RecordPKeyAssignmentsOutput(
        assignment_ids=assignment_ids,
        display=(f"Recorded {len(assignment_ids)} OverlayAssignment(s) in DCIM"),
    )


@activity.defn
async def remove_pkey_assignments(
    input: RemovePKeyAssignmentsInput,
) -> RemovePKeyAssignmentsOutput:
    """Delete OverlayAssignment records for the given overlay + interface IDs."""

    async with _dcim_workflow_client() as client:
        removed, not_assigned = await client.remove_ib_pkey_assignments(
            input.overlay_id, input.interface_ids
        )

    return RemovePKeyAssignmentsOutput(
        assignment_ids_removed=removed,
        interface_ids_not_assigned=not_assigned,
        display=(
            f"Removed {len(removed)} OverlayAssignment(s); "
            f"{len(not_assigned)} interface(s) had no assignment"
        ),
    )


@activity.defn
async def cleanup_empty_pkey_partition(
    input: CleanupEmptyPartitionInput,
) -> CleanupEmptyPartitionOutput:
    """Delete an InfiniBandPKey and auto-created Overlay once empty.

    UFM auto-removes a PKey partition when its last member leaves.
    After assignments are removed, this reconciles the DCIM -- but only when the
    UFM partition is also verified empty, so UFM-only members that the provider
    never tracked do not get orphaned as a live partition with no DCIM record.
    If the overlay was auto-created and has no other PKeys, it is also deleted.
    """
    async with _dcim_workflow_client() as client:
        cleanup = await client.cleanup_ib_pkey_partition(
            input.overlay_id,
            input.overlay_name,
            input.pkey_id,
            input.pkey,
            input.ufm_partition_empty,
        )
    if cleanup.remaining_assignments:
        display = (
            f"Overlay {input.overlay_id} still has {cleanup.remaining_assignments} member(s); "
            "leaving PKey and Overlay in place"
        )
    elif not cleanup.partition_empty:
        display = (
            f"PKey {input.pkey} still has untracked members on UFM; "
            "leaving PKey and Overlay in place"
        )
    else:
        deleted = "InfiniBandPKey + Overlay" if cleanup.overlay_deleted else "InfiniBandPKey"
        display = f"Empty PKey partition reconciled; deleted {deleted}"
    return CleanupEmptyPartitionOutput(
        partition_empty=cleanup.partition_empty,
        pkey_deleted=cleanup.pkey_deleted,
        overlay_deleted=cleanup.overlay_deleted,
        display=display,
    )


@activity.defn
async def fetch_pkey_assignments(
    input: FetchPKeyAssignmentsInput,
) -> FetchPKeyAssignmentsOutput:
    """Fetch current OverlayAssignment records for a PKey overlay from the DCIM."""
    async with _dcim_workflow_client() as client:
        provider_assignments = await client.get_ib_pkey_assignments(input.overlay_id)
    assignments = [
        CurrentAssignment(
            assignment_id=assignment.assignment_id,
            interface_id=assignment.interface_id,
            guid=assignment.guid,
            membership_type=normalize_membership_type(assignment.membership_type),
        )
        for assignment in provider_assignments
    ]

    log.info(
        "Found %d existing OverlayAssignment(s) for overlay %s",
        len(assignments),
        input.overlay_id,
    )

    return FetchPKeyAssignmentsOutput(
        assignments=assignments,
        display=f"Found {len(assignments)} existing assignment(s) for overlay {input.overlay_id}",
    )


@activity.defn
async def sync_pkey_assignments(
    input: SyncPKeyAssignmentsInput,
) -> SyncPKeyAssignmentsOutput:
    """Reconcile DCIM OverlayAssignment records to match the desired member list."""

    async with _dcim_workflow_client() as client:
        added, removed, unchanged = await client.sync_ib_pkey_assignments(
            input.overlay_id,
            [
                (
                    resolved.interface_id,
                    resolved.guid,
                    normalize_membership_type(resolved.membership or input.membership_type),
                )
                for resolved in input.desired
            ],
        )

    log.info(
        "OverlayAssignment sync complete: +%d added, -%d removed, %d unchanged",
        len(added),
        len(removed),
        len(unchanged),
    )

    return SyncPKeyAssignmentsOutput(
        added=added,
        removed=removed,
        unchanged=unchanged,
        display=(
            f"DCIM assignments synced: "
            f"+{len(added)} added, -{len(removed)} removed, {len(unchanged)} unchanged"
        ),
    )


# ---------------------------------------------------------------------------
# IB context resolver
#
# Lets clients call ib_pkey_member_{add,delete,update} with just (host, pkey)
# and have the workflow derive the location and overlay from the DCIM.
# ---------------------------------------------------------------------------


async def canonicalize_ufm_host(host: str) -> str:
    """Resolve a UFM host (device name or IPv4) to one identifier."""
    async with _dcim_workflow_client() as client:
        return str(await client.canonicalize_ib_host(host))


async def canonicalize_ufm_host_for_site(host: str, site_reference: str | None) -> str:
    """Resolve an API-supplied UFM host and verify its optional Site reference."""
    async with _dcim_workflow_client() as client:
        host_site = await client.resolve_ib_host_site(host)
    canonical_host = str(host_site.device_primary_ip or host_site.device_name)

    normalized_reference = site_reference
    if site_reference is not None:
        try:
            normalized_reference = str(UUID(site_reference))
        except ValueError:
            pass
    if normalized_reference is not None and normalized_reference not in {
        host_site.site_id,
        host_site.site_name,
    }:
        raise ApplicationError(
            f"UFM device {host_site.device_name!r} belongs to Site {host_site.site_name!r}, "
            f"not {site_reference!r}",
            non_retryable=True,
        )
    return canonical_host


@activity.defn
async def resolve_ib_site_for_host(
    input: ResolveIBSiteForHostInput,
) -> ResolveIBSiteForHostOutput:
    """Resolve the Site for a UFM host. Allows site specific UFM credentials."""

    async with _dcim_workflow_client() as client:
        host_site = await client.resolve_ib_host_site(input.host)

    log.info(
        "Resolved IB site for host=%s -> device=%s site=%s",
        input.host,
        host_site.device_name,
        host_site.site_name,
    )

    return ResolveIBSiteForHostOutput(
        ufm_device_id=host_site.device_id,
        ufm_device_name=host_site.device_name,
        ufm_device_primary_ip=host_site.device_primary_ip,
        location_id=host_site.site_id,
        location_name=host_site.site_name,
        location_type=host_site.site_type,
        display=f"Resolved {input.host} -> site {host_site.site_name}",
    )


@activity.defn
async def resolve_ib_context(
    input: ResolveIBContextInput,
) -> ResolveIBContextOutput:
    """Resolve UFM device, location, overlay, and PKey records from (host, pkey)."""
    async with _dcim_workflow_client() as client:
        context = await client.resolve_ib_pkey_context(input.host, input.pkey)

    log.info(
        "Resolved IB context for host=%s pkey=%s -> device=%s site=%s overlay=%s",
        input.host,
        context.pkey,
        context.host_site.device_name,
        context.host_site.site_name,
        context.overlay_name,
    )

    return ResolveIBContextOutput(
        ufm_device_id=context.host_site.device_id,
        ufm_device_name=context.host_site.device_name,
        ufm_device_primary_ip=context.host_site.device_primary_ip,
        location_id=context.host_site.site_id,
        location_name=context.host_site.site_name,
        location_type=context.host_site.site_type,
        overlay_id=context.overlay_id,
        overlay_name=context.overlay_name,
        pkey_id=context.pkey_id,
        pkey=context.pkey,
        display=f"Resolved {input.host}+{context.pkey} -> overlay {context.overlay_name}",
    )


@activity.defn
async def resolve_ib_context_for_add(
    input: ResolveIBContextInput,
) -> ResolveIBContextOutput:
    """Resolve UFM/site/overlay/pkey for member-add with lazy Overlay creation."""
    async with _dcim_workflow_client() as client:
        context = await client.resolve_ib_pkey_context(
            input.host, input.pkey, create_overlay_for_orphan=True
        )

    log.info(
        "Resolved IB context (with lazy-create) for host=%s pkey=%s -> "
        "device=%s site=%s overlay=%s",
        input.host,
        context.pkey,
        context.host_site.device_name,
        context.host_site.site_name,
        context.overlay_name,
    )

    return ResolveIBContextOutput(
        ufm_device_id=context.host_site.device_id,
        ufm_device_name=context.host_site.device_name,
        ufm_device_primary_ip=context.host_site.device_primary_ip,
        location_id=context.host_site.site_id,
        location_name=context.host_site.site_name,
        location_type=context.host_site.site_type,
        overlay_id=context.overlay_id,
        overlay_name=context.overlay_name,
        pkey_id=context.pkey_id,
        pkey=context.pkey,
        display=f"Resolved {input.host}+{context.pkey} -> overlay {context.overlay_name}",
    )


IB_DCIM_ACTIVITIES = (
    record_ib_pkey_in_dcim,
    record_ib_pkey_in_nautobot,
    create_partition_in_dcim,
    create_partition_in_nautobot,
    resolve_interface_guids,
    resolve_guids_to_interfaces,
    resolve_ib_context,
    resolve_ib_context_for_add,
    resolve_ib_site_for_host,
    record_pkey_assignments,
    fetch_pkey_assignments,
    sync_pkey_assignments,
    remove_pkey_assignments,
    cleanup_empty_pkey_partition,
)
