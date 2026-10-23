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
"""Service compatibility adapter for the workflow-owned Cisco IOS XR client."""

from typing import Any

from ncclient import manager  # type: ignore[import-untyped]

from nv_config_manager.temporal.client.device.base import NetworkConnection
from nv_config_manager_workflows.clients.device.cisco_xr import (
    CiscoIosXrConnection as _CiscoIosXrConnection,
)


class CiscoIosXrConnection(NetworkConnection, _CiscoIosXrConnection):
    """IOS XR client using service-owned configuration resolution."""

    DEFAULT_PORT = 830

    def _netconf_connect(self, **kwargs: Any) -> Any:
        """Use the service module's patch-compatible NETCONF constructor."""
        return manager.connect(**kwargs)
