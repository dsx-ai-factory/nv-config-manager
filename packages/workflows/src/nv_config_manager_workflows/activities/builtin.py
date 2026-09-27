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
"""Canonical catalog of activities owned by the workflows package."""

from nv_config_manager_workflows.activities.backup import BACKUP_ACTIVITIES
from nv_config_manager_workflows.activities.bmc import BMC_ACTIVITIES
from nv_config_manager_workflows.activities.cable_validation import (
    CABLE_VALIDATION_ACTIVITIES,
)
from nv_config_manager_workflows.activities.config import CONFIG_ACTIVITIES
from nv_config_manager_workflows.activities.dcim import DCIM_ACTIVITIES
from nv_config_manager_workflows.activities.deploy import DEPLOY_ACTIVITIES
from nv_config_manager_workflows.activities.device import DEVICE_ACTIVITIES
from nv_config_manager_workflows.activities.device_password_rotation import (
    DEVICE_PASSWORD_ROTATION_ACTIVITIES,
)
from nv_config_manager_workflows.activities.diagnostics import DIAGNOSTICS_ACTIVITIES
from nv_config_manager_workflows.activities.hardware_validation import (
    HARDWARE_VALIDATION_ACTIVITIES,
)
from nv_config_manager_workflows.activities.hello_world import HELLO_WORLD_ACTIVITIES
from nv_config_manager_workflows.activities.ib_dcim import IB_DCIM_ACTIVITIES
from nv_config_manager_workflows.activities.ib_guid_discovery import (
    IB_GUID_DISCOVERY_ACTIVITIES,
)
from nv_config_manager_workflows.activities.ib_pkey import IB_PKEY_ACTIVITIES
from nv_config_manager_workflows.activities.lock import LOCK_ACTIVITIES
from nv_config_manager_workflows.activities.nats import NATS_ACTIVITIES
from nv_config_manager_workflows.activities.nvlinkswitch_firmware import (
    NVLINKSWITCH_FIRMWARE_ACTIVITIES,
)
from nv_config_manager_workflows.activities.os import OS_ACTIVITIES
from nv_config_manager_workflows.activities.render import RENDER_ACTIVITIES
from nv_config_manager_workflows.activities.slack import SLACK_ACTIVITIES
from nv_config_manager_workflows.activities.ticketing import TICKETING_ACTIVITIES
from nv_config_manager_workflows.activities.ufm import UFM_ACTIVITIES

BUILTIN_ACTIVITIES = (
    *CONFIG_ACTIVITIES,
    *DEVICE_ACTIVITIES,
    *NATS_ACTIVITIES,
    *SLACK_ACTIVITIES,
    *DCIM_ACTIVITIES,
    *UFM_ACTIVITIES,
    *IB_PKEY_ACTIVITIES,
    *IB_DCIM_ACTIVITIES,
    *IB_GUID_DISCOVERY_ACTIVITIES,
    *HELLO_WORLD_ACTIVITIES,
    *LOCK_ACTIVITIES,
    *BACKUP_ACTIVITIES,
    *DEPLOY_ACTIVITIES,
    *RENDER_ACTIVITIES,
    *OS_ACTIVITIES,
    *NVLINKSWITCH_FIRMWARE_ACTIVITIES,
    *CABLE_VALIDATION_ACTIVITIES,
    *BMC_ACTIVITIES,
    *HARDWARE_VALIDATION_ACTIVITIES,
    *DEVICE_PASSWORD_ROTATION_ACTIVITIES,
    *DIAGNOSTICS_ACTIVITIES,
    *TICKETING_ACTIVITIES,
)

__all__ = ["BUILTIN_ACTIVITIES"]
