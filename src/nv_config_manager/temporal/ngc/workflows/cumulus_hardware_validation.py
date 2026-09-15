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
"""Hardware Validation Workflow Definition."""

import ast
import asyncio
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, Field
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

from nv_config_manager.temporal.common.decorators.workflow import run_nv_config_manager_workflow
from nv_config_manager.temporal.common.mixins.metadata import WorkflowMetadataMixin
from nv_config_manager.temporal.common.mixins.stage import (
    StageInput,
    StageMixin,
    StageOutput,
    StateEnum,
    stage_executor,
)
from nv_config_manager.temporal.common.search_attributes import (
    SITE_SEARCH_ATTRIBUTE,
    upsert_missing_search_attributes,
)
from nv_config_manager.temporal.common.workflow_references import LocationReference

with workflow.unsafe.imports_passed_through():
    from nv_config_manager.temporal.common.mixins.archive import ArchiveMixin
    from nv_config_manager.temporal.common.mixins.device import DeviceMixin, NetworkDeviceData
    from nv_config_manager.temporal.ngc.activities.dcim import (
        GetNetworkDevicesInput,
        get_network_devices,
    )
    from nv_config_manager.temporal.ngc.activities.hardware_validation import (
        CreateConsolidatedExcelInput,
        HardwareValidationInput,
        HardwareValidationOutput,
        HardwareValidationResult,
        create_consolidated_excel_export,
        get_platform,
        get_platform_environment_fan,
        get_platform_environment_led,
        get_platform_environment_psu,
        get_platform_environment_voltage,
        get_platform_inventory,
    )

DEFAULT_ACTIVITY_RETRY_POLICY = RetryPolicy(maximum_attempts=5)
DEVICE_QUERY_START_TO_CLOSE_TIMEOUT = timedelta(seconds=30)
DEFAULT_HARDWARE_VALIDATION_STATUS = ["Active", "Provisioned"]


def format_filter_summary(
    site: str,
    roles: list[str],
    status: list[str],
    tenant: str | None,
    device_type_ids: list[str],
) -> str:
    """Return a concise hardware validation filter summary."""
    role_summary = roles if roles else "any"
    tenant_summary = tenant or "any"
    device_type_summary = device_type_ids if device_type_ids else "any"
    return (
        f"site={site}, roles={role_summary}, status={status}, "
        f"tenant={tenant_summary}, device_type_ids={device_type_summary}"
    )


def is_device_filter_error(error_message: str) -> bool:
    """Return whether an activity failure came from an invalid device filter."""
    return (
        "GraphQL error:" in error_message
        or "GraphQL errors:" in error_message
        or "Must apply at least one filter" in error_message
    )


def format_device_filter_error(error_message: str) -> str:
    """Format a GraphQL filter validation error for workflow display."""
    for prefix in ("GraphQL error:", "GraphQL errors:"):
        if error_message.startswith(prefix):
            error_message = error_message.removeprefix(prefix).strip()
            break
    return _format_error_value(_parse_error_literal(error_message))


def _parse_error_literal(value: str) -> Any:
    stripped = value.strip()
    if not stripped.startswith(("{", "[")):
        return stripped
    try:
        return ast.literal_eval(stripped)
    except (SyntaxError, ValueError):
        return stripped


def _format_error_value(value: Any) -> str:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            parsed = _parse_error_literal(stripped)
            if not isinstance(parsed, str) or parsed != stripped:
                return _format_error_value(parsed)
        return value
    if isinstance(value, list):
        return "; ".join(_format_error_value(item) for item in value)
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            formatted_item = _format_error_value(item)
            if key == "message":
                parts.append(formatted_item)
            else:
                parts.append(f"{key}: {formatted_item}")
        return "; ".join(parts)
    return str(value)


