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
# pylint: disable=too-many-locals.too-many-nested-blocks,too-many-branches
"""Provider-neutral cable-validation activities and public exports."""

import asyncio
import copy
import logging

from nv_config_manager_dcim import CableStatusUpdate, DCIMCableStatusClient
from nv_config_manager_logging import LogCategory, get_logger
from py_markdown_table.markdown_table import markdown_table
from temporalio import activity

from nv_config_manager_workflows.activities.cable_validation import helpers as _cable_helpers
from nv_config_manager_workflows.activities.cable_validation.helpers import (
    is_mac_address,
)
from nv_config_manager_workflows.activities.cable_validation.models import (
    CABLE_STATUS_UPDATE_CONCURRENCY,
    CSV_EXCLUDE_COLUMNS,
    MARKDOWN_EXCLUDE_COLUMNS,
    CableStatus,
    DecorateResultActivityInput,
    DecorateResultActivityOutput,
    FormatDeviceValidationResultInput,
    FormatResultsActivityInput,
    UpdateCableStatusesInput,
    ValidateDeviceNeighborsInput,
    ValidateDeviceNeighborsResult,
)
from nv_config_manager_workflows.runtime import get_dcim_client

logger = get_logger(__name__, category=LogCategory.TEMPORAL_ACTIVITY)
logger.setLevel(logging.INFO)


@activity.defn
async def validate_device_neighbors(
    activity_input: ValidateDeviceNeighborsInput,
) -> ValidateDeviceNeighborsResult:
    """
    Validate device neighbors.

    MAC and LLDP will be checked, if either is correct, the link is considered valid.

    This function catches actual LLDP neighbors that are not modeled in the DCIM, but it
    does not validate MAC addresses that are on the device but absent from the DCIM.

    """
    intended = activity_input.intended
    actual = activity_input.actual
    device = activity_input.device
    mac_table = activity_input.mac_table
    arp_table = activity_input.arp_table
    result = ValidateDeviceNeighborsResult()

    for intended_interface, intended_neighbor in intended.neighbors.items():
        invalid_cable, cable_status = _cable_helpers._process_intended_interface(
            intended_interface,
            intended_neighbor,
            intended,
            actual,
            device,
            mac_table,
            arp_table,
            activity_input.ignore_no_neighbor,
        )
        if invalid_cable:
            result.interfaces[intended_interface] = invalid_cable
        if cable_status:
            result.cable_statuses[intended_interface] = cable_status

    # Find unexpected neighbors (in actual but not in intended)
    unexpected = _cable_helpers._find_unexpected_neighbors(intended, actual)
    result.interfaces.update(unexpected)

    return result


@activity.defn
async def update_cable_statuses(activity_input: UpdateCableStatusesInput) -> None:
    """Persist cable statuses when the selected provider exposes that capability."""
    client = get_dcim_client()
    if not isinstance(client, DCIMCableStatusClient):
        await client.close()
        logger.info("Selected DCIM provider does not support cable status updates")
        return

    semaphore = asyncio.Semaphore(CABLE_STATUS_UPDATE_CONCURRENCY)

    async def update_one(interface_name: str, status: CableStatus) -> None:
        async with semaphore:
            await client.update_cable_status(
                CableStatusUpdate(
                    device_id=activity_input.device_id,
                    interface_name=interface_name,
                    status=status,
                    workflow_id=activity_input.workflow_id,
                )
            )

    async with client:
        results = await asyncio.gather(
            *(
                update_one(interface_name, status)
                for interface_name, status in activity_input.cable_statuses.items()
            ),
            return_exceptions=True,
        )

    for result in results:
        if isinstance(result, asyncio.CancelledError):
            raise result
        if isinstance(result, Exception):
            raise result


