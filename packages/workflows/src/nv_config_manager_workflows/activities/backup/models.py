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
"""Input models for configuration backup activities."""

from nv_config_manager_dcim.workflow_models import NetworkDeviceData
from pydantic import BaseModel


class PersistConfigBackupInput(BaseModel):
    """Input class for persist_config_backup activity."""

    device_data: NetworkDeviceData
    device_running_config: str
    commit_message: str
    user: str
    user_domain: str | None


class RecordBackupConfigManagerPluginInput(BaseModel):
    """Input class for record_backup_config_manager_plugin activity."""

    workflow_id: str
    device_id: str
    commit_id: str
    path: str
    user: str
    commit_message: str
    deployed_commit_id: str | None


__all__ = [
    "PersistConfigBackupInput",
    "RecordBackupConfigManagerPluginInput",
]
