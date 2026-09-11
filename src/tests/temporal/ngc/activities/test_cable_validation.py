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
"""Test suite for cable validation activities."""

import asyncio
import base64
import io
from typing import Any

import pandas as pd
import pytest
from aioresponses import aioresponses
from nv_config_manager_dcim import CableStatus

from nv_config_manager.temporal.client.device import (
    DeviceArpTable,
    DeviceMacTable,
    DeviceNeighborData,
    InterfaceNeighborData,
)
from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData
from nv_config_manager.temporal.ngc.activities.cable_validation import (
    CABLE_STATUS_UPDATE_CONCURRENCY,
    CSV_EXCLUDE_COLUMNS,
    HOST_SUMMARY_COLUMNS,
    MARKDOWN_EXCLUDE_COLUMNS,
    CableValidationResultData,
    CableValidationRow,
    DecorateResultActivityInput,
    DecorateResultActivityOutput,
    InvalidCable,
    UpdateCableStatusesInput,
    ValidateDeviceNeighborsInput,
    _build_host_summary,
    _classify_issue,
    _escape_formula,
    _format_results_markdown,
    _generate_csv_link,
    _generate_xlsx_link,
    decorate_result,
    update_cable_statuses,
    validate_device_neighbors,
)
from tests.temporal.ngc.activities.test_cable_validation_activity_data import (
    NAUTOBOT_INTERFACE_RESPONSE,
    VALIDATION_RESULTS,
)


def construct_input(json: Any) -> DecorateResultActivityInput:
    return DecorateResultActivityInput(
        devices={device: CableValidationResultData(**data) for device, data in json.items()}
    )


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
        platform="cumulus-linux",
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
async def test_update_cable_statuses_is_noop_for_unsupported_provider(monkeypatch) -> None:
    class UnsupportedClient:
        def __init__(self) -> None:
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    client = UnsupportedClient()
    monkeypatch.setattr(
        "nv_config_manager.temporal.ngc.activities.cable_validation.create_dcim_client",
        lambda: client,
    )

    await update_cable_statuses(
        UpdateCableStatusesInput(
            device_id="device-1",
            cable_statuses={"Ethernet1/1": CableStatus.CONNECTED},
            workflow_id="workflow-1",
        )
    )

    assert client.closed is True