def analyze_error_results(
    devices_data_and_results: dict[str, dict[str, Any]], validation_type: str
) -> str:
    """
    Analyze hardware validation results for API errors and connectivity issues.

    Args:
        devices_data_and_results: Dictionary containing device data and command results
        validation_type: Type of validation (e.g., 'fan', 'led', 'psu', 'voltage', 'inventory')

    Returns:
        Markdown string with error results, or empty string if none found
    """
    not_found_devices = []
    connectivity_issues = []
    other_errors = []

    for device_id, data in devices_data_and_results.items():
        device_data = data["device_data"]
        error = data.get("error")
        error_type = data.get("error_type")

        if error:
            if isinstance(device_data, dict):
                device_name = device_data.get("name", device_id)
                rack_name = device_data.get("rack", "Unknown")
                rack_position = device_data.get("position", "Unknown")
            else:
                device_name = getattr(device_data, "name", device_id)
                rack_name = getattr(device_data, "rack", "Unknown") or "Unknown"
                rack_position = getattr(device_data, "position", "Unknown") or "Unknown"

            device_info = f"**{device_name}** (Rack: {rack_name}, Position: {rack_position})"

            if error_type == "not_found":
                not_found_devices.append(
                    f"  - {device_info}: {validation_type.title()} endpoint not available"
                )
            elif error_type == "connectivity":
                connectivity_issues.append(f"  - {device_info}: {error}")
            else:
                other_errors.append(f"  - {device_info}: {error}")

    error_output = ""

    if not_found_devices:
        error_output += f"\n\n⚠️ **Unsupported {validation_type.title()} Endpoints:**\n\n"
        error_output += "\n\n".join(not_found_devices)

    if connectivity_issues:
        if error_output:
            error_output += "\n\n"
        error_output += f"\n\n🔌 **Connectivity Issues ({validation_type.title()}):**\n\n"
        error_output += "\n\n".join(connectivity_issues)

    if other_errors:
        if error_output:
            error_output += "\n\n"
        error_output += f"\n\n❌ **Other Errors ({validation_type.title()}):**\n\n"
        error_output += "\n\n".join(other_errors)

    return error_output


def analyze_flagged_results(
    devices_data_and_results: dict[str, dict[str, Any]], validation_type: str
) -> str:
    """
    Analyze hardware validation results and generate markdown for flagged findings.

    Args:
        devices_data_and_results: Dictionary containing device data and command results
        validation_type: Type of validation (e.g., 'fan', 'led', 'psu', 'voltage', 'inventory')

    Returns:
        Markdown string with flagged results, or empty string if none found
    """
    device_sections = []

    for device_id, data in devices_data_and_results.items():
        if data.get("error"):
            continue

        device_data = data["device_data"]
        command_result = data.get("command_result", {})

        if isinstance(device_data, dict):
            device_name = device_data.get("name", device_id)
            rack_name = device_data.get("rack", "Unknown")
            rack_position = device_data.get("position", "Unknown")
        else:
            device_name = getattr(device_data, "name", device_id)
            rack_name = getattr(device_data, "rack", "Unknown") or "Unknown"
            rack_position = getattr(device_data, "position", "Unknown") or "Unknown"

        device_flags = []

        if isinstance(command_result, dict):
            for component_name, component_data in command_result.items():
                if isinstance(component_data, dict):
                    if validation_type == "led":
                        color = component_data.get("color", "").lower()
                        if color and color != "green":
                            device_flags.append(
                                f"  - **{component_name}**: LED is {color} (expected: green)"
                            )
                    else:
                        state = component_data.get("state", "").lower()
                        if state and state != "ok":
                            details = []
                            if "actual" in component_data:
                                details.append(f"actual: {component_data['actual']}")
                            if "min" in component_data:
                                details.append(f"min: {component_data['min']}")
                            if "max" in component_data:
                                details.append(f"max: {component_data['max']}")

                            detail_str = f" ({', '.join(details)})" if details else ""
                            device_flags.append(
                                f"  - **{component_name}**: state is {state} (expected: ok){detail_str}"
                            )

        if device_flags:
            device_section = f"**{device_name}** (Rack: {rack_name}, Position: {rack_position}):\n"
            device_section += "\n".join(device_flags)
            device_sections.append(device_section)

    if device_sections:
        header = f"\n\n⚠️ **Flagged {validation_type.title()} Results:**\n\n"
        return header + "\n\n\n".join(device_sections)

    return ""


