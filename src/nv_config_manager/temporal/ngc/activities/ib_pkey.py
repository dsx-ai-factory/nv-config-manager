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
"""InfiniBand PKey management activities."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel
from temporalio import activity
from temporalio.exceptions import ApplicationError

from nv_config_manager.temporal.client.ufm import UFMClient, UFMClientError
from nv_config_manager.temporal.common.mixins.stage import StageOutput

log = logging.getLogger(__name__)

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


def _validate_memberships_aligned(pkey: str, guids: list[str], memberships: list[str]) -> None:
    """Ensure ``memberships`` is index-aligned with ``guids``."""
    if len(memberships) != len(guids):
        raise ApplicationError(
            f"PKey {pkey}: memberships length ({len(memberships)}) must match "
            f"guids length ({len(guids)})",
            non_retryable=True,
        )


async def _get_pkey_state(client: UFMClient, pkey: str) -> tuple[bool, dict[str, str], bool | None]:
    """Read a PKey's current members from UFM."""
    try:
        pkey_data = await client.request(
            "GET",
            f"/resources/pkeys/{pkey}",
            params={"guids_data": "true"},
        )
    except UFMClientError as e:
        if e.status_code != 404:
            raise
        return False, {}, None

    if not isinstance(pkey_data, dict):
        raise ApplicationError(
            f"PKey {pkey} returned an unexpected response from UFM",
            non_retryable=True,
        )

    members: dict[str, str] = {}
    for entry in pkey_data.get("guids") or []:
        guid = str(entry.get("guid", "")).lower() if isinstance(entry, dict) else ""
        membership = str(entry.get("membership", "")).lower() if isinstance(entry, dict) else ""
        if not guid or not membership:
            raise ApplicationError(
                f"PKey {pkey}: UFM returned a member with no guid or membership: {entry!r}",
                non_retryable=True,
            )
        members[guid] = membership

    ip_over_ib = pkey_data.get("ip_over_ib")
    return True, members, ip_over_ib if isinstance(ip_over_ib, bool) else None


def _parse_pkey_int(pkey_str: str) -> int:
    """Parse a PKey hex string to an integer, stripping the high bit."""
    return int(pkey_str, 16) & 0x7FFF


def _extract_pkey_strings(raw: Any) -> list[str]:
    """Extract PKey hex strings from the UFM response.

    UFM may return pkeys as a dict (keys are pkey hex strings),
    a list of strings, or a list of dicts with a 'pkey' field.
    """
    if isinstance(raw, dict):
        return [str(k) for k in raw]
    if isinstance(raw, list):
        result: list[str] = []
        for item in raw:
            if isinstance(item, str):
                result.append(item)
            elif isinstance(item, dict) and "pkey" in item:
                result.append(str(item["pkey"]))
        return result
    return []


def _find_next_available_pkey(existing: set[int], pkey_min: int, pkey_max: int) -> int | None:
    """Find the lowest available PKey value in the given range."""
    for candidate in range(pkey_min, pkey_max + 1):
        if candidate not in existing and candidate not in PKEY_RESERVED:
            return candidate
    return None


@activity.defn
async def validate_pkey_available(input: ValidatePKeyInput) -> ValidatePKeyOutput:
    """Validate that the requested PKey is available, or auto-assign the next free one."""
    async with UFMClient(host=input.host, site=input.site) as client:
        raw_pkeys = await client.request("GET", "/resources/pkeys")

    log.debug("UFM pkeys response type=%s, value=%r", type(raw_pkeys).__name__, raw_pkeys)

    existing_keys: list[str] = _extract_pkey_strings(raw_pkeys)
    existing_ints: set[int] = {_parse_pkey_int(k) for k in existing_keys}

    log.debug(
        "Parsed %d existing pkeys: %s",
        len(existing_keys),
        sorted(f"0x{v:04x}" for v in existing_ints),
    )

    if input.pkey is not None:
        requested_int = _parse_pkey_int(input.pkey)
        if requested_int in existing_ints:
            raise ApplicationError(
                f"PKey {input.pkey} is already in use on UFM at {input.host}",
                non_retryable=True,
            )
        chosen = input.pkey
        auto_assigned = False
        log.info("PKey %s is available on %s", chosen, input.host)
    else:
        next_val = _find_next_available_pkey(existing_ints, input.pkey_min, input.pkey_max)
        if next_val is None:
            raise ApplicationError(
                f"No available PKeys in range "
                f"0x{input.pkey_min:04x}-0x{input.pkey_max:04x} "
                f"on UFM at {input.host}",
                non_retryable=True,
            )
        chosen = f"0x{next_val:04x}"
        auto_assigned = True
        log.info("Auto-assigned PKey %s on %s", chosen, input.host)

    return ValidatePKeyOutput(
        pkey=chosen,
        auto_assigned=auto_assigned,
        existing_pkeys=existing_keys,
        display=f"PKey {chosen} is available" + (" (auto-assigned)" if auto_assigned else ""),
    )


