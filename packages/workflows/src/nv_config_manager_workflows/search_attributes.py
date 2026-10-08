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
"""Temporal search attribute names used by NVIDIA Config Manager."""

from typing import Any

from temporalio import workflow

INITIAL_SEARCH_ATTRIBUTES_PATCH_ID = "initial-search-attributes-v1"

DEVICE_ID_SEARCH_ATTRIBUTE = "DeviceID"
DEVICE_NAME_SEARCH_ATTRIBUTE = "DeviceName"
DEVICE_PLATFORM_SEARCH_ATTRIBUTE = "DevicePlatform"
DEVICE_ROLE_SEARCH_ATTRIBUTE = "DeviceRole"
EXECUTE_ROLES_SEARCH_ATTRIBUTE = "ExecuteRoles"
FAILED_STAGE_SEARCH_ATTRIBUTE = "FailedStage"
ISSUE_KEY_SEARCH_ATTRIBUTE = "IssueKey"
PENDING_APPROVAL_SEARCH_ATTRIBUTE = "PendingApproval"
READ_ROLES_SEARCH_ATTRIBUTE = "ReadRoles"
SITE_SEARCH_ATTRIBUTE = "Site"
USER_SEARCH_ATTRIBUTE = "User"


def upsert_missing_search_attributes(attributes: dict[str, list[Any]]) -> None:
    """Upsert attributes not already attached when the workflow was started."""
    if workflow.patched(INITIAL_SEARCH_ATTRIBUTES_PATCH_ID):
        initial_attributes = workflow.info().search_attributes
        attributes = {
            name: value for name, value in attributes.items() if name not in initial_attributes
        }
    if attributes:
        workflow.upsert_search_attributes(attributes)
