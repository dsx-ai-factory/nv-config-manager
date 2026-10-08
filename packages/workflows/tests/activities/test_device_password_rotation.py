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
"""Package-owned password rotation activity contracts."""

from typing import Self, cast

import pytest
from nv_config_manager_dcim.api import DCIMClient
from nv_config_manager_dcim.workflow_models import NetworkDeviceData

from nv_config_manager_workflows.activities import device_password_rotation
from nv_config_manager_workflows.activities.device_password_rotation import (
    DEVICE_PASSWORD_ROTATION_ACTIVITIES,
    GetPasswordMappingsInput,
    ValidatePasswordDiffInput,
    get_password_mappings,
    helpers,
    models,
    validate_password_diff,
)
from nv_config_manager_workflows.registration import activity_name
from nv_config_manager_workflows.runtime import configure_dcim_client


class _PasswordMappingClient:
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get_device_password_mapping_users(self, _device_id: str) -> set[str]:
        return {"admin"}


def _device() -> NetworkDeviceData:
    return NetworkDeviceData.model_construct(id="device-1", name="leaf-1")


def test_package_reexports_models_and_helpers() -> None:
    """The package keeps its public API while definitions live in focused modules."""
    assert device_password_rotation.FormatPasswordRotationResultsInput is (
        models.FormatPasswordRotationResultsInput
    )
    assert device_password_rotation.GetPasswordMappingsInput is models.GetPasswordMappingsInput
    assert device_password_rotation.GetPasswordMappingsOutput is models.GetPasswordMappingsOutput
    assert device_password_rotation.ValidatePasswordDiffInput is models.ValidatePasswordDiffInput
    assert device_password_rotation.ValidatePasswordDiffOutput is models.ValidatePasswordDiffOutput
    assert device_password_rotation.ValidatePlatformSupportInput is (
        models.ValidatePlatformSupportInput
    )
    assert device_password_rotation.ValidatePlatformSupportOutput is (
        models.ValidatePlatformSupportOutput
    )
    assert helpers._junos_expected_path("root") == "system root-authentication"
    assert not hasattr(device_password_rotation, "_junos_expected_path")
    assert not hasattr(device_password_rotation, "_validate_cumulus_diff")
    assert not hasattr(device_password_rotation, "_validate_junos_diff")


def test_password_rotation_catalog_has_five_unique_activities() -> None:
    assert len(DEVICE_PASSWORD_ROTATION_ACTIVITIES) == 5
    assert len({activity_name(item) for item in DEVICE_PASSWORD_ROTATION_ACTIVITIES}) == 5


@pytest.mark.asyncio
async def test_password_diff_parser_preserves_line_classification() -> None:
    result = await validate_password_diff(
        ValidatePasswordDiffInput(
            diff="nv set system aaa user admin hashed-password SECRET\nnv set system hostname bad",
            username="admin",
            platform="cumulus",
        )
    )

    assert result.valid_lines == ["nv set system aaa user admin hashed-password SECRET"]
    assert result.invalid_lines == ["nv set system hostname bad"]


@pytest.mark.asyncio
async def test_password_mapping_uses_runtime_dcim_provider() -> None:
    client = cast(DCIMClient, _PasswordMappingClient())
    configure_dcim_client(lambda: client)

    result = await get_password_mappings(
        GetPasswordMappingsInput(device=_device(), username="admin")
    )

    assert result.username == "admin"
