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
"""Private helpers for operating-system image activities."""

from datetime import datetime

from nv_config_manager_dcim.workflow_models import NetworkDeviceData

from nv_config_manager_workflows.clients.device.base import NetworkConnection
from nv_config_manager_workflows.clients.device.mellanox import MellanoxConnection
from nv_config_manager_workflows.runtime import get_device_connection


def _verify_device_rebooted(
    device: NetworkConnection,
    ztp_execution_time: datetime,
) -> bool:
    """Return whether uptime proves the device rebooted after ZTP execution."""
    try:
        uptime = device.get_uptime()
        elapsed_time = (datetime.now() - ztp_execution_time).total_seconds()
        return uptime < elapsed_time
    except Exception:
        return False


def check_ztp_success(
    device: NetworkConnection,
    ztp_execution_time: datetime | None,
) -> bool:
    """Return whether ZTP succeeded and, when requested, the device rebooted."""
    status = device.get_ztp_status()
    if status != "success":
        return False

    if ztp_execution_time:
        return _verify_device_rebooted(device, ztp_execution_time)

    return True


def mellanox_connection(device_data: NetworkDeviceData) -> MellanoxConnection:
    """Return a Mellanox connection or reject an incompatible device client."""
    connection = get_device_connection(device_data)
    if not isinstance(connection, MellanoxConnection):
        raise ValueError("Failed to create MellanoxConnection")
    return connection