@activity.defn
async def create_pkey_on_ufm(input: CreatePKeyInput) -> CreatePKeyOutput:
    """Create a PKey partition on UFM with zero GUID members."""
    payload: dict[str, Any] = {
        "pkey": input.pkey,
        "ip_over_ib": input.ip_over_ib,
    }

    log.info("Creating PKey %s on UFM at %s", input.pkey, input.host)
    log.debug("create payload=%r", payload)

    async with UFMClient(host=input.host, site=input.site) as client:
        result = await client.request("POST", "/resources/pkeys/add", json=payload)

    log.debug("UFM create response: %r", result)

    return CreatePKeyOutput(
        pkey=input.pkey,
        created=True,
        display=f"PKey {input.pkey} created on UFM",
    )


@activity.defn
async def verify_pkey_created(input: VerifyPKeyInput) -> VerifyPKeyOutput:
    """Verify that a PKey exists on UFM after creation."""
    log.info("Verifying PKey %s on UFM at %s", input.pkey, input.host)

    async with UFMClient(host=input.host, site=input.site) as client:
        pkey_data = await client.request("GET", f"/resources/pkeys/{input.pkey}")

    log.debug("UFM verify response: %r", pkey_data)

    if not pkey_data:
        raise ApplicationError(
            f"PKey {input.pkey} not found on UFM at {input.host} after creation",
            non_retryable=True,
        )

    if not isinstance(pkey_data, dict):
        pkey_data = {}

    return VerifyPKeyOutput(
        pkey=input.pkey,
        verified=True,
        pkey_data=pkey_data,
        display=f"PKey {input.pkey} verified on UFM",
    )


@activity.defn
async def add_guids_to_pkey(input: AddGuidsInput) -> AddGuidsOutput:
    """Add port GUIDs to an existing PKey partition on UFM.

    UFM's Add endpoint (POST) cannot assign per-GUID membership in one call and
    silently defaults added members to "full". To assign membership reliably and
    without leaving partial state, this reads the partition's current members, merges
    in the requested GUIDs, and issues a single PUT that UFM applies atomically.
    """
    if not input.guids:
        log.info("No GUIDs to add to PKey %s — skipping", input.pkey)
        return AddGuidsOutput(
            pkey=input.pkey,
            guids_added=[],
            display=f"No GUIDs to add to PKey {input.pkey}",
        )

    _validate_memberships_aligned(input.pkey, input.guids, input.memberships)

    async with UFMClient(host=input.host, site=input.site) as client:
        _, current_members, current_ip_over_ib = await _get_pkey_state(client, input.pkey)

        merged = dict(current_members)
        for guid, membership in zip(input.guids, input.memberships, strict=True):
            merged[guid.lower()] = membership.lower()

        payload: dict[str, Any] = {
            "pkey": input.pkey,
            "guids": list(merged.keys()),
            "memberships": list(merged.values()),
            "ip_over_ib": (
                current_ip_over_ib if current_ip_over_ib is not None else input.ip_over_ib
            ),
        }

        log.info(
            "Adding %d GUID(s) to PKey %s on UFM at %s via merge-and-PUT "
            "(%d existing member(s), %d total after merge): %s",
            len(input.guids),
            input.pkey,
            input.host,
            len(current_members),
            len(merged),
            input.guids,
        )

        await client.request("PUT", "/resources/pkeys/", json=payload)

    return AddGuidsOutput(
        pkey=input.pkey,
        guids_added=input.guids,
        display=f"Added {len(input.guids)} GUID(s) to PKey {input.pkey}",
    )


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