@pytest.mark.asyncio
async def test_update_cable_statuses_passes_workflow_provenance(monkeypatch) -> None:
    class SupportedClient:
        def __init__(self) -> None:
            self.updates = []

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
    monkeypatch.setattr(
        "nv_config_manager.temporal.ngc.activities.cable_validation.create_dcim_client",
        lambda: client,
    )

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
async def test_update_cable_statuses_writes_concurrently(monkeypatch) -> None:
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
    monkeypatch.setattr(
        "nv_config_manager.temporal.ngc.activities.cable_validation.create_dcim_client",
        lambda: client,
    )

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
async def test_decorate_results():
    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload=NAUTOBOT_INTERFACE_RESPONSE,
        )

        result = await decorate_result(construct_input(VALIDATION_RESULTS))

    assert result == DecorateResultActivityOutput(
        devices={
            "AZ50-AG422-GW-02": CableValidationResultData(
                interfaces={
                    "swp31": InvalidCable(
                        intended=None,
                        actual=InterfaceNeighborData(
                            name="Ethernet17/1",
                            macs=[],
                            device_name="TYO27-0101-0801-01T0",
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=None,
                            ts_info=None,
                        ),
                    ),
                    "swp32": InvalidCable(
                        intended=None,
                        actual=InterfaceNeighborData(
                            name="Ethernet18/1",
                            macs=[],
                            device_name="TYO27-0101-0801-01T0",
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=None,
                            ts_info=None,
                        ),
                    ),
                    "swp33": InvalidCable(
                        intended=None,
                        actual=InterfaceNeighborData(
                            name="Ethernet19/1",
                            macs=[],
                            device_name="TYO27-0101-0801-01T0",
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=None,
                            ts_info=None,
                        ),
                    ),
                    "swp34": InvalidCable(
                        intended=None,
                        actual=InterfaceNeighborData(
                            name="Ethernet20/1",
                            macs=[],
                            device_name="TYO27-0101-0801-01T0",
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=None,
                            ts_info=None,
                        ),
                    ),
                    "swp35": InvalidCable(
                        intended=None,
                        actual=InterfaceNeighborData(
                            name="Ethernet21/1",
                            macs=[],
                            device_name="TYO27-0101-0801-01T0",
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=None,
                            ts_info=None,
                        ),
                    ),
                    "swp36": InvalidCable(
                        intended=None,
                        actual=InterfaceNeighborData(
                            name="Ethernet22/1",
                            macs=[],
                            device_name="TYO27-0101-0801-01T0",
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=None,
                            ts_info=None,
                        ),
                    ),
                    "swp37": InvalidCable(
                        intended=None,
                        actual=InterfaceNeighborData(
                            name="Ethernet23/1",
                            macs=[],
                            device_name="TYO27-0101-0801-01T0",
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=None,
                            ts_info=None,
                        ),
                    ),
                    "swp38": InvalidCable(
                        intended=None,
                        actual=InterfaceNeighborData(
                            name="Ethernet24/1",
                            macs=[],
                            device_name="TYO27-0101-0801-01T0",
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=None,
                            ts_info=None,
                        ),
                    ),
                    "swp39": InvalidCable(
                        intended=None,
                        actual=InterfaceNeighborData(
                            name="Ethernet25/1",
                            macs=[],
                            device_name="TYO27-0101-0801-01T0",
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=None,
                            ts_info=None,
                        ),
                    ),
                    "swp40": InvalidCable(
                        intended=None,
                        actual=InterfaceNeighborData(
                            name="Ethernet26/1",
                            macs=[],
                            device_name="TYO27-0101-0801-01T0",
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=None,
                            ts_info=None,
                        ),
                    ),
                    "swp41": InvalidCable(
                        intended=None,
                        actual=InterfaceNeighborData(
                            name="Ethernet27/1",
                            macs=[],
                            device_name="TYO27-0101-0801-01T0",
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=None,
                            ts_info=None,
                        ),
                    ),
                    "swp42": InvalidCable(
                        intended=None,
                        actual=InterfaceNeighborData(
                            name="Ethernet28/1",
                            macs=[],
                            device_name="TYO27-0101-0801-01T0",
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=None,
                            ts_info=None,
                        ),
                    ),
                    "swp43": InvalidCable(
                        intended=None,
                        actual=InterfaceNeighborData(
                            name="Ethernet29/1",
                            macs=[],
                            device_name="TYO27-0101-0801-01T0",
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=None,
                            ts_info=None,
                        ),
                    ),
                    "swp44": InvalidCable(
                        intended=None,
                        actual=InterfaceNeighborData(
                            name="Ethernet30/1",
                            macs=[],
                            device_name="TYO27-0101-0801-01T0",
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=None,
                            ts_info=None,
                        ),
                    ),
                    "swp45": InvalidCable(
                        intended=None,
                        actual=InterfaceNeighborData(
                            name="Ethernet31/1",
                            macs=[],
                            device_name="TYO27-0101-0801-01T0",
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=None,
                            ts_info=None,
                        ),
                    ),
                    "swp46": InvalidCable(
                        intended=None,
                        actual=InterfaceNeighborData(
                            name="Ethernet32/1",
                            macs=[],
                            device_name="TYO27-0101-0801-01T0",
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=None,
                            ts_info=None,
                        ),
                    ),
                    "swp47": InvalidCable(
                        intended=None,
                        actual=InterfaceNeighborData(
                            name="Ethernet17/1",
                            macs=[],
                            device_name="TYO27-0101-0801-02T0",
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=None,
                            ts_info=None,
                        ),
                    ),
                    "swp48": InvalidCable(
                        intended=None,
                        actual=InterfaceNeighborData(
                            name="Ethernet18/1",
                            macs=[],
                            device_name="TYO27-0101-0801-02T0",
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=None,
                            ts_info=None,
                        ),
                    ),
                    "swp49": InvalidCable(
                        intended=None,
                        actual=InterfaceNeighborData(
                            name="Ethernet19/1",
                            macs=[],
                            device_name="TYO27-0101-0801-02T0",
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=None,
                            ts_info=None,
                        ),
                    ),
                    "swp50": InvalidCable(
                        intended=None,
                        actual=InterfaceNeighborData(
                            name="Ethernet20/1",
                            macs=[],
                            device_name="TYO27-0101-0801-02T0",
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=None,
                            ts_info=None,
                        ),
                    ),
                    "swp51": InvalidCable(
                        intended=None,
                        actual=InterfaceNeighborData(
                            name="Ethernet21/1",
                            macs=[],
                            device_name="TYO27-0101-0801-02T0",
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=None,
                            ts_info=None,
                        ),
                    ),
                    "swp52": InvalidCable(
                        intended=None,
                        actual=InterfaceNeighborData(
                            name="Ethernet22/1",
                            macs=[],
                            device_name="TYO27-0101-0801-02T0",
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=None,
                            ts_info=None,
                        ),
                    ),
                    "swp53": InvalidCable(
                        intended=None,
                        actual=InterfaceNeighborData(
                            name="Ethernet23/1",
                            macs=[],
                            device_name="TYO27-0101-0801-02T0",
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=None,
                            ts_info=None,
                        ),
                    ),
                    "swp54": InvalidCable(
                        intended=None,
                        actual=InterfaceNeighborData(
                            name="Ethernet24/1",
                            macs=[],
                            device_name="TYO27-0101-0801-02T0",
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=None,
                            ts_info=None,
                        ),
                    ),
                    "swp55": InvalidCable(
                        intended=None,
                        actual=InterfaceNeighborData(
                            name="Ethernet25/1",
                            macs=[],
                            device_name="TYO27-0101-0801-02T0",
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=None,
                            ts_info=None,
                        ),
                    ),
                    "swp56": InvalidCable(
                        intended=None,
                        actual=InterfaceNeighborData(
                            name="Ethernet26/1",
                            macs=[],
                            device_name="TYO27-0101-0801-02T0",
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=None,
                            ts_info=None,
                        ),
                    ),
                    "swp57": InvalidCable(
                        intended=None,
                        actual=InterfaceNeighborData(
                            name="Ethernet27/1",
                            macs=[],
                            device_name="TYO27-0101-0801-02T0",
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=None,
                            ts_info=None,
                        ),
                    ),
                    "swp58": InvalidCable(
                        intended=None,
                        actual=InterfaceNeighborData(
                            name="Ethernet28/1",
                            macs=[],
                            device_name="TYO27-0101-0801-02T0",
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=None,
                            ts_info=None,
                        ),
                    ),
                    "swp59": InvalidCable(
                        intended=None,
                        actual=InterfaceNeighborData(
                            name="Ethernet29/1",
                            macs=[],
                            device_name="TYO27-0101-0801-02T0",
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=None,
                            ts_info=None,
                        ),
                    ),
                    "swp60": InvalidCable(
                        intended=None,
                        actual=InterfaceNeighborData(
                            name="Ethernet30/1",
                            macs=[],
                            device_name="TYO27-0101-0801-02T0",
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=None,
                            ts_info=None,
                        ),
                    ),
                    "swp61": InvalidCable(
                        intended=None,
                        actual=InterfaceNeighborData(
                            name="Ethernet31/1",
                            macs=[],
                            device_name="TYO27-0101-0801-02T0",
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=None,
                            ts_info=None,
                        ),
                    ),
                    "swp62": InvalidCable(
                        intended=None,
                        actual=InterfaceNeighborData(
                            name="Ethernet32/1",
                            macs=[],
                            device_name="TYO27-0101-0801-02T0",
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=None,
                            ts_info=None,
                        ),
                    ),
                },
                device=None,
            ),
            "AZ50-AG422-LEAF-01": CableValidationResultData(interfaces={}, device=None),
            "AZ50-AN422-IPMITOR-03": CableValidationResultData(
                interfaces={
                    "swp34": InvalidCable(
                        intended=InterfaceNeighborData(
                            name="Server BMC",
                            macs=["08-8F-C3-A6-35-F5"],
                            device_name="AZ50-AT422-OVX-Server-01",
                            device_serial="J701C0T7",
                            device_role="tenant-a-device",
                            device_rack=None,
                            device_position=None,
                            link_up=None,
                            ts_info=None,
                        ),
                        actual=InterfaceNeighborData(
                            name="DPU BMC",
                            macs=[
                                "B8-3F-D2-E9-B1-48",
                                "B8-3F-D2-E9-B1-54",
                                "FC-6A-1C-05-BD-41",
                            ],
                            device_name="AZ50-AT422-OVX-Server-01-dpu0",
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=True,
                            ts_info=None,
                        ),
                    ),
                    "swp35": InvalidCable(
                        intended=None,
                        actual=InterfaceNeighborData(
                            name="p1",
                            macs=[],
                            device_name="gpu56-gp1-cin1-sitea-dpu39",
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=True,
                            ts_info=None,
                        ),
                    ),
                },
                device=None,
            ),
            "AZ50-AO425-IPMITOR-03": CableValidationResultData(
                interfaces={
                    "swp19": InvalidCable(
                        intended=InterfaceNeighborData(
                            name="DPU BMC",
                            macs=["A0-88-C2-9B-22-60"],
                            device_name="AZ50-AZ431-OVX-Server-02-dpu0",
                            device_serial="0",
                            device_role="tenant-a-device",
                            device_rack=None,
                            device_position=None,
                            link_up=None,
                            ts_info=None,
                        ),
                        actual=InterfaceNeighborData(
                            name="FC-6A-1C-05-8A-6E",
                            macs=["FC-6A-1C-05-8A-6E"],
                            device_name=None,
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=False,
                            ts_info=None,
                        ),
                    ),
                    "swp23": InvalidCable(
                        intended=InterfaceNeighborData(
                            name="DPU BMC",
                            macs=[],
                            device_name="AZ50-AJ434-OVX-Server-02-dpu0",
                            device_serial="0",
                            device_role="tenant-a-device",
                            device_rack=None,
                            device_position=None,
                            link_up=None,
                            ts_info=None,
                        ),
                        actual=InterfaceNeighborData(
                            name="A0-88-C2-00-B5-F2",
                            macs=[
                                "A0-88-C2-00-B5-F2",
                                "A0-88-C2-00-B5-FE",
                                "FC-6A-1C-05-8A-6A",
                            ],
                            device_name=None,
                            device_serial=None,
                            device_role=None,
                            device_rack=None,
                            device_position=None,
                            link_up=True,
                            ts_info=None,
                        ),
                    ),
                },
                device=None,
            ),
        }
    )


