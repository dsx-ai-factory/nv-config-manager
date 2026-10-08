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
"""Models and serialization constants for cable-validation activities."""

from __future__ import annotations

import hashlib
from typing import Any

from nv_config_manager_dcim import CableStatus
from nv_config_manager_dcim.workflow_models import NetworkDeviceData
from pydantic import BaseModel, ConfigDict, Field

from nv_config_manager_workflows.clients.device.models import (
    DeviceArpTable,
    DeviceMacTable,
    DeviceNeighborData,
    InterfaceNeighborData,
)

CABLE_STATUS_UPDATE_CONCURRENCY = 10
CSV_EXCLUDE_COLUMNS: frozenset[str] = frozenset({"Troubleshooting Info"})
MARKDOWN_EXCLUDE_COLUMNS: frozenset[str] = frozenset(
    {"Troubleshooting Info", "Start Rack", "Intended End Rack", "ID"}
)
LINK_UP_NO_NEIGHBOR_MSG = "Link is up but no neighbor found"
LINK_DOWN_MSG = "Link is down."
UNEXPECTED_CONNECTION_MSG = "Unexpected connection found"
INCORRECT_CABLING_PREFIX = "Incorrect cabling"
HOST_SUMMARY_COLUMNS: tuple[str, ...] = (
    "Host",
    "Rack",
    "Missing Cables",
    "Miscabled",
    "Unexpected Connections",
    "Total Issues",
)
DETAIL_SHEET_NAME = "Cable Issues"
HOST_SUMMARY_SHEET_NAME = "Host Summary"
EXCEL_MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_FORMULA_TRIGGERS: frozenset[str] = frozenset({"=", "+", "-", "@", "\t", "\r"})


def _escape_formula(value: Any) -> Any:
    """Neutralize spreadsheet formula injection for string cell values."""
    if isinstance(value, str) and value and value[0] in _FORMULA_TRIGGERS:
        return f"'{value}"
    return value


class ValidateDeviceNeighborsInput(BaseModel):
    """Input for the device neighbor validation activity."""

    device: NetworkDeviceData
    intended: DeviceNeighborData
    actual: DeviceNeighborData
    mac_table: DeviceMacTable
    arp_table: DeviceArpTable
    ignore_no_neighbor: bool = False


class InvalidCable(BaseModel, validate_assignment=True):
    """Invalid Cable for Validation Results."""

    intended: InterfaceNeighborData | None = None
    actual: InterfaceNeighborData | None = None


class ValidateDeviceNeighborsResult(BaseModel, validate_assignment=True):
    """Result for a Device Cable Validation."""

    # key is the interface name
    interfaces: dict[str, InvalidCable] = {}
    cable_statuses: dict[str, CableStatus] = {}


class UpdateCableStatusesInput(BaseModel):
    """Cable statuses determined for one validated device."""

    device_id: str
    cable_statuses: dict[str, CableStatus]
    workflow_id: str


class CableValidationRow(BaseModel):
    """A single row of cable validation results with serialization for CSV/markdown."""

    model_config = ConfigDict(populate_by_name=True)

    start_device: str | None = Field(default=None, alias="Start Device")
    start_port: str | None = Field(default=None, alias="Start Port")
    start_rack: str | None = Field(default=None, alias="Start Rack")
    intended_end_device: str | None = Field(default=None, alias="Intended End Device")
    intended_end_port: str | None = Field(default=None, alias="Intended End Port")
    intended_end_rack: str | None = Field(default=None, alias="Intended End Rack")
    actual_end_device: str | None = Field(default=None, alias="Actual End Device")
    actual_end_port: str | None = Field(default=None, alias="Actual End Port")
    issue: str | None = Field(default=None, alias="Issue")
    troubleshooting_info: str | None = Field(default=None, alias="Troubleshooting Info")
    id_: str | None = Field(default=None, alias="ID")

    def to_markdown(self) -> dict[str, Any]:
        """Return dict of columns to include in markdown table (excludes MARKDOWN_EXCLUDE_COLUMNS)."""
        return {
            k: v
            for k, v in self.model_dump(by_alias=True).items()
            if k not in MARKDOWN_EXCLUDE_COLUMNS
        }

    def to_csv_dict(self) -> dict[str, Any]:
        """Return columns for CSV/Excel export, formula-escaped (excludes CSV_EXCLUDE_COLUMNS)."""
        return {
            k: _escape_formula(v)
            for k, v in self.model_dump(by_alias=True).items()
            if k not in CSV_EXCLUDE_COLUMNS
        }

    @classmethod
    def compute_id(cls, row: CableValidationRow) -> str:
        """Compute hash ID for this row."""
        return hashlib.md5(
            (
                str(row.start_device)
                + str(row.start_port)
                + str(row.start_rack)
                + str(row.intended_end_device)
                + str(row.intended_end_port)
                + str(row.intended_end_rack)
                + str(row.actual_end_device)
                + str(row.actual_end_port)
            ).encode("utf-8"),
            usedforsecurity=False,
        ).hexdigest()


class CableValidationResultData(BaseModel):
    """Cable validation result data."""

    interfaces: dict[str, InvalidCable]
    device: NetworkDeviceData | None = None


class DecorateResultActivityInput(BaseModel):
    """Decorate result activity input."""

    devices: dict[str, CableValidationResultData]


class DecorateResultActivityOutput(BaseModel):
    """Decorate result activity input."""

    devices: dict[str, CableValidationResultData]


class FormatResultsActivityInput(BaseModel):
    """Format results activity input."""

    devices: dict[str, CableValidationResultData]
    failed_devices: dict[str, str]
    ignore_no_neighbor: bool = False


class FormatDeviceValidationResultInput(BaseModel):
    """Format device validation result activity input."""

    device: NetworkDeviceData
    validation_result: ValidateDeviceNeighborsResult
    ignore_no_neighbor: bool = False


__all__ = [
    "CABLE_STATUS_UPDATE_CONCURRENCY",
    "CSV_EXCLUDE_COLUMNS",
    "DETAIL_SHEET_NAME",
    "EXCEL_MIME_TYPE",
    "HOST_SUMMARY_COLUMNS",
    "HOST_SUMMARY_SHEET_NAME",
    "INCORRECT_CABLING_PREFIX",
    "LINK_DOWN_MSG",
    "LINK_UP_NO_NEIGHBOR_MSG",
    "MARKDOWN_EXCLUDE_COLUMNS",
    "UNEXPECTED_CONNECTION_MSG",
    "CableStatus",
    "CableValidationResultData",
    "CableValidationRow",
    "DecorateResultActivityInput",
    "DecorateResultActivityOutput",
    "DeviceArpTable",
    "DeviceMacTable",
    "DeviceNeighborData",
    "FormatDeviceValidationResultInput",
    "FormatResultsActivityInput",
    "InterfaceNeighborData",
    "InvalidCable",
    "NetworkDeviceData",
    "UpdateCableStatusesInput",
    "ValidateDeviceNeighborsInput",
    "ValidateDeviceNeighborsResult",
    "_escape_formula",
]
