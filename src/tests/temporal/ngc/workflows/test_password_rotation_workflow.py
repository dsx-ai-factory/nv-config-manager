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
# pylint: disable=B101,C0115,C0116
"""Test Suite for Password Rotation Workflows"""

import pytest
from pydantic import ValidationError
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from nv_config_manager_dcim_nautobot_2x.workflow_models import (
        network_device_from_nautobot_graphql,
    )

    from nv_config_manager.temporal.ngc.workflows.site_password_rotation import (
        PasswordRotationResultData,
        SitePasswordRotationInput,
    )


# Test data
TEST_DEVICE_DATA = {
    "id": "device-1-uuid",
    "name": "rno1-tor-001",
    "role": {"name": "TAN-Leaf"},
    "location": {"location_type": {"name": "Site"}, "name": "rno1"},
    "device_type": {"model": "SN2010"},
    "platform": {"name": "Cumulus Linux"},
    "primary_ip4": {"host": "10.1.1.1"},
    "primary_ip6": None,
    "rack": {"name": "a01"},
    "position": 1,
    "configmanagerdevicestatus": {
        "render_enabled": True,
        "deploy_enabled": True,
        "backup_enabled": True,
        "ztp_enabled": True,
    },
    "config_context": {
        "password_mappings": {
            "cumulus": {"password": "root_password", "role": "system-admin", "rotation": "r1"}
        }
    },
}


class TestSitePasswordRotationInput:
    """Tests for SitePasswordRotationInput validation."""

    def test_location_must_not_be_empty(self):
        """Reject an empty location before starting the workflow."""
        with pytest.raises(ValidationError):
            SitePasswordRotationInput(location="", selected_secret="device-password")


class TestPasswordRotationResultData:
    """Tests for PasswordRotationResultData model."""

    def test_password_rotation_result_data_success(self):
        """Test successful password rotation result."""
        device = network_device_from_nautobot_graphql(TEST_DEVICE_DATA)
        result = PasswordRotationResultData(
            device=device,
            success=True,
            child_workflow_id="workflow-123",
        )
        assert result.device.name == "rno1-tor-001"
        assert result.success is True
        assert result.error is None
        assert result.child_workflow_id == "workflow-123"

    def test_password_rotation_result_data_failure(self):
        """Test failed password rotation result."""
        device = network_device_from_nautobot_graphql(TEST_DEVICE_DATA)
        result = PasswordRotationResultData(
            device=device,
            success=False,
            error="Password rotation failed",
            child_workflow_id="workflow-456",
        )
        assert result.device.name == "rno1-tor-001"
        assert result.success is False
        assert result.error == "Password rotation failed"
        assert result.child_workflow_id == "workflow-456"

    def test_password_rotation_result_data_no_device(self):
        """Test password rotation result with no device data."""
        result = PasswordRotationResultData(
            device=None,
            success=False,
            error="Device not found",
            child_workflow_id="workflow-789",
        )
        assert result.device is None
        assert result.success is False
        assert result.error == "Device not found"