def _row(
    start_device: str,
    issue: str,
    start_rack: str | None = None,
) -> CableValidationRow:
    """Build a minimal CableValidationRow for summary/export tests."""
    return CableValidationRow(start_device=start_device, start_rack=start_rack, issue=issue)


def _decode_workbook(link: str) -> dict[str, pd.DataFrame]:
    """Decode a '[Download Excel](data:...base64,...)' link into {sheet_name: DataFrame}."""
    assert link.startswith("[Download Excel](data:")
    b64 = link.split("base64,", 1)[1].rstrip(")")
    raw = base64.b64decode(b64)
    return pd.read_excel(io.BytesIO(raw), sheet_name=None)


class TestClassifyIssue:
    """Issue-string bucketing for the host summary."""

    def test_link_down_is_missing(self):
        assert _classify_issue("Link is down.") == "missing"

    def test_link_up_no_neighbor_is_missing(self):
        assert _classify_issue("Link is up but no neighbor found") == "missing"

    def test_incorrect_cabling_is_miscabled(self):
        issue = "Incorrect cabling, actual should match intended. Based on LLDP data"
        assert _classify_issue(issue) == "miscabled"

    def test_unexpected_connection(self):
        assert _classify_issue("Unexpected connection found") == "unexpected"

    def test_none_is_other(self):
        assert _classify_issue(None) == "other"

    def test_unknown_is_other(self):
        assert _classify_issue("some new issue") == "other"


