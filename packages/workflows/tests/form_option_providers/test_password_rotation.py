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
"""Tests for site password-rotation form options."""

import subprocess
import sys
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from nv_config_manager_dcim.api import DCIMClient
from nv_config_manager_dcim.models import (
    DCIMDeviceSelection,
    DCIMDeviceSelectionFilter,
    dcim_location_reference,
)

from nv_config_manager_workflows.form_option_providers.password_rotation import (
    PasswordUserOptionsQuery,
    resolve_password_user_options,
)
from nv_config_manager_workflows.runtime import configure_dcim_client


async def test_resolver_uses_filtered_devices_and_normalizes_password_users() -> None:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.list_devices = AsyncMock(
        return_value=[
            DCIMDeviceSelection(id="device-1", name="leaf-1"),
            DCIMDeviceSelection(id="device-2", name="leaf-2"),
        ]
    )
    client.get_device_password_secret_names = AsyncMock(
        return_value={
            "admin": "admin-password",
            "svc-ngc-cfa-nv-config-manager": "service-password",
        }
    )
    configure_dcim_client(lambda: cast(DCIMClient, client))

    response = await resolve_password_user_options(
        PasswordUserOptionsQuery(
            location="site-a",
            location_type="Site",
            role=["leaf"],
            status=["Active", "Provisioned"],
            tenant="tenant-a",
        )
    )

    client.list_devices.assert_awaited_once_with(
        DCIMDeviceSelectionFilter(
            sites=(dcim_location_reference("site-a", "Site"),),
            statuses=("Active", "Provisioned"),
            roles=("leaf",),
            tenants=("tenant-a",),
            managed_only=True,
        )
    )
    client.get_device_password_secret_names.assert_awaited_once_with("device-1")
    assert [item.model_dump(exclude_none=True) for item in response.items] == [
        {
            "label": "admin",
            "value": "admin",
            "description": "admin (admin-password)",
        }
    ]
    assert response.meta.matching_device_count == 2


async def test_resolver_does_not_request_passwords_without_matching_devices() -> None:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.list_devices = AsyncMock(return_value=[])
    client.get_device_password_secret_names = AsyncMock()
    configure_dcim_client(lambda: cast(DCIMClient, client))

    response = await resolve_password_user_options(PasswordUserOptionsQuery(location="site-a"))

    client.get_device_password_secret_names.assert_not_awaited()
    assert response.items == []
    assert response.meta.matching_device_count == 0


def test_importing_the_workflow_does_not_import_its_api_only_resolver() -> None:
    script = "\n".join(
        [
            "import sys",
            "import nv_config_manager_workflows.workflows.site_password_rotation",
            (
                "assert 'nv_config_manager_workflows.form_option_providers.password_rotation' "
                "not in sys.modules"
            ),
        ]
    )

    subprocess.run([sys.executable, "-c", script], check=True)
