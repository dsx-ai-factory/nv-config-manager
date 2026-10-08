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
"""Package-owned BMC and Redfish activity contracts."""

from typing import Any, Self, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from nv_config_manager_dcim.api import DCIMClient
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.activities import bmc
from nv_config_manager_workflows.activities.bmc import (
    BMC_ACTIVITIES,
    DeviceArpTable,
    DiscoverHostsInput,
    GetDpuDetailsActivityInput,
    GetServerDetailsActivityInput,
    PopulateRedfishMacsInput,
    RedfishHostInput,
    RedfishServer,
    UpdateDpuDataActivityInput,
    discover_redfish_hosts,
    factory_reset_bmc,
    get_dpu_details,
    get_server_details,
    helpers,
    models,
    populate_redfish_macs,
    power_on_host,
    set_redfish_password,
    update_dpu_data,
)
from nv_config_manager_workflows.clients.redfish.base import RedfishConnection
from nv_config_manager_workflows.clients.redfish.models import RedfishHost, RedfishVendor
from nv_config_manager_workflows.registration import activity_name
from nv_config_manager_workflows.runtime import configure_dcim_client, configure_redfish_connection


class _DCIMClient:
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    find_host_devices_by_mac = AsyncMock()


def test_bmc_catalog_has_eight_unique_activities() -> None:
    assert len(BMC_ACTIVITIES) == 8
    assert len({activity_name(item) for item in BMC_ACTIVITIES}) == 8


def test_bmc_package_reexports_split_models_and_helpers() -> None:
    """The package root preserves its public surface after the module split."""
    assert bmc.RedfishHostInput is models.RedfishHostInput
    assert bmc.UpdateDpuDataActivityOutput is models.UpdateDpuDataActivityOutput
    assert bmc.combine_arp_tables is helpers.combine_arp_tables
    assert bmc.http_get is helpers.http_get


def test_password_provisioning_requests_default_credentials() -> None:
    roles: list[str] = []
    connection = MagicMock()
    connection.set_config_manager_password.return_value = object()

    def provider(_host: RedfishHost, role: str) -> RedfishConnection:
        roles.append(role)
        return cast(RedfishConnection, connection)

    configure_redfish_connection(provider)  # type: ignore[arg-type]
    host = RedfishHost(address="192.0.2.10", vendor=RedfishVendor.LENOVO)

    result = set_redfish_password(RedfishHostInput(host=host))

    assert result.host is host
    assert roles == ["default"]
    connection.set_config_manager_password.assert_called_once_with()


@pytest.mark.asyncio
async def test_discovery_rejects_an_empty_or_reversed_range() -> None:
    with pytest.raises(ApplicationError, match="lower or equal"):
        await discover_redfish_hosts(
            DiscoverHostsInput(
                ip_range_start="192.0.2.10",
                ip_range_end="192.0.2.10",
                ips_excluded=[],
                port=443,
            )
        )


@pytest.mark.asyncio
async def test_discovery_skips_a_fully_excluded_range() -> None:
    result = await discover_redfish_hosts(
        DiscoverHostsInput(
            ip_range_start="192.0.2.10",
            ip_range_end="192.0.2.11",
            ips_excluded=["192.0.2.10"],
            port=443,
        )
    )

    assert result.hosts == []


@pytest.mark.asyncio
async def test_discovery_returns_only_successful_supported_redfish_hosts(
    aioresponses: Any,
) -> None:
    aioresponses.get(
        "https://192.0.2.1:443/redfish/v1/",
        payload={"RedfishVersion": "1.0.0", "Vendor": "Lenovo"},
    )
    aioresponses.get(
        "https://192.0.2.2:443/redfish/v1/",
        status=404,
        payload={"error": "not found"},
    )
    aioresponses.get(
        "https://192.0.2.3:443/redfish/v1/",
        payload={"RedfishVersion": "1.0.0", "Vendor": "unsupported"},
    )

    result = await discover_redfish_hosts(
        DiscoverHostsInput(
            ip_range_start="192.0.2.1",
            ip_range_end="192.0.2.4",
            ips_excluded=[],
            port=443,
        )
    )

    assert result.hosts == [RedfishHost(address="192.0.2.1", port=443, vendor=RedfishVendor.LENOVO)]


