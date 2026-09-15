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
"""Provider-neutral activities for InfiniBand overlay management."""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from pydantic import BaseModel, field_validator
from temporalio import activity
from temporalio.exceptions import ApplicationError

from nv_config_manager.dcim import DCIMError, create_dcim_client
from nv_config_manager.temporal.common.mixins.stage import StageOutput

log = logging.getLogger(__name__)

DEFAULT_MEMBERSHIP_TYPE = "full"
_VALID_MEMBERSHIP_TYPES = frozenset({"full", "limited"})

_PKEY_PATTERN = re.compile(r"^0[xX][0-9a-fA-F]{1,4}$")


@asynccontextmanager
async def _dcim_workflow_client() -> AsyncIterator[Any]:
    """Yield the configured client and translate provider errors for Temporal."""
    client = create_dcim_client()
    try:
        async with client:
            yield client
    except DCIMError as error:
        raise ApplicationError(
            str(error),
            non_retryable=bool(getattr(error, "non_retryable", False)),
        ) from error


def normalize_membership_type(membership_type: object) -> str:
    """Normalize membership to 'full'/'limited'.

    None or a blank string defaults to 'full'; any other type or value raises ValueError.
    """
    if membership_type is None:
        return DEFAULT_MEMBERSHIP_TYPE
    if not isinstance(membership_type, str):
        raise ValueError("membership_type must be 'full' or 'limited'")
    normalized = membership_type.strip().lower()
    if not normalized:
        return DEFAULT_MEMBERSHIP_TYPE
    if normalized not in _VALID_MEMBERSHIP_TYPES:
        raise ValueError("membership_type must be 'full' or 'limited'")
    return normalized


def _normalize_membership_override(value: object) -> str | None:
    """Normalize an optional per-port membership override; blank/None stays None."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("membership must be 'full' or 'limited'")
    if not value.strip():
        return None
    return normalize_membership_type(value)


class CreatePartitionInDCIMInput(BaseModel):
    """Parameters for recording an IB overlay partition in the configured DCIM."""

    pkey: str
    partition_name: str | None = None
    location_name: str
    tenant_name: str | None = None
    membership_type: str = "full"


class CreatePartitionInDCIMOutput(StageOutput):
    """DCIM IDs for the created or reused overlay and PKey objects."""

    partition_id: str
    partition_name: str
    pkey_id: str
    pkey: str


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


_RESOLVE_GUIDS_QUERY = """
query ($guids: [String]) {
  interfaces(cf_ib_guid__ie: $guids) {
    id
    name
    cf_ib_guid
    device {
      name
    }
  }
}
"""


def _normalize_ib_guid(guid: str) -> str:
    """Normalize an IB GUID for matching: trim, drop an optional ``0x`` prefix, lowercase.

    UFM and the DCIM store port GUIDs as bare hex (e.g. ``946dae0300598000``),
    but users commonly enter the ``0x``-prefixed form. Normalizing both sides
    lets either representation resolve.
    """
    normalized = (guid or "").strip().lower()
    if normalized.startswith("0x"):
        normalized = normalized[2:]
    return normalized


def _index_resolved_interfaces(
    interfaces: list[dict[str, Any]],
    default_membership: str,
    membership_by_guid: dict[str, str] | None = None,
) -> dict[str, ResolvedInterface]:
    """Group GraphQL interface results by normalized GUID.

    Skips entries with no ``cf_ib_guid`` set. Raises ``ApplicationError`` if
    any GUID has more than one matching interface. Each match takes its per-GUID
    membership from ``membership_by_guid`` (keyed by normalized GUID), falling
    back to ``default_membership``.
    """
    membership_by_guid = membership_by_guid or {}
    grouped: dict[str, list[ResolvedInterface]] = {}
    for iface in interfaces:
        original_guid = iface.get("cf_ib_guid") or ""
        guid_key = _normalize_ib_guid(original_guid)
        if not guid_key:
            continue
        device = (iface.get("device") or {}).get("name") or ""
        grouped.setdefault(guid_key, []).append(
            ResolvedInterface(
                device=device,
                interface=iface.get("name") or "",
                interface_id=iface.get("id") or "",
                guid=original_guid,
                membership=membership_by_guid.get(guid_key, default_membership),
            )
        )

    duplicates = {
        g: [r.interface_id for r in matches] for g, matches in grouped.items() if len(matches) > 1
    }
    if duplicates:
        raise ApplicationError(
            f"GUID(s) matched multiple DCIM interfaces: {duplicates}",
            non_retryable=True,
        )
    return {g: matches[0] for g, matches in grouped.items()}


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


def _is_auto_created_overlay_name(overlay_name: str, pkey: str) -> bool:
    """True when the overlay matches the member-add auto-created naming scheme.

    Auto-created overlays are named ``ib-pkey-overlay-<pkey>`` and exist solely as
    a container for one PKey with no members, so the delete workflow owns their lifecycle.
    Operator- or VPC-owned overlays use other names and are left untouched.
    """
    return overlay_name == f"ib-pkey-overlay-{pkey}"


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
    overlay_id: str
    overlay_name: str
    pkey_id: str
    pkey: str


SITE_LOCATION_TYPE_NAME = "Site"

_RESOLVE_BY_NAME_QUERY = """
query ($host: [String]) {
  devices(name: $host) {
    id
    name
    role { name }
    primary_ip4 { host }
    tenant { id name }
    location {
      id
      name
      location_type { name }
      overlays(isolation_type: ["ib_pkey"]) {
        id
        name
        pkeys {
          id
          pkey
        }
      }
      parent {
        id
        name
        location_type { name }
        overlays(isolation_type: ["ib_pkey"]) {
          id
          name
          pkeys {
            id
            pkey
          }
        }
        parent {
          id
          name
          location_type { name }
          overlays(isolation_type: ["ib_pkey"]) {
            id
            name
            pkeys {
              id
              pkey
            }
          }
        }
      }
    }
  }
}
"""

_RESOLVE_BY_IP_QUERY = """
query ($ip: [String]) {
  ip_addresses(address: $ip) {
    address
    interfaces {
      device {
        id
        name
        role { name }
        primary_ip4 { host }
        tenant { id name }
        location {
          id
          name
          location_type { name }
          overlays(isolation_type: ["ib_pkey"]) {
            id
            name
            pkeys {
              id
              pkey
            }
          }
          parent {
            id
            name
            location_type { name }
            overlays(isolation_type: ["ib_pkey"]) {
              id
              name
              pkeys {
                id
                pkey
              }
            }
            parent {
              id
              name
              location_type { name }
              overlays(isolation_type: ["ib_pkey"]) {
                id
                name
                pkeys {
                  id
                  pkey
                }
              }
            }
          }
        }
      }
    }
  }
}
"""


def _normalize_pkey(value: str) -> str:
    """Canonicalize an IB PKey to '0x' + 4 lowercase hex digits."""
    if not value or not _PKEY_PATTERN.match(value):
        raise ApplicationError(
            f"pkey {value!r} does not match required format (e.g. '0x8001')",
            non_retryable=True,
        )
    return f"0x{int(value, 16):04x}"


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


def _walk_location_chain(location: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return [location, parent, grandparent, ...] until parent is missing."""
    chain: list[dict[str, Any]] = []
    current = location
    while current:
        chain.append(current)
        current = current.get("parent")
    return chain


