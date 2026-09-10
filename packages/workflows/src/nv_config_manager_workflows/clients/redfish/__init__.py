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
"""Reusable Redfish models, connections, and vendor dispatch."""

from nv_config_manager_workflows.clients.redfish.base import (
    RedfishClientSettings,
    RedfishConnection,
)
from nv_config_manager_workflows.clients.redfish.bluefield import Bluefield3RedfishConnection
from nv_config_manager_workflows.clients.redfish.dell import DellRedfishConnection
from nv_config_manager_workflows.clients.redfish.dispatch import (
    get_config_manager_connection,
    get_default_connection,
)
from nv_config_manager_workflows.clients.redfish.lenovo import LenovoRedfishConnection
from nv_config_manager_workflows.clients.redfish.models import (
    RedfishDpu,
    RedfishDpuPort,
    RedfishHost,
    RedfishNic,
    RedfishServer,
    RedfishVendor,
)

__all__ = [
    "Bluefield3RedfishConnection",
    "DellRedfishConnection",
    "LenovoRedfishConnection",
    "RedfishClientSettings",
    "RedfishConnection",
    "RedfishDpu",
    "RedfishDpuPort",
    "RedfishHost",
    "RedfishNic",
    "RedfishServer",
    "RedfishVendor",
    "get_config_manager_connection",
    "get_default_connection",
]