class TestBuildHostSummary:
    """Per-host aggregation and triage ordering."""

    def test_counts_categories_per_host(self):
        rows = [
            _row("hostA", "Link is down.", "rack1:u1"),
            _row("hostA", "Link is up but no neighbor found", "rack1:u1"),
            _row("hostA", "Unexpected connection found", "rack1:u1"),
            _row(
                "hostB",
                "Incorrect cabling, actual should match intended. Based on LLDP data",
                "rack2:u3",
            ),
        ]

        by_host = {r["Host"]: r for r in _build_host_summary(rows)}

        assert by_host["hostA"]["Missing Cables"] == 2
        assert by_host["hostA"]["Unexpected Connections"] == 1
        assert by_host["hostA"]["Miscabled"] == 0
        assert by_host["hostA"]["Total Issues"] == 3
        assert by_host["hostA"]["Rack"] == "rack1:u1"
        assert by_host["hostB"]["Miscabled"] == 1
        assert by_host["hostB"]["Missing Cables"] == 0

    def test_sorted_by_missing_then_total(self):
        rows = [
            _row("low", "Link is down."),
            _row("high", "Link is down."),
            _row("high", "Link is up but no neighbor found"),
            _row("mid_total", "Link is down."),
            _row("mid_total", "Unexpected connection found"),
        ]

        hosts = [r["Host"] for r in _build_host_summary(rows)]

        # high: 2 missing; then 1-missing hosts broken by total desc (mid_total=2, low=1)
        assert hosts == ["high", "mid_total", "low"]

    def test_rack_falls_back_to_first_populated_value(self):
        rows = [
            _row("hostA", "Link is down.", None),
            _row("hostA", "Link is down.", "rack9:u9"),
        ]

        summary = _build_host_summary(rows)

        assert summary[0]["Rack"] == "rack9:u9"

    def test_includes_queried_switches_with_zero_issues(self):
        devices = {
            "switchA": CableValidationResultData(interfaces={}, device=None),
            "switchB": CableValidationResultData(interfaces={}, device=None),
        }
        rows = [_row("switchA", "Link is down.", "rack1:u1")]

        summary = _build_host_summary(rows, devices)
        by_host = {r["Host"]: r for r in summary}

        assert by_host["switchB"]["Missing Cables"] == 0
        assert by_host["switchB"]["Total Issues"] == 0
        assert by_host["switchA"]["Missing Cables"] == 1
        # switchA (1 missing) sorts ahead of healthy switchB (0).
        assert summary[0]["Host"] == "switchA"
        assert summary[-1]["Host"] == "switchB"

    def test_empty_results(self):
        assert _build_host_summary([]) == []


