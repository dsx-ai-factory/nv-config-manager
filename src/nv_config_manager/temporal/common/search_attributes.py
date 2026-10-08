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
"""Deprecated import path for :mod:`nv_config_manager_workflows.search_attributes`.

The constants and the upsert helper moved to the standalone workflows package,
which the stage framework needs without the service installed. This module
re-exports them so existing service imports keep resolving; import from
``nv_config_manager_workflows`` in new code.

Note for test authors: ``upsert_missing_search_attributes`` reads ``workflow``
from the package module's globals, so patch
``nv_config_manager_workflows.search_attributes.workflow`` rather than this path.
"""

from nv_config_manager_workflows.search_attributes import (
    DEVICE_ID_SEARCH_ATTRIBUTE,
    DEVICE_NAME_SEARCH_ATTRIBUTE,
    DEVICE_PLATFORM_SEARCH_ATTRIBUTE,
    DEVICE_ROLE_SEARCH_ATTRIBUTE,
    EXECUTE_ROLES_SEARCH_ATTRIBUTE,
    FAILED_STAGE_SEARCH_ATTRIBUTE,
    INITIAL_SEARCH_ATTRIBUTES_PATCH_ID,
    ISSUE_KEY_SEARCH_ATTRIBUTE,
    PENDING_APPROVAL_SEARCH_ATTRIBUTE,
    READ_ROLES_SEARCH_ATTRIBUTE,
    SITE_SEARCH_ATTRIBUTE,
    USER_SEARCH_ATTRIBUTE,
    upsert_missing_search_attributes,
)

__all__ = [
    "DEVICE_ID_SEARCH_ATTRIBUTE",
    "DEVICE_NAME_SEARCH_ATTRIBUTE",
    "DEVICE_PLATFORM_SEARCH_ATTRIBUTE",
    "DEVICE_ROLE_SEARCH_ATTRIBUTE",
    "EXECUTE_ROLES_SEARCH_ATTRIBUTE",
    "FAILED_STAGE_SEARCH_ATTRIBUTE",
    "INITIAL_SEARCH_ATTRIBUTES_PATCH_ID",
    "ISSUE_KEY_SEARCH_ATTRIBUTE",
    "PENDING_APPROVAL_SEARCH_ATTRIBUTE",
    "READ_ROLES_SEARCH_ATTRIBUTE",
    "SITE_SEARCH_ATTRIBUTE",
    "USER_SEARCH_ATTRIBUTE",
    "upsert_missing_search_attributes",
]