@activity.defn
async def fetch_pkey_members(input: FetchPKeyMembersInput) -> FetchPKeyMembersOutput:
    """Read a PKey partition's current members and settings from UFM.

    Tolerates a 404 by returning ``exists=False`` so callers reconciling against
    UFM can treat a missing partition as empty without special-casing the error.
    """
    log.info("Fetching members for PKey %s on UFM at %s", input.pkey, input.host)

    async with UFMClient(host=input.host, site=input.site) as client:
        exists, members, ip_over_ib = await _get_pkey_state(client, input.pkey)

    if not exists:
        log.info("PKey %s does not exist on UFM", input.pkey)
        return FetchPKeyMembersOutput(
            pkey=input.pkey,
            exists=False,
            guids=[],
            memberships=[],
            ip_over_ib=None,
            display=f"PKey {input.pkey} does not exist on UFM",
        )

    guids = list(members.keys())
    memberships = list(members.values())
    log.info("PKey %s has %d current member(s): %s", input.pkey, len(guids), guids)

    return FetchPKeyMembersOutput(
        pkey=input.pkey,
        exists=True,
        guids=guids,
        memberships=memberships,
        ip_over_ib=ip_over_ib,
        display=f"PKey {input.pkey} has {len(guids)} current member(s)",
    )


@activity.defn
async def remove_guids_from_pkey(input: RemoveGuidsInput) -> RemoveGuidsOutput:
    """Remove specific port GUIDs from an existing PKey partition on UFM.

    No-ops gracefully when the GUID list is empty.
    """
    if not input.guids:
        log.info("No GUIDs to remove from PKey %s — skipping", input.pkey)
        return RemoveGuidsOutput(
            pkey=input.pkey,
            guids_removed=[],
            display=f"No GUIDs to remove from PKey {input.pkey}",
        )

    guids_csv = ",".join(input.guids)
    path = f"/resources/pkeys/{input.pkey}/guids/{guids_csv}"

    log.info(
        "Removing %d GUID(s) from PKey %s on UFM at %s: %s",
        len(input.guids),
        input.pkey,
        input.host,
        input.guids,
    )

    async with UFMClient(host=input.host, site=input.site) as client:
        await client.request("DELETE", path)

    return RemoveGuidsOutput(
        pkey=input.pkey,
        guids_removed=input.guids,
        display=f"Removed {len(input.guids)} GUID(s) from PKey {input.pkey}",
    )


@activity.defn
async def set_pkey_members(input: SetGuidsInput) -> SetGuidsOutput:
    """Atomically replace a PKey's entire GUID membership on UFM."""
    if not input.guids:
        raise ApplicationError(
            f"set_pkey_members requires a non-empty GUID set for PKey {input.pkey}; "
            "emptying a partition is handled by the delete workflow",
            non_retryable=True,
        )

    _validate_memberships_aligned(input.pkey, input.guids, input.memberships)

    payload: dict[str, Any] = {
        "pkey": input.pkey,
        "guids": input.guids,
        "memberships": input.memberships,
        "ip_over_ib": input.ip_over_ib,
    }

    log.info(
        "Setting PKey %s membership on UFM at %s to %d GUID(s): %s (memberships=%s)",
        input.pkey,
        input.host,
        len(input.guids),
        input.guids,
        input.memberships,
    )

    async with UFMClient(host=input.host, site=input.site) as client:
        await client.request("PUT", "/resources/pkeys/", json=payload)

    return SetGuidsOutput(
        pkey=input.pkey,
        guids_set=input.guids,
        memberships_set=input.memberships,
        display=f"Set PKey {input.pkey} membership to {len(input.guids)} GUID(s)",
    )


