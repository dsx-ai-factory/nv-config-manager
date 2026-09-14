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
"""Compatibility exports for the relocated Redfish client."""

from nv_config_manager.temporal.factories.redfish import get_bmc_creds
from nv_config_manager.temporal.factories.redfish import (
    get_config_manager_connection as create_config_manager_connection,
)
from nv_config_manager.temporal.factories.redfish import (
    get_default_connection as create_default_connection,
)
from nv_config_manager_workflows.clients.redfish import (
    Bluefield3RedfishConnection,
    DellRedfishConnection,
    LenovoRedfishConnection,
    RedfishConnection,
    RedfishDpu,
    RedfishDpuPort,
    RedfishHost,
    RedfishNic,
    RedfishServer,
    RedfishVendor,
)


def get_default_connection(redfish_host: RedfishHost) -> RedfishConnection:
    """Preserve the legacy service entry point for a default connection."""
    return create_default_connection(
        redfish_host,
        bmc_credentials=get_bmc_creds(),
    )


def get_config_manager_connection(redfish_host: RedfishHost) -> RedfishConnection:
    """Preserve the legacy service entry point for a managed connection."""
    return create_config_manager_connection(
        redfish_host,
        bmc_credentials=get_bmc_creds(),
    )


__all__ = [
    "Bluefield3RedfishConnection",
    "DellRedfishConnection",
    "LenovoRedfishConnection",
    "RedfishConnection",
    "RedfishDpu",
    "RedfishDpuPort",
    "RedfishHost",
    "RedfishNic",
    "RedfishServer",
    "RedfishVendor",
    "get_bmc_creds",
    "get_config_manager_connection",
    "get_default_connection",
]
