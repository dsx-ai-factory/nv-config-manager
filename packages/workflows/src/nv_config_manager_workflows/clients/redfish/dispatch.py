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
"""Vendor dispatch for Redfish connections."""

from __future__ import annotations

from nv_config_manager_workflows.clients.redfish.base import (
    RedfishClientSettings,
    RedfishConnection,
)
from nv_config_manager_workflows.clients.redfish.bluefield import Bluefield3RedfishConnection
from nv_config_manager_workflows.clients.redfish.dell import DellRedfishConnection
from nv_config_manager_workflows.clients.redfish.lenovo import LenovoRedfishConnection
from nv_config_manager_workflows.clients.redfish.models import RedfishHost, RedfishVendor


def _connection_for_vendor(
    host: RedfishHost,
    settings: RedfishClientSettings,
) -> RedfishConnection:
    """Construct the connection class associated with a host vendor."""
    connection_type: type[RedfishConnection]
    if host.vendor == RedfishVendor.LENOVO:
        connection_type = LenovoRedfishConnection
    elif host.vendor == RedfishVendor.BLUEFIELD:
        connection_type = Bluefield3RedfishConnection
    elif host.vendor == RedfishVendor.DELL:
        connection_type = DellRedfishConnection
    else:
        raise NotImplementedError(f"No Redfish connection implemented for vendor {host}")

    return connection_type(
        host=host,
        username=settings["username"],
        password=settings["password"],
        config_manager_password=settings["config_manager_password"],
    )


def get_default_connection(
    host: RedfishHost,
    settings: RedfishClientSettings,
) -> RedfishConnection:
    """Construct a vendor connection from explicit default-login settings."""
    return _connection_for_vendor(host, settings)


def get_config_manager_connection(
    host: RedfishHost,
    settings: RedfishClientSettings,
) -> RedfishConnection:
    """Construct a vendor connection from explicit service-login settings."""
    return _connection_for_vendor(host, settings)