class ValidateHardwareInput(BaseModel):
    """Validate Hardware Workflow Input."""

    site: LocationReference = Field(
        description="Site used to select network devices for validation."
    )
    roles: list[str] = Field(
        default=[], description="Device roles used to filter the selected network devices."
    )
    status: list[str] = Field(
        default=DEFAULT_HARDWARE_VALIDATION_STATUS,
        description="Device statuses used to filter the selected network devices.",
    )
    tenant: str | None = Field(
        default=None,
        description="Tenant used to filter the selected network devices.",
    )
    device_type_ids: list[str] = Field(
        default=[], description="Device type identifiers used to filter network devices."
    )
    raise_for_invalid: bool = Field(
        default=False, description="Whether invalid hardware should fail the workflow."
    )


@workflow.defn
class ValidateHardwareWorkflow(WorkflowMetadataMixin, StageMixin, DeviceMixin, ArchiveMixin):
    """Network hardware validation workflow for infrastructure health monitoring."""

    # Workflow metadata
    workflow_name = "Cumulus Hardware Validation"
    workflow_description = (
        "Validate hardware components (fans, PSUs, LEDs, voltage) across network devices"
    )
    workflow_input_class = ValidateHardwareInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/ngc/cumulus_hardware_validation"
    workflow_namespace = "ngc"
    workflow_mcp_enabled = True

    def __init__(self) -> None:
        """Initialize workflow."""
        StageMixin.__init__(self)
        self.define_stage(
            name="get_devices_to_validate",
            description="Query devices based on filtering criteria",
            requires_approval=False,
            depends_on=[],
        )
        self.define_stage(
            name="get_device_info",
            description="Get device information from the DCIM",
            requires_approval=False,
            depends_on=["get_devices_to_validate"],
        )
        self.define_stage(
            name="get_platform",
            description="Get platform information from device",
            requires_approval=False,
            depends_on=["get_device_info"],
        )
        self.define_stage(
            name="get_environment_fan",
            description="Get fan information from device",
            requires_approval=False,
            depends_on=["get_device_info"],
        )
        self.define_stage(
            name="get_environment_led",
            description="Get LED information from device",
            requires_approval=False,
            depends_on=["get_device_info"],
        )
        self.define_stage(
            name="get_environment_psu",
            description="Get PSU information from device",
            requires_approval=False,
            depends_on=["get_device_info"],
        )
        self.define_stage(
            name="get_environment_voltage",
            description="Get voltage information from device",
            requires_approval=False,
            depends_on=["get_device_info"],
        )
        self.define_stage(
            name="get_inventory",
            description="Get inventory information from device",
            requires_approval=False,
            depends_on=["get_device_info"],
        )
        self.define_stage(
            name="generate_consolidated_report",
            description="Generate consolidated Excel report with all validation data",
            requires_approval=False,
            depends_on=[
                "get_platform",
                "get_environment_fan",
                "get_environment_led",
                "get_environment_psu",
                "get_environment_voltage",
                "get_inventory",
            ],
        )

    class GetDevicesToValidateStageInput(StageInput):
        """Get Devices to Validate Stage Input."""

        site: str
        roles: list[str]
        status: list[str]
        tenant: str | None
        device_type_ids: list[str]

    class GetDevicesToValidateStageOutput(StageOutput):
        """Get Devices to Validate Stage Output."""

        devices: list[NetworkDeviceData]
        invalid_filter: bool = False

    class GetDeviceStageInput(StageInput):
        """Get Device Stage Input."""

        devices: list[NetworkDeviceData]

    class GetDeviceStageOutput(StageOutput):
        """Get Device Stage Output."""

        devices_data: dict[str, NetworkDeviceData]

    class GetPlatformInfoStageInput(StageInput):
        """Get Platform Info Stage Input."""

        devices_data: dict[str, NetworkDeviceData]

    class GetPlatformInfoStageOutput(StageOutput):
        """Get Platform Info Stage Output."""

        devices_info: dict[str, dict]
        devices_data_and_results: dict[str, dict[str, Any]]

    class GenerateConsolidatedReportStageInput(StageInput):
        """Generate Consolidated Report Stage Input."""

        platform_data: dict[str, dict[str, Any]]
        fan_data: dict[str, dict[str, Any]]
        led_data: dict[str, dict[str, Any]]
        psu_data: dict[str, dict[str, Any]]
        voltage_data: dict[str, dict[str, Any]]
        inventory_data: dict[str, dict[str, Any]]

    class GenerateConsolidatedReportStageOutput(StageOutput):
        """Generate Consolidated Report Stage Output."""

        excel_data: str
        total_row_count: int
        worksheet_counts: dict[str, int]

    @stage_executor("get_devices_to_validate")
    async def get_devices_to_validate(
        self, stage_input: GetDevicesToValidateStageInput
    ) -> GetDevicesToValidateStageOutput:
        """Query devices based on filtering criteria."""
        filter_summary = format_filter_summary(
            stage_input.site,
            stage_input.roles,
            stage_input.status,
            stage_input.tenant,
            stage_input.device_type_ids,
        )
        try:
            result = await workflow.execute_activity(
                get_network_devices,
                GetNetworkDevicesInput(
                    site=stage_input.site,
                    roles=stage_input.roles,
                    status=stage_input.status,
                    tenant=stage_input.tenant,
                    device_type_ids=stage_input.device_type_ids,
                    managed_only=True,
                ),
                start_to_close_timeout=DEVICE_QUERY_START_TO_CLOSE_TIMEOUT,
                retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            )
        except ActivityError as exc:
            reason = str(exc.cause) if exc.cause else str(exc)
            if not is_device_filter_error(reason):
                raise
            display = (
                f"Invalid hardware validation device filter ({filter_summary}): "
                f"{format_device_filter_error(reason)}"
            )
            return self.GetDevicesToValidateStageOutput(
                devices=[],
                invalid_filter=True,
                display=display,
            )

        # Filter devices to only include Cumulus Linux devices
        cumulus_devices = [
            device
            for device in result.devices
            if device.platform and "cumulus" in device.platform.lower()
        ]
        if not result.devices:
            display = f"No devices matched the specified filters ({filter_summary})."
        elif not cumulus_devices:
            display = (
                "No Cumulus Linux devices matched the specified filters "
                f"({filter_summary}). The DCIM returned {len(result.devices)} "
                "device(s), but hardware validation only runs against Cumulus Linux devices."
            )
        else:
            display = f"Found {len(cumulus_devices)} Cumulus Linux devices."

        return self.GetDevicesToValidateStageOutput(
            devices=cumulus_devices,
            display=display,
        )

    @stage_executor("get_device_info")
    async def get_device_info(self, stage_input: GetDeviceStageInput) -> GetDeviceStageOutput:
        """Get device data from the DCIM."""
        devices_data = {}
        device_names = []

        for device in stage_input.devices:
            devices_data[device.id] = device
            device_names.append(device.name)

        return self.GetDeviceStageOutput(
            devices_data=devices_data,
            display=f"Retrieved device info for {len(devices_data)} devices: {', '.join(device_names)}",
        )

    @stage_executor("get_platform")
    async def get_platform_stage(
        self, stage_input: GetPlatformInfoStageInput
    ) -> GetPlatformInfoStageOutput:
        """Get platform information from devices."""

        device_tasks = [
            workflow.execute_activity(
                get_platform,
                HardwareValidationInput(device_data=device_data),
                start_to_close_timeout=DEVICE_QUERY_START_TO_CLOSE_TIMEOUT,
                retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            )
            for device_id, device_data in stage_input.devices_data.items()
        ]

        results = await asyncio.gather(*device_tasks, return_exceptions=True)

        devices_info = {}
        devices_data_and_results = {}
        for (device_id, device_data), result in zip(
            stage_input.devices_data.items(), results, strict=False
        ):
            if isinstance(result, Exception):
                # Activity failed after retries
                devices_data_and_results[device_id] = {
                    "device_data": device_data,
                    "command_result": {},
                    "error": str(result),
                    "error_type": "activity_failure",
                }
            elif isinstance(result, HardwareValidationOutput):
                # Activity succeeded
                devices_info[device_id] = result.info
                devices_data_and_results[device_id] = {
                    "device_data": device_data,
                    "command_result": result.info,
                    "error": None,
                    "error_type": None,
                }

        successful_count = sum(1 for d in devices_data_and_results.values() if not d.get("error"))
        total_count = len(stage_input.devices_data)

        error_results = analyze_error_results(devices_data_and_results, "platform")

        display = f"**Platform validation completed** - {successful_count}/{total_count} devices validated successfully"
        display += error_results

        return self.GetPlatformInfoStageOutput(
            devices_info=devices_info,
            devices_data_and_results=devices_data_and_results,
            display=display,
        )

    @stage_executor("get_environment_fan")
    async def get_environment_fan_stage(
        self, stage_input: GetPlatformInfoStageInput
    ) -> GetPlatformInfoStageOutput:
        """Get fan information from devices."""

        device_tasks = [
            workflow.execute_activity(
                get_platform_environment_fan,
                HardwareValidationInput(device_data=device_data),
                start_to_close_timeout=DEVICE_QUERY_START_TO_CLOSE_TIMEOUT,
                retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            )
            for device_id, device_data in stage_input.devices_data.items()
        ]

        results = await asyncio.gather(*device_tasks, return_exceptions=True)

        devices_info = {}
        devices_data_and_results = {}
        for (device_id, device_data), result in zip(
            stage_input.devices_data.items(), results, strict=False
        ):
            if isinstance(result, Exception):
                # Activity failed after retries
                devices_data_and_results[device_id] = {
                    "device_data": device_data,
                    "command_result": {},
                    "error": str(result),
                    "error_type": "activity_failure",
                }
            elif isinstance(result, HardwareValidationOutput):
                # Activity succeeded
                devices_info[device_id] = result.info
                devices_data_and_results[device_id] = {
                    "device_data": device_data,
                    "command_result": result.info,
                    "error": None,
                    "error_type": None,
                }

        successful_devices = [d for d in devices_data_and_results.values() if not d.get("error")]
        total_entries = sum(
            len(d["command_result"])
            for d in successful_devices
            if isinstance(d["command_result"], dict)
        )
        successful_count = len(successful_devices)
        total_count = len(stage_input.devices_data)

        error_results = analyze_error_results(devices_data_and_results, "fan")
        flagged_results = analyze_flagged_results(devices_data_and_results, "fan")

        display = f"**Fan validation completed** - {successful_count}/{total_count} devices validated, {total_entries} entries"
        display += error_results

        if successful_count > 0:
            if flagged_results:
                display += flagged_results
            else:
                display += "\n\n **No flagged results** - All fan checks passed"

        return self.GetPlatformInfoStageOutput(
            devices_info=devices_info,
            devices_data_and_results=devices_data_and_results,
            display=display,
        )

    @stage_executor("get_environment_led")
    async def get_environment_led_stage(
        self, stage_input: GetPlatformInfoStageInput
    ) -> GetPlatformInfoStageOutput:
        """Get LED information from devices."""

        device_tasks = [
            workflow.execute_activity(
                get_platform_environment_led,
                HardwareValidationInput(device_data=device_data),
                start_to_close_timeout=DEVICE_QUERY_START_TO_CLOSE_TIMEOUT,
                retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            )
            for device_id, device_data in stage_input.devices_data.items()
        ]

        results = await asyncio.gather(*device_tasks, return_exceptions=True)

        devices_info = {}
        devices_data_and_results = {}
        for (device_id, device_data), result in zip(
            stage_input.devices_data.items(), results, strict=False
        ):
            if isinstance(result, Exception):
                # Activity failed after retries
                devices_data_and_results[device_id] = {
                    "device_data": device_data,
                    "command_result": {},
                    "error": str(result),
                    "error_type": "activity_failure",
                }
            elif isinstance(result, HardwareValidationOutput):
                # Activity succeeded
                devices_info[device_id] = result.info
                devices_data_and_results[device_id] = {
                    "device_data": device_data,
                    "command_result": result.info,
                    "error": None,
                    "error_type": None,
                }

        successful_devices = [d for d in devices_data_and_results.values() if not d.get("error")]
        total_entries = sum(
            len(d["command_result"])
            for d in successful_devices
            if isinstance(d["command_result"], dict)
        )
        successful_count = len(successful_devices)
        total_count = len(stage_input.devices_data)

        error_results = analyze_error_results(devices_data_and_results, "led")

        flagged_results = analyze_flagged_results(devices_data_and_results, "led")

        display = f"**LED validation completed** - {successful_count}/{total_count} devices validated, {total_entries} entries"
        display += error_results

        if successful_count > 0:
            if flagged_results:
                display += flagged_results
            else:
                display += "\n\n **No flagged results** - All LED checks passed"

        return self.GetPlatformInfoStageOutput(
            devices_info=devices_info,
            devices_data_and_results=devices_data_and_results,
            display=display,
        )

    @stage_executor("get_environment_psu")
    async def get_environment_psu_stage(
        self, stage_input: GetPlatformInfoStageInput
    ) -> GetPlatformInfoStageOutput:
        """Get PSU information from devices."""

        device_tasks = [
            workflow.execute_activity(
                get_platform_environment_psu,
                HardwareValidationInput(device_data=device_data),
                start_to_close_timeout=DEVICE_QUERY_START_TO_CLOSE_TIMEOUT,
                retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            )
            for device_id, device_data in stage_input.devices_data.items()
        ]

        results = await asyncio.gather(*device_tasks)

        devices_info = {}
        devices_data_and_results = {}
        for (device_id, device_data), result in zip(
            stage_input.devices_data.items(), results, strict=False
        ):
            if isinstance(result, Exception):
                # Activity failed after retries
                devices_data_and_results[device_id] = {
                    "device_data": device_data,
                    "command_result": {},
                    "error": str(result),
                    "error_type": "activity_failure",
                }
            elif isinstance(result, HardwareValidationOutput):
                # Activity succeeded
                devices_info[device_id] = result.info
                devices_data_and_results[device_id] = {
                    "device_data": device_data,
                    "command_result": result.info,
                    "error": None,
                    "error_type": None,
                }

        successful_devices = [d for d in devices_data_and_results.values() if not d.get("error")]
        total_entries = sum(
            len(d["command_result"])
            for d in successful_devices
            if isinstance(d["command_result"], dict)
        )
        successful_count = len(successful_devices)
        total_count = len(stage_input.devices_data)

        error_results = analyze_error_results(devices_data_and_results, "psu")

        flagged_results = analyze_flagged_results(devices_data_and_results, "psu")

        display = f"**PSU validation completed** - {successful_count}/{total_count} devices validated, {total_entries} entries"
        display += error_results

        if successful_count > 0:
            if flagged_results:
                display += flagged_results
            else:
                display += "\n\n **No flagged results** - All PSU checks passed"

        return self.GetPlatformInfoStageOutput(
            devices_info=devices_info,
            devices_data_and_results=devices_data_and_results,
            display=display,
        )

    @stage_executor("get_environment_voltage")
    async def get_environment_voltage_stage(
        self, stage_input: GetPlatformInfoStageInput
    ) -> GetPlatformInfoStageOutput:
        """Get voltage information from devices."""

        device_tasks = [
            workflow.execute_activity(
                get_platform_environment_voltage,
                HardwareValidationInput(device_data=device_data),
                start_to_close_timeout=DEVICE_QUERY_START_TO_CLOSE_TIMEOUT,
                retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            )
            for device_id, device_data in stage_input.devices_data.items()
        ]

        results = await asyncio.gather(*device_tasks, return_exceptions=True)

        devices_info = {}
        devices_data_and_results = {}
        for (device_id, device_data), result in zip(
            stage_input.devices_data.items(), results, strict=False
        ):
            if isinstance(result, Exception):
                # Activity failed after retries
                devices_data_and_results[device_id] = {
                    "device_data": device_data,
                    "command_result": {},
                    "error": str(result),
                    "error_type": "activity_failure",
                }
            elif isinstance(result, HardwareValidationOutput):
                # Activity succeeded
                devices_info[device_id] = result.info
                devices_data_and_results[device_id] = {
                    "device_data": device_data,
                    "command_result": result.info,
                    "error": None,
                    "error_type": None,
                }

        successful_devices = [d for d in devices_data_and_results.values() if not d.get("error")]
        total_entries = sum(
            len(d["command_result"])
            for d in successful_devices
            if isinstance(d["command_result"], dict)
        )
        successful_count = len(successful_devices)
        total_count = len(stage_input.devices_data)

        error_results = analyze_error_results(devices_data_and_results, "voltage")

        flagged_results = analyze_flagged_results(devices_data_and_results, "voltage")

        display = f"**Voltage validation completed** - {successful_count}/{total_count} devices validated, {total_entries} entries"
        display += error_results

        if successful_count > 0:
            if flagged_results:
                display += flagged_results
            else:
                display += "\n\n **No flagged results** - All voltage checks passed"

        return self.GetPlatformInfoStageOutput(
            devices_info=devices_info,
            devices_data_and_results=devices_data_and_results,
            display=display,
        )

    @stage_executor("get_inventory")
    async def get_inventory_stage(
        self, stage_input: GetPlatformInfoStageInput
    ) -> GetPlatformInfoStageOutput:
        """Get inventory information from devices."""

        device_tasks = [
            workflow.execute_activity(
                get_platform_inventory,
                HardwareValidationInput(device_data=device_data),
                start_to_close_timeout=DEVICE_QUERY_START_TO_CLOSE_TIMEOUT,
                retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            )
            for device_id, device_data in stage_input.devices_data.items()
        ]

        results = await asyncio.gather(*device_tasks)

        devices_info = {}
        devices_data_and_results = {}
        for (device_id, device_data), result in zip(
            stage_input.devices_data.items(), results, strict=False
        ):
            if isinstance(result, Exception):
                # Activity failed after retries
                devices_data_and_results[device_id] = {
                    "device_data": device_data,
                    "command_result": {},
                    "error": str(result),
                    "error_type": "activity_failure",
                }
            elif isinstance(result, HardwareValidationOutput):
                # Activity succeeded
                devices_info[device_id] = result.info
                devices_data_and_results[device_id] = {
                    "device_data": device_data,
                    "command_result": result.info,
                    "error": None,
                    "error_type": None,
                }

        successful_devices = [d for d in devices_data_and_results.values() if not d.get("error")]
        total_entries = sum(
            len(d["command_result"])
            for d in successful_devices
            if isinstance(d["command_result"], dict)
        )
        successful_count = len(successful_devices)
        total_count = len(stage_input.devices_data)

        error_results = analyze_error_results(devices_data_and_results, "inventory")

        flagged_results = analyze_flagged_results(devices_data_and_results, "inventory")

        display = f"**Inventory validation completed** - {successful_count}/{total_count} devices validated, {total_entries} entries"
        display += error_results

        if successful_count > 0:
            if flagged_results:
                display += flagged_results
            else:
                display += "\n\n **No flagged results** - All inventory checks passed"

        return self.GetPlatformInfoStageOutput(
            devices_info=devices_info,
            devices_data_and_results=devices_data_and_results,
            display=display,
        )

    @stage_executor("generate_consolidated_report")
    async def generate_consolidated_report_stage(
        self, stage_input: GenerateConsolidatedReportStageInput
    ) -> GenerateConsolidatedReportStageOutput:
        """Generate consolidated Excel report with all validation data."""

        stage_data = {
            "platform": stage_input.platform_data,
            "fan": stage_input.fan_data,
            "led": stage_input.led_data,
            "psu": stage_input.psu_data,
            "voltage": stage_input.voltage_data,
            "inventory": stage_input.inventory_data,
        }

        excel_result = await workflow.execute_activity(
            create_consolidated_excel_export,
            CreateConsolidatedExcelInput(stage_data=stage_data),
            start_to_close_timeout=DEVICE_QUERY_START_TO_CLOSE_TIMEOUT,
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        worksheet_summary = ", ".join(
            [f"{name}: {count} rows" for name, count in excel_result.worksheet_counts.items()]
        )

        display = (
            f"**Consolidated report generated** - {excel_result.total_row_count} total entries\n\n"
        )
        display += f"**Worksheets:** {worksheet_summary}\n\n"

        display += "📊 **Excel Features:**\n"
        display += (
            "- **AutoFilters**: All columns have filters enabled for easy sorting and filtering\n"
        )
        display += "- **Manual Filtering**: Use column filters to focus on specific data or identify hardware issues\n"
        display += "- **Quick Problem Detection**: Filter by status/color columns to easily identify hardware issues for vendor reporting\n\n"

        display += f"[Download Excel Summary](data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,{excel_result.excel_data})"

        return self.GenerateConsolidatedReportStageOutput(
            excel_data=excel_result.excel_data,
            total_row_count=excel_result.total_row_count,
            worksheet_counts=excel_result.worksheet_counts,
            display=display,
        )

    @run_nv_config_manager_workflow
    async def run(  # type: ignore[override, ty:invalid-method-override]
        self, workflow_input: ValidateHardwareInput
    ) -> HardwareValidationResult:
        """Execute hardware validation workflow."""
        self.set_input(workflow_input)
        upsert_missing_search_attributes({SITE_SEARCH_ATTRIBUTE: [workflow_input.site]})

        devices_to_validate_output = await self.get_devices_to_validate(
            self.GetDevicesToValidateStageInput(
                site=workflow_input.site,
                roles=workflow_input.roles,
                status=workflow_input.status,
                tenant=workflow_input.tenant,
                device_type_ids=workflow_input.device_type_ids,
            )
        )

        if not devices_to_validate_output.devices:
            for stage_name in [
                "get_device_info",
                "get_platform",
                "get_environment_fan",
                "get_environment_led",
                "get_environment_psu",
                "get_environment_voltage",
                "get_inventory",
                "generate_consolidated_report",
            ]:
                self.set_stage_state(stage_name, StateEnum.UNREACHABLE)

            await self.archive_results()
            return HardwareValidationResult(
                success=False,
                devices_validated=0,
                total_entries=0,
                message=devices_to_validate_output.display,
            )

        device_output = await self.get_device_info(
            self.GetDeviceStageInput(devices=devices_to_validate_output.devices)
        )

        stage_input = self.GetPlatformInfoStageInput(devices_data=device_output.devices_data)

        platform_task = asyncio.create_task(self.get_platform_stage(stage_input))
        fan_task = asyncio.create_task(self.get_environment_fan_stage(stage_input))
        led_task = asyncio.create_task(self.get_environment_led_stage(stage_input))
        psu_task = asyncio.create_task(self.get_environment_psu_stage(stage_input))
        voltage_task = asyncio.create_task(self.get_environment_voltage_stage(stage_input))
        inventory_task = asyncio.create_task(self.get_inventory_stage(stage_input))

        platform_output = await platform_task
        fan_output = await fan_task
        led_output = await led_task
        psu_output = await psu_task
        voltage_output = await voltage_task
        inventory_output = await inventory_task

        consolidated_report_output = await self.generate_consolidated_report_stage(
            self.GenerateConsolidatedReportStageInput(
                platform_data=platform_output.devices_data_and_results,
                fan_data=fan_output.devices_data_and_results,
                led_data=led_output.devices_data_and_results,
                psu_data=psu_output.devices_data_and_results,
                voltage_data=voltage_output.devices_data_and_results,
                inventory_data=inventory_output.devices_data_and_results,
            )
        )

        workflow.logger.info(
            f"Consolidated report generated with {consolidated_report_output.total_row_count} total entries"
        )

        devices_validated = len(device_output.devices_data)
        total_entries = consolidated_report_output.total_row_count
        success = True

        if devices_validated == 0:
            message = "Hardware validation completed - no devices matched the filter."
        else:
            message = f"Hardware validation completed for {devices_validated} devices."

        validation_results = HardwareValidationResult(
            success=success,
            devices_validated=devices_validated,
            total_entries=total_entries,
            message=message,
        )

        await self.archive_results()
        return validation_results