def _find_site_in_chain(chain: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the first Site-typed location in the chain, or None."""
    for location in chain:
        if (location.get("location_type") or {}).get("name") == SITE_LOCATION_TYPE_NAME:
            return location
    return None


def _require_device_site(device: dict[str, Any]) -> dict[str, Any]:
    """Return the device's Site-typed location or raise a non-retryable error."""
    chain = _walk_location_chain(device.get("location") or {})
    site = _find_site_in_chain(chain)
    if site is not None:
        return site

    chain_repr = " -> ".join(
        f"{loc.get('name', '?')}:{(loc.get('location_type') or {}).get('name', '?')}"
        for loc in chain
    )
    raise ApplicationError(
        f"No {SITE_LOCATION_TYPE_NAME}-typed location in hierarchy for device "
        f"{device.get('name')!r}: {chain_repr}",
        non_retryable=True,
    )


def _iter_pkey_matches(
    chain: list[dict[str, Any]], canonical_pkey: str
) -> list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]:
    """Collect every (location, overlay, pkey_record) triple in the chain matching canonical_pkey."""
    matches: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    for location in chain:
        for overlay in location.get("overlays") or []:
            for pkey_record in overlay.get("pkeys") or []:
                stored = pkey_record.get("pkey") or ""
                if not _PKEY_PATTERN.match(stored):
                    continue
                if f"0x{int(stored, 16):04x}" == canonical_pkey:
                    matches.append((location, overlay, pkey_record))
    return matches


def _select_pkey_match(
    device: dict[str, Any], canonical_pkey: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Select the (location, overlay, pkey_record) triple matching canonical_pkey."""
    device_location = device.get("location") or {}
    chain = _walk_location_chain(device_location)
    matches = _iter_pkey_matches(chain, canonical_pkey)

    device_loc_name = device_location.get("name") or "<unknown>"
    if not matches:
        raise ApplicationError(
            f"PKey {canonical_pkey!r} not found at or above location {device_loc_name!r}",
            non_retryable=True,
        )
    if len(matches) > 1:
        candidates = ", ".join(
            f"{loc.get('name', '<unnamed>')}/{ovl.get('name', '<unnamed>')}"
            for loc, ovl, _ in matches
        )
        raise ApplicationError(
            f"PKey {canonical_pkey!r} ambiguous near location {device_loc_name!r}: "
            f"matches [{candidates}]. Resolve the duplicate PKey/Overlay "
            f"entries in the DCIM before retrying.",
            non_retryable=True,
        )
    return matches[0]


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
        overlay_id=context.overlay_id,
        overlay_name=context.overlay_name,
        pkey_id=context.pkey_id,
        pkey=context.pkey,
        display=f"Resolved {input.host}+{context.pkey} -> overlay {context.overlay_name}",
    )