class TestGenerateXlsxLink:
    """The two-tab Excel download."""

    def test_has_detail_and_summary_sheets(self):
        rows = [
            _row("hostA", "Link is down.", "rack1:u1"),
            _row("hostA", "Link is up but no neighbor found", "rack1:u1"),
            _row("hostB", "Unexpected connection found", "rack2:u2"),
        ]

        sheets = _decode_workbook(_generate_xlsx_link(rows))

        assert set(sheets) == {"Cable Issues", "Host Summary"}
        assert len(sheets["Cable Issues"]) == 3

        summary = sheets["Host Summary"]
        assert list(summary.columns) == list(HOST_SUMMARY_COLUMNS)
        # hostA has the most missing cables, so it sorts first.
        assert summary.iloc[0]["Host"] == "hostA"
        assert summary.iloc[0]["Missing Cables"] == 2

    def test_empty_results_still_valid_workbook(self):
        sheets = _decode_workbook(_generate_xlsx_link([]))

        assert set(sheets) == {"Cable Issues", "Host Summary"}
        assert len(sheets["Host Summary"]) == 0

    def test_summary_lists_all_queried_switches(self):
        devices = {
            "switchA": CableValidationResultData(interfaces={}, device=None),
            "switchB": CableValidationResultData(interfaces={}, device=None),
        }
        rows = [_row("switchA", "Link is down.", "rack1:u1")]

        sheets = _decode_workbook(_generate_xlsx_link(rows, devices))
        summary = sheets["Host Summary"]

        assert set(summary["Host"]) == {"switchA", "switchB"}
        assert summary.iloc[0]["Host"] == "switchA"


