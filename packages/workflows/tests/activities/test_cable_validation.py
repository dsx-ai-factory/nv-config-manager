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
"""Package-owned cable validation activity contracts."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from nv_config_manager_dcim import CableStatus
from nv_config_manager_dcim.workflow_models import InterfaceData, Platform

from nv_config_manager_workflows.activities import cable_validation
from nv_config_manager_workflows.activities.cable_validation import (
    CABLE_STATUS_UPDATE_CONCURRENCY,
    CABLE_VALIDATION_ACTIVITIES,
    CableValidationResultData,
    DecorateResultActivityInput,
    DeviceArpTable,
    DeviceMacTable,
    DeviceNeighborData,
    InterfaceNeighborData,
    InvalidCable,
    NetworkDeviceData,
    UpdateCableStatusesInput,
    ValidateDeviceNeighborsInput,
    decorate_result,
    models,
    update_cable_statuses,
    validate_device_neighbors,
)
from nv_config_manager_workflows.activities.cable_validation import (
    activities as cable_validation_activities,
)
from nv_config_manager_workflows.registration import activity_name


def test_cable_validation_catalog_has_five_unique_activities() -> None:
    assert isinstance(CABLE_VALIDATION_ACTIVITIES, tuple)
    assert len(CABLE_VALIDATION_ACTIVITIES) == 5
    assert len({activity_name(item) for item in CABLE_VALIDATION_ACTIVITIES}) == 5


def test_cable_validation_package_reexports_split_models() -> None:
    """The package root preserves public model identity after the module split."""
    assert cable_validation.CableValidationRow is models.CableValidationRow
    assert cable_validation.ValidateDeviceNeighborsInput is models.ValidateDeviceNeighborsInput


@pytest.mark.asyncio
async def test_validation_classifies_cable_statuses() -> None:
    neighbor = lambda name: InterfaceNeighborData(  # noqa: E731
        name=name,
        device_name="peer",
        device_role="leaf",
    )
    intended = DeviceNeighborData(
        neighbors={
            "valid": neighbor("Ethernet1"),
            "down": neighbor("Ethernet2"),
            "mismatch": neighbor("Ethernet3"),
            "ignored": neighbor("Ethernet4"),
            "link-state-only": neighbor("Ethernet5"),
            "ignored-no-neighbor": neighbor("Ethernet6"),
        },
        ignore=["ignored"],
        link_state_only=["link-state-only"],
    )
    actual = DeviceNeighborData(
        neighbors={
            "valid": neighbor("Ethernet1"),
            "mismatch": neighbor("wrong-port"),
        },
        link_states={
            "valid": True,
            "down": False,
            "mismatch": True,
            "ignored": False,
            "link-state-only": True,
            "ignored-no-neighbor": True,
        },
    )
    device = NetworkDeviceData(
        id="device-1",
        name="leaf-1",
        role="leaf",
        site="site-1",
        device_type="switch",
        platform=Platform.CUMULUS_LINUX,
        primary_ip4="192.0.2.1",
        primary_ip6=None,
    )

    result = await validate_device_neighbors(
        ValidateDeviceNeighborsInput(
            device=device,
            intended=intended,
            actual=actual,
            mac_table=DeviceMacTable(),
            arp_table=DeviceArpTable(),
            ignore_no_neighbor=True,
        )
    )

    assert result.cable_statuses == {
        "valid": CableStatus.CONNECTED,
        "down": CableStatus.DISCONNECTED,
        "mismatch": CableStatus.INVALID,
        "link-state-only": CableStatus.CONNECTED,
    }
    assert set(result.interfaces) == {"down", "mismatch", "ignored-no-neighbor"}


@pytest.mark.asyncio
async def test_update_cable_statuses_is_noop_for_unsupported_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnsupportedClient:
        def __init__(self) -> None:
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    client = UnsupportedClient()
    monkeypatch.setattr(cable_validation_activities, "get_dcim_client", lambda: client)

    await update_cable_statuses(
        UpdateCableStatusesInput(
            device_id="device-1",
            cable_statuses={"Ethernet1/1": CableStatus.CONNECTED},
            workflow_id="workflow-1",
        )
    )

    assert client.closed is True


@pytest.mark.asyncio
async def test_update_cable_statuses_passes_workflow_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SupportedClient:
        def __init__(self) -> None:
            self.updates: list[Any] = []

        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(
            self,
            exc_type: object,
            exc_value: object,
            traceback: object,
        ) -> None:
            return None

        async def update_cable_status(self, update: Any) -> None:
            self.updates.append(update)

    client = SupportedClient()
    monkeypatch.setattr(cable_validation_activities, "get_dcim_client", lambda: client)

    await update_cable_statuses(
        UpdateCableStatusesInput(
            device_id="device-1",
            cable_statuses={"Ethernet1/1": CableStatus.DISCONNECTED},
            workflow_id="workflow-1",
        )
    )

    assert len(client.updates) == 1
    assert client.updates[0].model_dump(mode="json") == {
        "device_id": "device-1",
        "interface_name": "Ethernet1/1",
        "status": "Disconnected",
        "workflow_id": "workflow-1",
    }


@pytest.mark.asyncio
async def test_update_cable_statuses_writes_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SupportedClient:
        def __init__(self) -> None:
            self.active = 0
            self.maximum_active = 0

        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def update_cable_status(self, update: Any) -> None:
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            await asyncio.sleep(0)
            self.active -= 1

    client = SupportedClient()
    monkeypatch.setattr(cable_validation_activities, "get_dcim_client", lambda: client)

    await update_cable_statuses(
        UpdateCableStatusesInput(
            device_id="device-1",
            cable_statuses={
                f"Ethernet1/{index}": CableStatus.CONNECTED
                for index in range(CABLE_STATUS_UPDATE_CONCURRENCY * 2)
            },
            workflow_id="workflow-1",
        )
    )

    assert client.maximum_active == CABLE_STATUS_UPDATE_CONCURRENCY


@pytest.mark.asyncio
async def test_decorate_result_uses_runtime_dcim_provider_without_mutating_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.get_interface_hosts_by_mac = AsyncMock(
        return_value=[
            InterfaceData(
                name="eth0",
                id="interface-1",
                host="server-1",
                mac_address="AA-BB-CC-DD-EE-01",
                vrf_id=None,
            ),
            InterfaceData(
                name="eth1",
                id="interface-2",
                host="server-2",
                mac_address="AA-BB-CC-DD-EE-02",
                vrf_id=None,
            ),
            InterfaceData(
                name="eth2",
                id="interface-3",
                host="server-3",
                mac_address="AA-BB-CC-DD-EE-03",
                vrf_id=None,
            ),
        ]
    )
    monkeypatch.setattr(cable_validation_activities, "get_dcim_client", Mock(return_value=client))
    activity_input = DecorateResultActivityInput(
        devices={
            "leaf-1": CableValidationResultData(
                interfaces={
                    "swp1": InvalidCable(
                        actual=InterfaceNeighborData(
                            macs=["AA-BB-CC-DD-EE-01", "AA-BB-CC-DD-EE-03"]
                        )
                    ),
                    "swp2": InvalidCable(actual=InterfaceNeighborData(name="AA-BB-CC-DD-EE-02")),
                },
                device=None,
            )
        }
    )

    result = await decorate_result(activity_input)

    first = result.devices["leaf-1"].interfaces["swp1"].actual
    second = result.devices["leaf-1"].interfaces["swp2"].actual
    assert first is not None
    assert first.name == "eth0"
    assert first.device_name == "server-1"
    assert second is not None
    assert second.name == "eth1"
    assert second.device_name == "server-2"
    assert activity_input.devices["leaf-1"].interfaces["swp1"].actual is not None
    assert activity_input.devices["leaf-1"].interfaces["swp1"].actual.name is None
    client.get_interface_hosts_by_mac.assert_awaited_once_with(
        ["AA-BB-CC-DD-EE-01", "AA-BB-CC-DD-EE-03", "AA-BB-CC-DD-EE-02"]
    )
    client.__aenter__.assert_awaited_once_with()
    client.__aexit__.assert_awaited_once_with(None, None, None)


@pytest.mark.asyncio
async def test_decorate_result_skips_dcim_lookup_without_mac_addresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = Mock(side_effect=AssertionError("DCIM provider should not be requested"))
    monkeypatch.setattr(cable_validation_activities, "get_dcim_client", provider)
    activity_input = DecorateResultActivityInput(
        devices={
            "leaf-1": CableValidationResultData(
                interfaces={
                    "swp1": InvalidCable(
                        actual=InterfaceNeighborData(name="Ethernet1", device_name="server-1")
                    )
                },
                device=None,
            )
        }
    )

    result = await decorate_result(activity_input)

    assert result.devices == activity_input.devices
    assert result.devices is not activity_input.devices
    provider.assert_not_called()
