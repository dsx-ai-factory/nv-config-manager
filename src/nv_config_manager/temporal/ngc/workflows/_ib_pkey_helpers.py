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
"""Compatibility facade for package-owned InfiniBand PKey workflow helpers."""

from nv_config_manager_workflows.workflows._ib_pkey_helpers import (
    DEFAULT_ACTIVITY_RETRY_POLICY,
    DEFAULT_MEMBERSHIP_TYPE,
    call_resolve_ib_context,
    call_resolve_ib_context_for_add,
    call_resolve_ib_site_for_host,
    normalize_guid_membership_list,
    normalize_membership_type,
    resolve_members,
    validate_guid_memberships,
    validate_interfaces_xor_guids,
    validate_pkey_format,
)

__all__ = [
    "DEFAULT_ACTIVITY_RETRY_POLICY",
    "DEFAULT_MEMBERSHIP_TYPE",
    "call_resolve_ib_context",
    "call_resolve_ib_context_for_add",
    "call_resolve_ib_site_for_host",
    "normalize_guid_membership_list",
    "normalize_membership_type",
    "resolve_members",
    "validate_guid_memberships",
    "validate_interfaces_xor_guids",
    "validate_pkey_format",
]
