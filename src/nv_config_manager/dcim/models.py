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
"""Compatibility imports for standalone DCIM SDK models."""

from nv_config_manager_dcim.models import *  # noqa: F403
from nv_config_manager_dcim.render import (  # noqa: F401
    DeviceRenderData,
    LocationRenderData,
    RenderData,
)
from nv_config_manager_dcim.workflow_models import (  # noqa: F401
    DeviceBayData,
    DeviceData,
    HostDeviceData,
    InterfaceData,
    NetworkDeviceData,
    OSImageVersions,
    Platform,
)
