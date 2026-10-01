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
"""Compatibility exports for package-owned InfiniBand PKey activities."""

from nv_config_manager_workflows.activities.ib_pkey import (
    IB_PKEY_ACTIVITIES,
    PKEY_MAX,
    PKEY_MIN,
    PKEY_RESERVED,
    AddGuidsInput,
    AddGuidsOutput,
    CreatePKeyInput,
    CreatePKeyOutput,
    FetchPKeyMembersInput,
    FetchPKeyMembersOutput,
    RemoveGuidsInput,
    RemoveGuidsOutput,
    SetGuidsInput,
    SetGuidsOutput,
    ValidatePKeyInput,
    ValidatePKeyOutput,
    VerifyPKeyInput,
    VerifyPKeyMembersAbsentInput,
    VerifyPKeyMembersAbsentOutput,
    VerifyPKeyMembersInput,
    VerifyPKeyMembersOutput,
    VerifyPKeyOutput,
    add_guids_to_pkey,
    create_pkey_on_ufm,
    fetch_pkey_members,
    remove_guids_from_pkey,
    set_pkey_members,
    validate_pkey_available,
    verify_pkey_created,
    verify_pkey_members,
    verify_pkey_members_absent,
)

__all__ = [
    "IB_PKEY_ACTIVITIES",
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
    "add_guids_to_pkey",
    "create_pkey_on_ufm",
    "fetch_pkey_members",
    "remove_guids_from_pkey",
    "set_pkey_members",
    "validate_pkey_available",
    "verify_pkey_created",
    "verify_pkey_members",
    "verify_pkey_members_absent",
]