@activity.defn
async def decorate_result(
    activity_input: DecorateResultActivityInput,
) -> DecorateResultActivityOutput:
    """Decorate result activity."""
    activity_result = copy.deepcopy(activity_input.devices)
    mac_to_host: dict[str, tuple[str, str | None]] = {}
    for device_result in activity_result.values():
        for interface_result in device_result.interfaces.values():
            macs = interface_result.actual.macs if interface_result.actual else []
            for mac in macs:
                if mac not in mac_to_host:
                    mac_to_host[mac] = (mac, None)
            if (
                interface_result.actual
                and interface_result.actual.name
                and is_mac_address(interface_result.actual.name)
                and interface_result.actual.name not in mac_to_host
            ):
                mac_to_host[interface_result.actual.name] = (
                    interface_result.actual.name,
                    None,
                )

    if not mac_to_host:
        return DecorateResultActivityOutput(devices=activity_result)

    client = get_dcim_client()
    async with client:
        interfaces = await client.get_interface_hosts_by_mac(list(mac_to_host.keys()))

    for interface in interfaces:
        if interface.mac_address:
            mac_to_host[interface.mac_address] = (interface.name, interface.host)

    for device_result in activity_result.values():
        for interface_result in device_result.interfaces.values():
            if interface_result.actual:
                for mac in interface_result.actual.macs:
                    if mac in mac_to_host:
                        if not interface_result.actual.name:
                            interface_result.actual.name = mac_to_host[mac][0]
                        if not interface_result.actual.device_name:
                            interface_result.actual.device_name = mac_to_host[mac][1]
                        # Assume one connected host match per interface
                        break
            if (
                interface_result.actual
                and interface_result.actual.name
                and is_mac_address(interface_result.actual.name)
                and interface_result.actual.name in mac_to_host
            ):
                interface_result.actual.device_name = mac_to_host[interface_result.actual.name][1]
                interface_result.actual.name = mac_to_host[interface_result.actual.name][0]

    return DecorateResultActivityOutput(devices=activity_result)


@activity.defn
def format_results(activity_input: FormatResultsActivityInput) -> str:
    """Format Cable Validation Results in markdown."""
    results = []
    dedup: set[tuple[str, str]] = set()
    for _, device_results in activity_input.devices.items():
        if device_results.interfaces:
            device_rows = _cable_helpers._format_device_result_row(
                device_results.device,
                device_results.interfaces,
                dedup=dedup,
                ignore_no_neighbor=activity_input.ignore_no_neighbor,
            )
            results.extend(device_rows)

    markdown = ""
    if activity_input.failed_devices:
        failures = []
        markdown += "### Failed Devices\n"
        markdown += "Address the listed issues and re-run the workflow for complete results.\n\n"
        for device_name, error in activity_input.failed_devices.items():
            failures.append(
                {
                    "Failed Device": device_name,
                    "Reason": error,
                }
            )
        markdown += (
            markdown_table(failures).set_params(quote=False, row_sep="markdown").get_markdown()
        )
        markdown += "\n\n"

    markdown += _cable_helpers._format_results_markdown(
        results,
        csv_exclude_columns=CSV_EXCLUDE_COLUMNS,
        markdown_exclude_columns=MARKDOWN_EXCLUDE_COLUMNS,
        export="xlsx",
        summary_devices=activity_input.devices,
    )
    return markdown


@activity.defn
def format_device_validation_result(
    activity_input: FormatDeviceValidationResultInput,
) -> str:
    """Format a single device's cable validation results in markdown."""
    results = _cable_helpers._format_device_result_row(
        activity_input.device,
        activity_input.validation_result.interfaces,
        ignore_no_neighbor=activity_input.ignore_no_neighbor,
    )

    return _cable_helpers._format_results_markdown(
        results,
        csv_exclude_columns=CSV_EXCLUDE_COLUMNS,
        markdown_exclude_columns=MARKDOWN_EXCLUDE_COLUMNS,
        empty_message="All cable connections are valid.",
        max_display_results=1000,
    )
