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
import responses
from nv_config_manager_dcim import CableStatus
from nv_config_manager_dcim.workflow_models import InterfaceData, Platform
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from nv_config_manager_workflows.activities import cable_validation
from nv_config_manager_workflows.activities.cable_validation import (
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
from nv_config_manager_workflows.clients.device.cumulus import CumulusConnection
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
@responses.activate
@pytest.mark.parametrize("firmware", ["5.14.0", "5.15.0"])
@pytest.mark.parametrize(("family", "address"), [("ipv4", "192.0.2.10"), ("ipv6", "2001:db8::10")])
@pytest.mark.parametrize(
    ("intended_mac", "expected_status"),
    [("00-11-22-33-44-55", CableStatus.CONNECTED), ("00-11-22-33-44-66", CableStatus.INVALID)],
)
async def test_cumulus_neighbors_support_mac_matching_without_lldp_or_fdb(
    firmware: str, family: str, address: str, intended_mac: str, expected_status: CableStatus
) -> None:
    conn = CumulusConnection(
        "192.0.2.1", settings={"username": "admin", "passwords": ["password"], "mock": False}
    )
    base = "https://192.0.2.1:8765/nvue_v1"
    responses.get(f"{base}/system", json={"version": {"product-release": firmware}})
    responses.get(f"{base}/interface", json={"swp1": {"type": "swp"}})
    neighbors = {address: {"lladdr": "00:11:22:33:44:55"}}
    if firmware == "5.14.0":
        responses.get(f"{base}/interface/swp1/ip/neighbor", json={family: neighbors})
    else:
        for current_family in ("ipv4", "ipv6"):
            responses.get(
                f"{base}/interface/swp1/{current_family}/neighbor",
                json=neighbors if current_family == family else {},
            )

    result = await validate_device_neighbors(
        ValidateDeviceNeighborsInput(
            device=NetworkDeviceData(
                id="device-1",
                name="leaf-1",
                role="leaf",
                site="site-1",
                device_type="switch",
                platform=Platform.CUMULUS_LINUX,
                primary_ip4="192.0.2.1",
                primary_ip6=None,
            ),
            intended=DeviceNeighborData(
                neighbors={
                    "swp1": InterfaceNeighborData(
                        device_name="host-1", device_role="server", name="eth0", macs=[intended_mac]
                    )
                }
            ),
            actual=DeviceNeighborData(link_states={"swp1": True}),
            mac_table=DeviceMacTable(),
            arp_table=conn.get_arp_table(),
        )
    )

    assert result.cable_statuses == {"swp1": expected_status}


class TestPortDescriptionValidity:
    """Validation of hyper-reach cabling via LLDP port description."""

    TEST_DEVICE = NetworkDeviceData(
        id="device-1",
        name="mock-switch",
        role="leaf",
        site="site-1",
        device_type="switch",
        platform=Platform.CUMULUS_LINUX,
        primary_ip4="192.0.2.1",
        primary_ip6=None,
    )

    @pytest.mark.asyncio
    async def test_matching_port_description_is_valid(self) -> None:
        """A port description matching the intended chassis and lane is a valid cable."""
        intended = DeviceNeighborData(
            neighbors={
                "swp1": InterfaceNeighborData(
                    label="C1-2-L1",
                    device_name="MOCK-GPU-TRAY-01",
                    device_role="gpu",
                )
            },
        )
        actual = DeviceNeighborData(
            neighbors={
                "swp1": InterfaceNeighborData(
                    name="A56p0",
                    device_name="mock-dpu-node",
                    port_description="MOCK-GPU-TRAY-01 C1-2-L1",
                )
            },
            link_states={"swp1": True},
        )

        result = await validate_device_neighbors(
            ValidateDeviceNeighborsInput(
                device=self.TEST_DEVICE,
                intended=intended,
                actual=actual,
                mac_table=DeviceMacTable(),
                arp_table=DeviceArpTable(),
            )
        )

        assert result.interfaces == {}

    @pytest.mark.asyncio
    async def test_mismatched_port_description_is_invalid(self) -> None:
        """A port description naming a different device is an invalid cable."""
        intended = DeviceNeighborData(
            neighbors={
                "swp1": InterfaceNeighborData(
                    label="C1-2-L1",
                    device_name="MOCK-GPU-TRAY-01",
                    device_role="gpu",
                )
            },
        )
        actual = DeviceNeighborData(
            neighbors={
                "swp1": InterfaceNeighborData(
                    name="A56p0",
                    device_name="mock-dpu-node",
                    port_description="MOCK-GPU-TRAY-02 C1-2-L1",
                )
            },
            link_states={"swp1": True},
        )

        result = await validate_device_neighbors(
            ValidateDeviceNeighborsInput(
                device=self.TEST_DEVICE,
                intended=intended,
                actual=actual,
                mac_table=DeviceMacTable(),
                arp_table=DeviceArpTable(),
            )
        )

        assert "swp1" in result.interfaces

    @pytest.mark.asyncio
    async def test_port_description_matches_chassis_alias_over_device_name(self) -> None:
        """A line card's chassis alias, not its own device name, matches the port description."""
        intended = DeviceNeighborData(
            neighbors={
                "swp1": InterfaceNeighborData(
                    label="C1-2-L1",
                    device_name="MOCK-GPU-TRAY-01-pcie1",
                    chassis_name="MOCK-GPU-TRAY-01",
                    device_role="gpu",
                )
            },
        )
        actual = DeviceNeighborData(
            neighbors={
                "swp1": InterfaceNeighborData(
                    name="A56p0",
                    device_name="mock-dpu-node",
                    port_description="MOCK-GPU-TRAY-01 C1-2-L1",
                )
            },
            link_states={"swp1": True},
        )

        result = await validate_device_neighbors(
            ValidateDeviceNeighborsInput(
                device=self.TEST_DEVICE,
                intended=intended,
                actual=actual,
                mac_table=DeviceMacTable(),
                arp_table=DeviceArpTable(),
            )
        )

        assert result.interfaces == {}


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
@pytest.mark.parametrize("failure_point", ["client", "provider"])
async def test_update_cable_statuses_keeps_permanent_errors_manually_retryable(
    monkeypatch: pytest.MonkeyPatch, failure_point: str
) -> None:
    failure = ApplicationError("DCIM unavailable", "detail", type="DCIMError", non_retryable=True)

    class FailingClient:
        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def update_cable_statuses(self, updates: Any) -> None:
            raise failure

    def get_client() -> Any:
        if failure_point == "client":
            raise failure
        return FailingClient()

    monkeypatch.setattr(cable_validation_activities, "get_dcim_client", get_client)

    with pytest.raises(ApplicationError) as raised:
        await update_cable_statuses(
            UpdateCableStatusesInput(
                device_id="device-1",
                cable_statuses={"Ethernet1/1": CableStatus.CONNECTED},
                workflow_id="workflow-1",
            )
        )

    assert raised.value.non_retryable is False
    assert raised.value.type == "DCIMError"
    assert raised.value.message == "DCIM unavailable"
    assert raised.value.details == ("detail",)
    assert raised.value.__cause__ is failure


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
async def test_bulk_only_provider_receives_all_changes_in_one_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SupportedClient:
        def __init__(self) -> None:
            self.calls: list[Any] = []

        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def update_cable_statuses(self, updates: Any) -> None:
            self.calls.append(updates)

    client = SupportedClient()
    monkeypatch.setattr(cable_validation_activities, "get_dcim_client", lambda: client)

    await update_cable_statuses(
        UpdateCableStatusesInput(
            device_id="device-1",
            cable_statuses={f"Ethernet1/{index}": CableStatus.CONNECTED for index in range(500)},
            workflow_id="workflow-1",
        )
    )

    assert len(client.calls) == 1
    assert len(client.calls[0]) == 500
    assert {update.interface_name for update in client.calls[0]} == {
        f"Ethernet1/{index}" for index in range(500)
    }
    assert all(
        update.device_id == "device-1"
        and update.workflow_id == "workflow-1"
        and update.status == CableStatus.CONNECTED
        for update in client.calls[0]
    )


@pytest.mark.asyncio
async def test_single_update_provider_keeps_ten_concurrent_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = 0
    maximum_active = 0

    # Use a concrete class so the optional bulk capability is absent.
    class SingleClient:
        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def update_cable_status(self, update: Any) -> None:
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            await asyncio.sleep(0)
            active -= 1

    client = SingleClient()
    monkeypatch.setattr(cable_validation_activities, "get_dcim_client", lambda: client)
    await update_cable_statuses(
        UpdateCableStatusesInput(
            device_id="device-1",
            workflow_id="workflow-1",
            cable_statuses={f"Ethernet1/{index}": CableStatus.CONNECTED for index in range(30)},
        )
    )
    assert maximum_active == 10


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", ["success", "error", "cancel"])
async def test_persistence_heartbeats_continue_and_stop_with_the_activity(
    monkeypatch: pytest.MonkeyPatch, finish: str
) -> None:
    environment = ActivityEnvironment()
    heartbeats: list[tuple[Any, ...]] = []
    environment.on_heartbeat = lambda *details: heartbeats.append(details)
    release = asyncio.Event()
    failure = ValueError("provider failed")

    class BulkClient:
        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def update_cable_statuses(self, updates: Any) -> None:
            await release.wait()
            if finish == "error":
                raise failure

    monkeypatch.setattr(cable_validation_activities, "get_dcim_client", BulkClient)
    monkeypatch.setattr(
        cable_validation_activities, "_CABLE_STATUS_HEARTBEAT_INTERVAL_SECONDS", 0.01
    )
    task = asyncio.create_task(
        environment.run(
            update_cable_statuses,
            UpdateCableStatusesInput(
                device_id="device-1",
                workflow_id="workflow-1",
                cable_statuses={"Ethernet1/1": CableStatus.CONNECTED},
            ),
        )
    )
    try:
        async with asyncio.timeout(2):
            while len(heartbeats) < 2:
                await asyncio.sleep(0.005)
        assert heartbeats[-1] == ({"device_id": "device-1", "interface_count": 1},)
        if finish == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            release.set()
            if finish == "error":
                with pytest.raises(ValueError) as raised:
                    await task
                assert raised.value is failure
            else:
                await task
        count = len(heartbeats)
        await asyncio.sleep(0.03)
        assert len(heartbeats) == count
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


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