class TestFormatResultsMarkdownExport:
    """Export-link selection in the shared markdown formatter."""

    def test_xlsx_export_uses_excel_link(self):
        rows = [_row("hostA", "Link is down.", "rack1:u1")]

        markdown = _format_results_markdown(
            rows,
            csv_exclude_columns=CSV_EXCLUDE_COLUMNS,
            markdown_exclude_columns=MARKDOWN_EXCLUDE_COLUMNS,
            export="xlsx",
        )

        assert "[Download Excel](data:application/vnd.openxmlformats" in markdown
        assert "[Export to CSV]" not in markdown

    def test_csv_export_is_default(self):
        rows = [_row("hostA", "Link is down.", "rack1:u1")]

        markdown = _format_results_markdown(
            rows,
            csv_exclude_columns=CSV_EXCLUDE_COLUMNS,
            markdown_exclude_columns=MARKDOWN_EXCLUDE_COLUMNS,
        )

        assert "[Export to CSV](data:text/csv" in markdown

    def test_too_many_results_names_excel_for_xlsx_export(self):
        rows = [_row(f"host{i}", "Link is down.", "rack1:u1") for i in range(3)]

        markdown = _format_results_markdown(
            rows,
            csv_exclude_columns=CSV_EXCLUDE_COLUMNS,
            markdown_exclude_columns=MARKDOWN_EXCLUDE_COLUMNS,
            max_display_results=1,
            export="xlsx",
        )

        assert "please export to Excel to view." in markdown
        assert "export to CSV to view" not in markdown

    def test_too_many_results_names_csv_for_csv_export(self):
        rows = [_row(f"host{i}", "Link is down.", "rack1:u1") for i in range(3)]

        markdown = _format_results_markdown(
            rows,
            csv_exclude_columns=CSV_EXCLUDE_COLUMNS,
            markdown_exclude_columns=MARKDOWN_EXCLUDE_COLUMNS,
            max_display_results=1,
        )

        assert "please export to CSV to view." in markdown


def _decode_csv(link: str) -> pd.DataFrame:
    """Decode an '[Export to CSV](data:...base64,...)' link into a DataFrame."""
    assert link.startswith("[Export to CSV](data:")
    b64 = link.split("base64,", 1)[1].rstrip(")")
    raw = base64.b64decode(b64).decode("utf-8")
    # keep_default_na=False so escaped text is compared as-is, not coerced to NaN.
    return pd.read_csv(io.StringIO(raw), dtype=str, keep_default_na=False)


class TestEscapeFormula:
    """Formula-injection escaping for spreadsheet exports."""

    @pytest.mark.parametrize("trigger", ["=", "+", "-", "@", "\t", "\r"])
    def test_leading_trigger_is_prefixed_with_quote(self, trigger: str):
        payload = f"{trigger}SUM(A1)"

        assert _escape_formula(payload) == f"'{payload}"

    def test_benign_string_is_unchanged(self):
        assert _escape_formula("hostA") == "hostA"
        assert _escape_formula("rack1:u1") == "rack1:u1"

    def test_trigger_not_leading_is_unchanged(self):
        assert _escape_formula("host=A") == "host=A"

    def test_empty_string_is_unchanged(self):
        assert _escape_formula("") == ""

    def test_non_string_passes_through(self):
        assert _escape_formula(None) is None
        assert _escape_formula(42) == 42


class TestExportsEscapeFormulas:
    """Both the CSV and Excel exports must neutralize formula-like cell values."""

    def test_to_csv_dict_escapes_leading_formula_char(self):
        row = _row("=cmd|'/c calc'!A1", "Link is down.", "rack1:u1")

        assert row.to_csv_dict()["Start Device"] == "'=cmd|'/c calc'!A1"

    def test_xlsx_cell_is_not_interpreted_as_formula(self):
        rows = [_row("=1+2", "Link is down.", "rack1:u1")]

        sheets = _decode_workbook(_generate_xlsx_link(rows))
        detail = sheets["Cable Issues"]

        assert detail.iloc[0]["Start Device"] == "'=1+2"

    def test_csv_cell_is_escaped(self):
        rows = [_row("@evil", "Link is down.", "rack1:u1")]

        detail = _decode_csv(_generate_csv_link(rows))

        assert detail.iloc[0]["Start Device"] == "'@evil"
