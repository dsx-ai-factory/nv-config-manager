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
"""Compatibility entry point for service device construction."""

from __future__ import annotations

from typing import cast

from nv_config_manager_dcim.workflow_models import NetworkDeviceData

from nv_config_manager.common.config_loader import load_config
from nv_config_manager.temporal.client.device.base import NetworkConnection


def from_device_data(device_data: NetworkDeviceData) -> NetworkConnection:
    """Forward dispatch while retaining this entry point's config loader."""
    return cast(
        NetworkConnection,
        NetworkConnection.from_device_data(device_data, config=load_config()),
    )