@pytest.mark.asyncio
async def test_populate_redfish_macs_requires_one_unambiguous_mac() -> None:
    matched = RedfishHost(address="192.0.2.10", vendor=RedfishVendor.LENOVO)
    ambiguous = RedfishHost(address="192.0.2.11", vendor=RedfishVendor.LENOVO)

    result = await populate_redfish_macs(
        PopulateRedfishMacsInput(
            hosts=[matched, ambiguous],
            arp_tables=[
                DeviceArpTable(
                    ip_to_mac={
                        "192.0.2.10": ["00-00-5E-00-53-01"],
                        "192.0.2.11": ["00-00-5E-00-53-02", "00-00-5E-00-53-03"],
                    }
                )
            ],
        )
    )

    assert result.hosts[0].mac == "00-00-5E-00-53-01"
    assert result.hosts[1].mac is None


def test_password_provisioning_checks_managed_credentials_after_unauthorized_default() -> None:
    default = MagicMock()
    default.set_config_manager_password.return_value = None
    managed = MagicMock()
    managed.get_redfish_data.return_value = object()
    roles: list[str] = []

    def provider(_host: RedfishHost, role: str) -> RedfishConnection:
        roles.append(role)
        return cast(RedfishConnection, default if role == "default" else managed)

    configure_redfish_connection(provider)  # type: ignore[arg-type]

    result = set_redfish_password(
        RedfishHostInput(host=RedfishHost(address="192.0.2.10", vendor=RedfishVendor.LENOVO))
    )

    assert result.host is None
    assert roles == ["default", "config_manager"]


def test_power_reset_and_server_details_use_managed_credentials() -> None:
    connection = MagicMock()
    connection.is_host_powered_on.return_value = False
    connection.get_serial.return_value = "serial-1"
    connection.get_nic_info.return_value = []
    roles: list[str] = []

    def provider(_host: RedfishHost, role: str) -> RedfishConnection:
        roles.append(role)
        return cast(RedfishConnection, connection)

    configure_redfish_connection(provider)  # type: ignore[arg-type]
    host = RedfishHost(address="192.0.2.10", vendor=RedfishVendor.LENOVO)

    assert power_on_host(RedfishHostInput(host=host)).host is host
    assert factory_reset_bmc(RedfishHostInput(host=host)).host is host
    result = get_server_details(
        GetServerDetailsActivityInput(host=host, nic_manufacturers=["NVIDIA"])
    )

    assert result.server.serial == "serial-1"
    assert roles == ["config_manager", "config_manager", "config_manager"]
    connection.power_on_chassis.assert_called_once_with()
    connection.factory_reset.assert_called_once_with()
    connection.get_nic_info.assert_called_once_with(manufacturers=["NVIDIA"])


def test_get_dpu_details_rejects_a_non_bluefield_connection() -> None:
    connection = MagicMock(spec=RedfishConnection)
    configure_redfish_connection(lambda _host, _role: cast(RedfishConnection, connection))  # type: ignore[arg-type]

    with pytest.raises(ApplicationError, match="not a Bluefield DPU"):
        get_dpu_details(
            GetDpuDetailsActivityInput(
                host=RedfishHost(address="192.0.2.10", vendor=RedfishVendor.BLUEFIELD)
            )
        )


@pytest.mark.asyncio
async def test_update_dpu_data_requires_a_server_mac() -> None:
    client = _DCIMClient()
    configure_dcim_client(lambda: cast(DCIMClient, client))

    with pytest.raises(ApplicationError, match="Server MAC address is required"):
        await update_dpu_data(
            UpdateDpuDataActivityInput(
                server=RedfishServer(
                    address="192.0.2.10",
                    vendor=RedfishVendor.DELL,
                    serial="serial-1",
                    nics=[],
                )
            )
        )
