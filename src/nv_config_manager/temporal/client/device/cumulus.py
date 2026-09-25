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
"""Service compatibility adapters for workflow-owned NVUE clients."""

from nv_config_manager.temporal.client.device.base import NetworkConnection
from nv_config_manager_workflows.clients.device.cumulus import (
    CumulusConnection as _CumulusConnection,
)
from nv_config_manager_workflows.clients.device.cumulus import (
    NVOSConnection as _NVOSConnection,
)
from nv_config_manager_workflows.clients.device.cumulus import (
    _parse_cl_support_path as _parse_cl_support_path,
)


class CumulusConnection(NetworkConnection, _CumulusConnection):
    """Cumulus client using service-owned configuration resolution."""

    DEFAULT_PORT = 8765


class NVOSConnection(NetworkConnection, _NVOSConnection):
    """NVOS client using the NVUE implementation on its default HTTPS port."""

    DEFAULT_PORT = 443
