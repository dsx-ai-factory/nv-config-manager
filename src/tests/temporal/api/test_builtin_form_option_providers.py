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
"""Integration tests for built-in workflow-owned form-option providers."""

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from nv_config_manager.dcim import DCIMDeviceSelection
from nv_config_manager.temporal.api.main import app


def test_site_password_rotation_password_users_are_served_by_generated_route() -> None:
    """The generated route invokes the built-in provider and preserves its response contract."""
    dcim_client = MagicMock()
    dcim_client.__aenter__ = AsyncMock(return_value=dcim_client)
    dcim_client.__aexit__ = AsyncMock(return_value=None)
    dcim_client.list_devices = AsyncMock(
        return_value=[DCIMDeviceSelection(id="device-1", name="leaf-1")]
    )
    dcim_client.get_device_password_secret_names = AsyncMock(
        return_value={"admin": "admin-password"}
    )

    with (
        patch(
            "nv_config_manager_workflows.form_option_providers.password_rotation.get_dcim_client",
            return_value=dcim_client,
        ),
        patch(
            "nv_config_manager.temporal.api.form_option_endpoints.require_workflow_execute_access"
        ),
    ):
        response = TestClient(app).get(
            "/v1/workflow/site-password-rotation/form-options/password-users",
            params={
                "location": "site-a",
                "location_type": "Site",
                "role": "leaf",
                "status": ["Active", "Provisioned"],
                "tenant": "tenant-a",
                "managed_only": "true",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "label": "admin",
                "value": "admin",
                "description": "admin (admin-password)",
            }
        ],
        "meta": {"matching_device_count": 1, "warnings": []},
    }


def test_branch_only_parameter_routes_are_not_exposed() -> None:
    """Routes introduced only by this branch are removed instead of treated as legacy API."""
    paths = app.openapi()["paths"]

    assert "/v1/parameter/diagnostics/command-options" not in paths
    assert "/v1/parameter/password-users" not in paths
    assert "/v1/parameter/diagnostics/commands" in paths
    assert "/v1/parameter/device/{device_id}/password_users" in paths
