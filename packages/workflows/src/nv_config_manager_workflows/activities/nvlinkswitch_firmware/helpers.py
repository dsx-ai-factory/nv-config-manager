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
"""Firmware-bundle helpers for NVLink switch activities."""

from nv_config_manager_dcim.models import FirmwareBundle
from nv_config_manager_dcim.workflow_models import NetworkDeviceData
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.runtime import RuntimeConfigurationError, get_dcim_client


async def get_firmware_bundle(
    device_data: NetworkDeviceData,
    bundle_version: str | None = None,
) -> FirmwareBundle:
    """Get normalized firmware intent through the selected DCIM provider."""
    try:
        client = get_dcim_client()
        async with client:
            return await client.get_firmware_bundle(device_data.id, bundle_version)
    except RuntimeConfigurationError:
        raise
    except Exception as error:
        raise ApplicationError(f"Failed to get device firmware bundle: {str(error)}") from error


async def get_desired_firmware_and_os_from_context(
    device_data: NetworkDeviceData,
    bundle_version: str,
) -> tuple[dict[str, str], str]:
    """Extract desired firmware and OS versions from normalized bundle data."""
    try:
        bundle = await get_firmware_bundle(device_data, bundle_version)
        desired_firmware = {}
        for component, component_data in bundle.components.items():
            if not component_data.reported_version:
                raise ApplicationError(
                    f"No reported version found for {component} in bundle {bundle.version}. "
                    "The configured DCIM provider must supply the version devices report "
                    "after upgrade."
                )
            desired_firmware[component.lower()] = component_data.reported_version

        return desired_firmware, bundle.desired_os_version
    except RuntimeConfigurationError:
        raise
    except Exception as error:
        raise ApplicationError(f"Failed to get desired firmware and OS: {str(error)}") from error
