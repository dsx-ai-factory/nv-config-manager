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

from pydantic import BaseModel

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


__all__ = [
    "PKEY_MAX",
    "PKEY_MIN",
    "PKEY_RESERVED",
    "AddGuidsInput",
    "AddGuidsOutput",
    "CreatePKeyInput",
    "CreatePKeyOutput",
    "FetchPKeyMembersInput",
    "FetchPKeyMembersOutput",
    "RemoveGuidsInput",
    "RemoveGuidsOutput",
    "SetGuidsInput",
    "SetGuidsOutput",
    "ValidatePKeyInput",
    "ValidatePKeyOutput",
    "VerifyPKeyInput",
    "VerifyPKeyMembersAbsentInput",
    "VerifyPKeyMembersAbsentOutput",
    "VerifyPKeyMembersInput",
    "VerifyPKeyMembersOutput",
    "VerifyPKeyOutput",
]
