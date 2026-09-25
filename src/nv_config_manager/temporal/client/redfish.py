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
"""Service credential factories and compatibility exports for Redfish clients."""

from temporalio.exceptions import ApplicationError

from nv_config_manager.common.config import load_config
from nv_config_manager.common.config.client_settings.redfish import (
    RedfishClientSettings,
    RedfishCredentialKind,
    get_bmc_credentials,
    redfish_client_settings,
)
from nv_config_manager_workflows.clients.redfish import (
    Bluefield3RedfishConnection as Bluefield3RedfishConnection,
)
from nv_config_manager_workflows.clients.redfish import (
    DellRedfishConnection as DellRedfishConnection,
)
from nv_config_manager_workflows.clients.redfish import (
    LenovoRedfishConnection as LenovoRedfishConnection,
)
from nv_config_manager_workflows.clients.redfish import RedfishConnection as RedfishConnection
from nv_config_manager_workflows.clients.redfish import RedfishDpu as RedfishDpu
from nv_config_manager_workflows.clients.redfish import RedfishDpuPort as RedfishDpuPort
from nv_config_manager_workflows.clients.redfish import RedfishHost as RedfishHost
from nv_config_manager_workflows.clients.redfish import RedfishNic as RedfishNic
from nv_config_manager_workflows.clients.redfish import RedfishServer as RedfishServer
from nv_config_manager_workflows.clients.redfish import RedfishVendor as RedfishVendor

# isort: off
from nv_config_manager_workflows.clients.redfish import (
    get_config_manager_connection as _get_config_manager_connection,
    get_default_connection as _get_default_connection,
)
# isort: on


def get_bmc_creds() -> dict[str, dict[str, str]]:
    """Retain the legacy credential lookup and its test patch point."""
    return get_bmc_credentials()


def _connection_settings(
    redfish_host: RedfishHost,
    credential_kind: RedfishCredentialKind,
) -> RedfishClientSettings:
    """Resolve service-owned credentials without exposing them to package code."""
    try:
        return redfish_client_settings(
            load_config(),
            vendor=redfish_host.vendor,
            mac=redfish_host.mac,
            credential_kind=credential_kind,
            bmc_credentials=get_bmc_creds(),
            config_loader=load_config,
        )
    except ApplicationError:
        if redfish_host.vendor == RedfishVendor.DELL:
            raise ApplicationError(f"No password found for host {redfish_host}") from None
        raise


def get_default_connection(redfish_host: RedfishHost) -> RedfishConnection:
    """Construct a Redfish client using the service's default credentials."""
    settings = _connection_settings(redfish_host, "default")
    return _get_default_connection(redfish_host, **settings)


def get_config_manager_connection(redfish_host: RedfishHost) -> RedfishConnection:
    """Construct a Redfish client using the service's managed credentials."""
    settings = _connection_settings(redfish_host, "config_manager")
    return _get_config_manager_connection(redfish_host, **settings)


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
