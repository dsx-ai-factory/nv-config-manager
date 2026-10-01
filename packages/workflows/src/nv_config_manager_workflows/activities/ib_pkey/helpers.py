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
"""Support functions for InfiniBand PKey activities."""

from typing import Any

from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.activities.ib_pkey.models import PKEY_RESERVED
from nv_config_manager_workflows.clients.ufm import UFMClient, UFMClientError


def validate_memberships_aligned(pkey: str, guids: list[str], memberships: list[str]) -> None:
    """Ensure ``memberships`` is index-aligned with ``guids``."""
    if len(memberships) != len(guids):
        raise ApplicationError(
            f"PKey {pkey}: memberships length ({len(memberships)}) must match "
            f"guids length ({len(guids)})",
            non_retryable=True,
        )


async def get_pkey_state(client: UFMClient, pkey: str) -> tuple[bool, dict[str, str], bool | None]:
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


def parse_pkey_int(pkey_str: str) -> int:
    """Parse a PKey hex string to an integer, stripping the high bit."""
    return int(pkey_str, 16) & 0x7FFF


def extract_pkey_strings(raw: Any) -> list[str]:
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


def find_next_available_pkey(existing: set[int], pkey_min: int, pkey_max: int) -> int | None:
    """Find the lowest available PKey value in the given range."""
    for candidate in range(pkey_min, pkey_max + 1):
        if candidate not in existing and candidate not in PKEY_RESERVED:
            return candidate
    return None


def verify_memberships(
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
