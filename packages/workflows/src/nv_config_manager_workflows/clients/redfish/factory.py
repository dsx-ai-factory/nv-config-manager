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
"""Select a Redfish connection from explicitly resolved credentials."""

from collections.abc import Callable

from nv_config_manager_workflows.clients.redfish.base import RedfishConnection
from nv_config_manager_workflows.clients.redfish.bluefield import Bluefield3RedfishConnection
from nv_config_manager_workflows.clients.redfish.dell import DellRedfishConnection
from nv_config_manager_workflows.clients.redfish.lenovo import LenovoRedfishConnection
from nv_config_manager_workflows.clients.redfish.models import RedfishHost, RedfishVendor


def _connection_for_vendor(
    redfish_host: RedfishHost,
    *,
    username: str,
    password: str,
    config_manager_password: Callable[[], str],
) -> RedfishConnection:
    """Construct the vendor client after the service resolves credentials."""
    if redfish_host.vendor == RedfishVendor.LENOVO:
        return LenovoRedfishConnection(
            host=redfish_host,
            username=username,
            password=password,
            config_manager_password=config_manager_password,
        )
    if redfish_host.vendor == RedfishVendor.BLUEFIELD:
        return Bluefield3RedfishConnection(
            host=redfish_host,
            username=username,
            password=password,
            config_manager_password=config_manager_password,
        )
    if redfish_host.vendor == RedfishVendor.DELL:
        return DellRedfishConnection(
            host=redfish_host,
            username=username,
            password=password,
        )
    raise NotImplementedError(f"No Redfish connection implemented for vendor {redfish_host}")


def get_default_connection(
    redfish_host: RedfishHost,
    *,
    username: str,
    password: str,
    config_manager_password: Callable[[], str],
) -> RedfishConnection:
    """Construct a client using credentials selected for the default login."""
    return _connection_for_vendor(
        redfish_host,
        username=username,
        password=password,
        config_manager_password=config_manager_password,
    )


def get_config_manager_connection(
    redfish_host: RedfishHost,
    *,
    username: str,
    password: str,
    config_manager_password: Callable[[], str],
) -> RedfishConnection:
    """Construct a client using credentials selected for the managed login."""
    return _connection_for_vendor(
        redfish_host,
        username=username,
        password=password,
        config_manager_password=config_manager_password,
    )
