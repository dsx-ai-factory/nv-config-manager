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
"""Tests for replay-safe workflow search attribute helpers."""

from unittest.mock import MagicMock, patch

from nv_config_manager.temporal.common.search_attributes import (
    upsert_missing_search_attributes,
)


@patch("nv_config_manager_workflows.search_attributes.workflow")
def test_old_workflow_histories_retain_all_upserts(mock_workflow: MagicMock) -> None:
    """Histories without the patch marker must replay their original upsert."""
    mock_workflow.patched.return_value = False
    attributes = {"Site": ["SJC01"]}

    upsert_missing_search_attributes(attributes)

    mock_workflow.info.assert_not_called()
    mock_workflow.upsert_search_attributes.assert_called_once_with(attributes)


@patch("nv_config_manager_workflows.search_attributes.workflow")
def test_new_workflows_skip_attributes_attached_at_start(mock_workflow: MagicMock) -> None:
    """New workflows must not overwrite canonical attributes set by the API."""
    mock_workflow.patched.return_value = True
    mock_workflow.info.return_value.search_attributes = {"Site": ["SJC01"]}

    upsert_missing_search_attributes({"Site": ["location-uuid"]})

    mock_workflow.upsert_search_attributes.assert_not_called()


@patch("nv_config_manager_workflows.search_attributes.workflow")
def test_new_workflows_upsert_only_missing_attributes(mock_workflow: MagicMock) -> None:
    """Direct starts and partial initial metadata retain workflow fallbacks."""
    mock_workflow.patched.return_value = True
    mock_workflow.info.return_value.search_attributes = {"DeviceID": ["device-uuid"]}

    upsert_missing_search_attributes({"DeviceID": ["device-uuid"], "DeviceName": ["LEAF01"]})

    mock_workflow.upsert_search_attributes.assert_called_once_with({"DeviceName": ["LEAF01"]})