@activity.defn
async def verify_pkey_members(input: VerifyPKeyMembersInput) -> VerifyPKeyMembersOutput:
    """Verify that all expected GUIDs are present as members of a PKey on UFM."""
    log.info(
        "Verifying %d GUID(s) in PKey %s on UFM at %s",
        len(input.expected_guids),
        input.pkey,
        input.host,
    )

    async with UFMClient(host=input.host, site=input.site) as client:
        pkey_data = await client.request(
            "GET",
            f"/resources/pkeys/{input.pkey}",
            params={"guids_data": "true"},
        )

    log.info("UFM pkey member response: %r", pkey_data)

    if not isinstance(pkey_data, dict):
        raise ApplicationError(
            f"PKey {input.pkey} not found on UFM at {input.host}",
            non_retryable=True,
        )

    raw_guids = pkey_data.get("guids", [])
    present_set = {
        (entry["guid"] if isinstance(entry, dict) else str(entry)).lower() for entry in raw_guids
    }
    expected_set = {g.lower() for g in input.expected_guids}
    missing = sorted(expected_set - present_set)

    if missing:
        raise ApplicationError(
            f"PKey {input.pkey}: {len(missing)} GUID(s) not yet present on UFM: {missing}",
            non_retryable=False,
        )

    if input.exact:
        unexpected = sorted(present_set - expected_set)
        if unexpected:
            raise ApplicationError(
                f"PKey {input.pkey}: {len(unexpected)} unexpected GUID(s) present on UFM: "
                f"{unexpected}",
                non_retryable=False,
            )

    if input.expected_memberships is not None:
        _verify_memberships(
            pkey=input.pkey,
            raw_guids=raw_guids,
            expected_guids=input.expected_guids,
            expected_memberships=input.expected_memberships,
        )

    log.info("All %d GUID(s) verified in PKey %s", len(input.expected_guids), input.pkey)

    return VerifyPKeyMembersOutput(
        pkey=input.pkey,
        verified=True,
        present_guids=sorted(present_set),
        missing_guids=[],
        display=f"All {len(input.expected_guids)} GUID(s) verified in PKey {input.pkey}",
    )


def _verify_memberships(
    pkey: str,
    raw_guids: list[Any],
    expected_guids: list[str],
    expected_memberships: list[str],
) -> None:
    """Raise if any expected GUID's membership on UFM differs from what we set."""
    if len(expected_memberships) != len(expected_guids):
        raise ApplicationError(
            f"PKey {pkey}: expected_memberships length ({len(expected_memberships)}) "
            f"must match expected_guids length ({len(expected_guids)})",
            non_retryable=True,
        )

    membership_by_guid: dict[str, str] = {}
    for entry in raw_guids:
        if isinstance(entry, dict):
            guid = str(entry.get("guid", "")).lower()
            membership_by_guid[guid] = str(entry.get("membership", "")).lower()

    mismatches = {
        guid.lower(): (membership_by_guid.get(guid.lower()), expected.lower())
        for guid, expected in zip(expected_guids, expected_memberships, strict=True)
        if membership_by_guid.get(guid.lower()) != expected.lower()
    }
    if mismatches:
        raise ApplicationError(
            f"PKey {pkey}: membership mismatch on UFM: {mismatches}",
            non_retryable=False,
        )


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


@activity.defn
async def verify_pkey_members_absent(
    input: VerifyPKeyMembersAbsentInput,
) -> VerifyPKeyMembersAbsentOutput:
    """Verify that none of the forbidden GUIDs remain as members of a PKey on UFM."""
    log.info(
        "Verifying %d GUID(s) absent from PKey %s on UFM at %s",
        len(input.forbidden_guids),
        input.pkey,
        input.host,
    )

    try:
        async with UFMClient(host=input.host, site=input.site) as client:
            pkey_data = await client.request(
                "GET",
                f"/resources/pkeys/{input.pkey}",
                params={"guids_data": "true"},
            )
    except UFMClientError as e:
        if e.status_code != 404:
            raise
        # UFM auto-removes a partition once its last member is gone, so a 404
        # here means the forbidden GUIDs are definitively absent.
        log.info("PKey %s no longer exists on UFM; members are absent", input.pkey)
        return VerifyPKeyMembersAbsentOutput(
            pkey=input.pkey,
            verified=True,
            still_present_guids=[],
            partition_exists=False,
            remaining_member_count=0,
            display=f"PKey {input.pkey} no longer exists on UFM; all members absent",
        )

    if not isinstance(pkey_data, dict):
        raise ApplicationError(
            f"PKey {input.pkey} not found on UFM at {input.host}",
            non_retryable=True,
        )

    raw_guids = pkey_data.get("guids", [])
    present_set = {
        (entry["guid"] if isinstance(entry, dict) else str(entry)).lower() for entry in raw_guids
    }
    forbidden_set = {g.lower() for g in input.forbidden_guids}
    still_present = sorted(forbidden_set & present_set)

    if still_present:
        raise ApplicationError(
            f"PKey {input.pkey}: {len(still_present)} GUID(s) still present on UFM: "
            f"{still_present}",
            non_retryable=False,
        )

    return VerifyPKeyMembersAbsentOutput(
        pkey=input.pkey,
        verified=True,
        still_present_guids=[],
        partition_exists=True,
        remaining_member_count=len(present_set),
        display=(
            f"All {len(input.forbidden_guids)} GUID(s) confirmed absent from PKey {input.pkey}"
        ),
    )
