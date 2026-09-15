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
"""NVLinkSwitch Firmware Upgrade Workflow Definition."""

from datetime import timedelta

from pydantic import BaseModel, Field
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

from nv_config_manager.temporal.common.decorators.workflow import run_nv_config_manager_workflow
from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData, Platform
from nv_config_manager.temporal.common.mixins.metadata import WorkflowMetadataMixin
from nv_config_manager.temporal.common.mixins.stage import (
    StageInput,
    StageMixin,
    StageOutput,
    StateEnum,
    stage_executor,
)
from nv_config_manager.temporal.common.workflow_references import DeviceReference

with workflow.unsafe.imports_passed_through():
    from nv_config_manager.temporal.common.mixins.archive import ArchiveMixin
    from nv_config_manager.temporal.common.mixins.device import DeviceMixin
    from nv_config_manager.temporal.ngc.activities.dcim import (
        CheckRecordedConfigDriftInput,
        GetNetworkDeviceInput,
        check_recorded_config_drift,
        get_network_device,
    )
    from nv_config_manager.temporal.ngc.activities.nvlinkswitch_firmware import (
        CompareRunningDesiredInput,
        GetRunningFirmwareInput,
        # ValidateTargetFilesInput,
        RebootDeviceInput,
        UpdateDeviceContextInput,
        ValidateRenderTargetsInput,
        compare_running_desired,
        get_running_firmware,
        # validate_target_files,
        reboot_device,
        update_device_context,
        validate_render_targets,
    )
    from nv_config_manager.temporal.ngc.activities.os import (
        ExecuteZTPInput,
        GetCurrentOSInput,
        PollZTPStatusInput,
        WaitRebootInput,
        execute_ztp,
        get_current_os,
        poll_ztp_status,
        wait_reboot,
    )
    from nv_config_manager.temporal.ngc.workflows.backup import (
        BackupInput,
        BackupWorkflow,
        TriggerEnum,
    )

DEFAULT_ACTIVITY_RETRY_POLICY = RetryPolicy(
    maximum_attempts=3,
    non_retryable_error_types=["NVLinkSwitchFirmwareUpgradeException"],
)

SUPPORTED_PLATFORMS = [Platform.NV_OS]


def _format_firmware_dict(firmware_dict: dict[str, str], title: str = "Firmware") -> str:
    """Format firmware dictionary as readable markdown."""
    if not firmware_dict:
        return f"**{title}:** None"

    lines = [f"**{title}:**"]
    for component, version in sorted(firmware_dict.items()):
        lines.append(f"  - **{component.upper()}:** `{version}`")
    return "\n".join(lines)


def _format_firmware_differences(differences: dict[str, dict[str, str]]) -> str:
    """Format firmware differences as readable markdown."""
    if not differences:
        return "**No differences found**"

    lines = ["**Firmware Changes Required:**"]
    for component, diff in sorted(differences.items()):
        actual = diff.get("actual", "unknown")
        expected = diff.get("expected", "unknown")
        lines.append(f"  - **{component.upper()}:** `{actual}` → `{expected}`")
    return "\n".join(lines)


class NVLinkSwitchFirmwareUpgradeInput(BaseModel):
    """NVLinkSwitch Firmware Upgrade Workflow Input Definition."""

    device_id: DeviceReference = Field(description="Identifier of the NVLink switch to upgrade.")
    bundle_version: str = Field(description="Target NVLink firmware bundle version.")


@workflow.defn
class NVLinkSwitchFirmwareUpgradeWorkflow(
    WorkflowMetadataMixin, StageMixin, DeviceMixin, ArchiveMixin
):
    """NVLink switch firmware upgrade workflow for GPU interconnect infrastructure."""

    # Workflow metadata
    workflow_name = "NVLink Switch Firmware Upgrade"
    workflow_description = "Upgrade firmware on NVLink switches with validation"
    workflow_input_class = NVLinkSwitchFirmwareUpgradeInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/ngc/nvlinkswitch_firmware_upgrade"
    workflow_namespace = "ngc"

    def __init__(self) -> None:
        """Initialize workflow."""
        StageMixin.__init__(self)
        self.define_stage(
            name="get_current_state",
            description="Get current OS and firmware versions from device.",
            requires_approval=False,
            depends_on=[],
        )

        self.define_stage(
            name="compare_versions",
            description="Compare running vs desired firmware versions.",
            requires_approval=False,
            depends_on=["get_current_state"],
        )

        self.define_stage(
            name="perform_backup",
            description="Run the backup workflow for the device.",
            requires_approval=False,
            depends_on=["compare_versions"],
        )

        self.define_stage(
            name="update_context_and_validate",
            description="Update device context and validate render/files.",
            requires_approval=False,
            depends_on=["perform_backup"],
        )

        self.define_stage(
            name="execute_firmware_upgrade",
            description="Execute factory reset and wait for ZTP completion.",
            requires_approval=False,
            depends_on=["update_context_and_validate"],
        )

        self.define_stage(
            name="validate_firmware_upgrade",
            description="Check final firmware versions and reboot if needed.",
            requires_approval=False,
            depends_on=["execute_firmware_upgrade"],
        )

    class GetCurrentStateStageInput(StageInput):
        """Get Current State Stage Input."""

        device_id: str

    class GetCurrentStateStageOutput(StageOutput):
        """Get Current State Stage Output."""

        device_data: NetworkDeviceData
        supported: bool
        running_os: str
        running_firmware: dict[str, str]

    @stage_executor("get_current_state")
    async def get_current_state(
        self, stage_input: GetCurrentStateStageInput
    ) -> GetCurrentStateStageOutput:
        """Get current OS and firmware versions from device."""
        result = await workflow.execute_activity(
            get_network_device,
            GetNetworkDeviceInput(device_id=stage_input.device_id),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        # Add device search attributes the first time we pull them from the DCIM
        DeviceMixin.attach_device_search_attributes(result.device)

        device_data = result.device
        if device_data.platform not in SUPPORTED_PLATFORMS:
            return NVLinkSwitchFirmwareUpgradeWorkflow.GetCurrentStateStageOutput(
                device_data=device_data,
                supported=False,
                running_os="",
                running_firmware={},
                display="NVLinkSwitch firmware upgrade is not supported for this platform.",
            )

        # Get current OS version
        os_result = await workflow.execute_activity(
            get_current_os,
            GetCurrentOSInput(device_data=device_data),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        # Get running firmware versions
        firmware_result = await workflow.execute_activity(
            get_running_firmware,
            GetRunningFirmwareInput(device_data=device_data),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        display = (
            f"## Current Device State\n\n"
            f"**Running OS:** `{os_result.running_os}`\n\n"
            f"{_format_firmware_dict(firmware_result.running_firmware, 'Running Firmware')}"
        )

        return NVLinkSwitchFirmwareUpgradeWorkflow.GetCurrentStateStageOutput(
            device_data=device_data,
            supported=True,
            running_os=os_result.running_os,
            running_firmware=firmware_result.running_firmware,
            display=display,
        )

    class CompareVersionsStageInput(StageInput):
        """Compare Versions Stage Input."""

        device_data: NetworkDeviceData
        running_os: str
        running_firmware: dict[str, str]
        bundle_version: str

    class CompareVersionsStageOutput(StageOutput):
        """Compare Versions Stage Output."""

        upgrade_needed: bool
        desired_os: str
        desired_firmware: dict[str, str]
        differences: dict[str, dict[str, str]]

    @stage_executor("compare_versions")
    async def compare_versions(
        self, stage_input: CompareVersionsStageInput
    ) -> CompareVersionsStageOutput:
        """Compare running vs desired firmware versions."""
        comparison_result = await workflow.execute_activity(
            compare_running_desired,
            CompareRunningDesiredInput(
                device_data=stage_input.device_data,
                running_os=stage_input.running_os,
                running_firmware=stage_input.running_firmware,
                bundle_version=stage_input.bundle_version,
            ),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        if not comparison_result.upgrade_needed:
            display = (
                f"## ✅ No Upgrade Required\n\n"
                f"All firmware components match the desired state.\n\n"
                f"**Current OS:** `{stage_input.running_os}`\n\n"
                f"{_format_firmware_dict(stage_input.running_firmware, 'Current Firmware')}"
            )
        else:
            os_change = ""
            if stage_input.running_os != comparison_result.desired_os:
                os_change = f"**OS Upgrade:** `{stage_input.running_os}` → `{comparison_result.desired_os}`\n\n"

            display = (
                f"## 🔄 Firmware Upgrade Required\n\n"
                f"{os_change}"
                f"{_format_firmware_differences(comparison_result.differences)}"
            )

        return NVLinkSwitchFirmwareUpgradeWorkflow.CompareVersionsStageOutput(
            upgrade_needed=comparison_result.upgrade_needed,
            desired_os=comparison_result.desired_os,
            desired_firmware=comparison_result.desired_firmware,
            differences=comparison_result.differences,
            display=display,
        )

    class BackupStageInput(StageInput):
        """Backup Stage Input."""

        device_id: str

    class BackupStageOutput(StageOutput):
        """Backup Stage Output."""

        config_drift: bool

    @stage_executor("perform_backup")
    async def perform_backup(self, stage_input: BackupStageInput) -> BackupStageOutput:
        """Perform a configuration backup."""
        backup_input = BackupInput(
            device_id=stage_input.device_id,
            trigger=TriggerEnum.WORKFLOW,
            user="nv-config-manager-temporal",
            user_domain=None,
            workflow_id=workflow.info().workflow_id,
            intended_config_commit_id="",
        )

        backup_handle = await workflow.start_child_workflow(
            BackupWorkflow.run, backup_input, run_timeout=timedelta(minutes=10)
        )
        self.append_child_workflow("perform_backup", backup_handle.id)
        await workflow.wait_condition(backup_handle.done)

        # Check if the backup workflow reported config drift to the DCIM
        config_drift = await workflow.execute_activity(
            check_recorded_config_drift,
            CheckRecordedConfigDriftInput(device_id=stage_input.device_id),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        if config_drift:
            display = (
                f"## ⚠️ Configuration Drift Detected\n\n"
                f"**Backup completed** via workflow `{backup_handle.id}`\n\n"
                f"**⚠️ WORKFLOW HALTED:** Configuration drift was detected during backup. "
                f"The device configuration has changed since the last known state. "
                f"Please resolve configuration drift before proceeding with firmware upgrade."
            )
        else:
            display = (
                f"## ✅ Configuration Backup Complete\n\n"
                f"**Backup workflow:** `{backup_handle.id}`\n\n"
                f"Device configuration has been successfully backed up with no drift detected. "
                f"Ready to proceed with firmware upgrade."
            )
        return NVLinkSwitchFirmwareUpgradeWorkflow.BackupStageOutput(
            display=display,
            config_drift=config_drift,
        )

    class UpdateContextAndValidateStageInput(StageInput):
        """Update Context and Validate Stage Input."""

        device_data: NetworkDeviceData
        bundle_version: str
        desired_firmware: dict[str, str]

    class UpdateContextAndValidateStageOutput(StageOutput):
        """Update Context and Validate Stage Output."""

    @stage_executor("update_context_and_validate")
    async def update_context_and_validate(
        self, stage_input: UpdateContextAndValidateStageInput
    ) -> UpdateContextAndValidateStageOutput:
        """Update device context and validate render/files."""
        # Update local device context with firmware targets
        await workflow.execute_activity(
            update_device_context,
            UpdateDeviceContextInput(
                device_data=stage_input.device_data,
                bundle_version=stage_input.bundle_version,
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        # Validate that render contains new targets
        await workflow.execute_activity(
            validate_render_targets,
            ValidateRenderTargetsInput(
                device_data=stage_input.device_data,
                desired_firmware=stage_input.desired_firmware,
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        # Validate that target files exist on ZTP server
        # TEMPORARILY DISABLED UNTIL MTLS INGRESS ENABLED IN UTILITY CLUSTERS
        # await workflow.execute_activity(
        #     validate_target_files,
        #     ValidateTargetFilesInput(
        #         device_data=stage_input.device_data,
        #         desired_firmware=stage_input.desired_firmware,
        #     ),
        #     start_to_close_timeout=timedelta(minutes=2),
        #     retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        # )

        display = (
            f"## ✅ Device Context Updated & Validated\n\n"
            f"**Device context updated** with firmware bundle version `{stage_input.bundle_version}`\n\n"
            f"**Render validation passed** - firmware commands template contains all required firmware files\n\n"
            f"**Note:** Target file validation temporarily disabled (MTLS ingress not enabled in utility clusters)\n\n"
            f"Ready to proceed with firmware upgrade execution."
        )

        return NVLinkSwitchFirmwareUpgradeWorkflow.UpdateContextAndValidateStageOutput(
            display=display
        )

    class ExecuteFirmwareUpgradeStageInput(StageInput):
        """Execute Firmware Upgrade Stage Input."""

        device_data: NetworkDeviceData

    class ExecuteFirmwareUpgradeStageOutput(StageOutput):
        """Execute Firmware Upgrade Stage Output."""

    @stage_executor("execute_firmware_upgrade")
    async def execute_firmware_upgrade(
        self, stage_input: ExecuteFirmwareUpgradeStageInput
    ) -> ExecuteFirmwareUpgradeStageOutput:
        """Execute factory reset and wait for ZTP completion."""
        # Trigger factory reset
        ztp_execute_result = await workflow.execute_activity(
            execute_ztp,
            ExecuteZTPInput(device_data=stage_input.device_data),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        # Wait for ZTP success (with extended timeout for firmware upgrades)
        # This now includes reboot verification using the ZTP execution timestamp
        ztp_result = await workflow.execute_activity(
            poll_ztp_status,
            PollZTPStatusInput(
                device_data=stage_input.device_data,
                timeout_minutes=110,  # 110 minutes polling with 10 minute buffer for activity
                ztp_execution_timestamp=ztp_execute_result.start_time,  # Verify reboot happened
            ),
            start_to_close_timeout=timedelta(minutes=120),  # Extended for firmware
            heartbeat_timeout=timedelta(minutes=5),  # Detect dead activities within 5 minutes
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        if not ztp_result.success:
            raise ApplicationError(
                "ZTP failed to complete within 110 minutes, check the device logs for details."
            )

        display = (
            "## ✅ Firmware Upgrade Execution Complete\n\n"
            "**Factory reset triggered** - Device initiated ZTP process with firmware upgrade\n\n"
            "**ZTP completed successfully** - Device has been reprovisioned with new firmware\n\n"
            "Proceeding to validate final firmware versions..."
        )

        return NVLinkSwitchFirmwareUpgradeWorkflow.ExecuteFirmwareUpgradeStageOutput(
            display=display
        )

    class ValidateFirmwareUpgradeStageInput(StageInput):
        """Validate Firmware Upgrade Stage Input."""

        device_data: NetworkDeviceData
        desired_firmware: dict[str, str]
        bundle_version: str

    class ValidateFirmwareUpgradeStageOutput(StageOutput):
        """Validate Firmware Upgrade Stage Output."""

        upgrade_successful: bool

    @stage_executor("validate_firmware_upgrade")
    async def validate_firmware_upgrade(
        self, stage_input: ValidateFirmwareUpgradeStageInput
    ) -> ValidateFirmwareUpgradeStageOutput:
        """Check final firmware versions and reboot if needed."""
        # Get current OS version
        os_result = await workflow.execute_activity(
            get_current_os,
            GetCurrentOSInput(device_data=stage_input.device_data),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        # Get current firmware versions
        firmware_result = await workflow.execute_activity(
            get_running_firmware,
            GetRunningFirmwareInput(device_data=stage_input.device_data),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        # Compare running vs desired firmware versions
        firmware_check = await workflow.execute_activity(
            compare_running_desired,
            CompareRunningDesiredInput(
                device_data=stage_input.device_data,
                running_os=os_result.running_os,
                running_firmware=firmware_result.running_firmware,
                bundle_version=stage_input.bundle_version,
            ),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        if not firmware_check.upgrade_needed:
            display = (
                f"## ✅ Firmware Upgrade Successful!\n\n"
                f"All firmware components have been upgraded and match the desired versions.\n\n"
                f"**Final OS Version:** `{os_result.running_os}`\n\n"
                f"{_format_firmware_dict(firmware_result.running_firmware, 'Final Firmware Versions')}"
            )
            return NVLinkSwitchFirmwareUpgradeWorkflow.ValidateFirmwareUpgradeStageOutput(
                upgrade_successful=True,
                display=display,
            )

        # If firmware doesn't match, reboot and check again
        reboot_output = await workflow.execute_activity(
            reboot_device,
            RebootDeviceInput(device_data=stage_input.device_data),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        # Wait for device to come back online after reboot
        reboot_result = await workflow.execute_activity(
            wait_reboot,
            WaitRebootInput(
                device_data=stage_input.device_data,
                ztp_execution_timestamp=reboot_output.start_time,
                timeout=10,  # 10 minutes timeout for reboot
            ),
            start_to_close_timeout=timedelta(minutes=12),
            heartbeat_timeout=timedelta(minutes=2),  # Detect dead activities within 2 minutes
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        if not reboot_result.success:
            raise ApplicationError("Device did not come back online after reboot within 10 minutes")

        # Check firmware again after reboot
        # Get current OS version
        post_reboot_os_result = await workflow.execute_activity(
            get_current_os,
            GetCurrentOSInput(device_data=stage_input.device_data),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        # Get current firmware versions
        post_reboot_firmware_result = await workflow.execute_activity(
            get_running_firmware,
            GetRunningFirmwareInput(device_data=stage_input.device_data),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        # Compare running vs desired firmware versions after reboot
        post_reboot_check = await workflow.execute_activity(
            compare_running_desired,
            CompareRunningDesiredInput(
                device_data=stage_input.device_data,
                running_os=post_reboot_os_result.running_os,
                running_firmware=post_reboot_firmware_result.running_firmware,
                bundle_version=stage_input.bundle_version,
            ),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        if not post_reboot_check.upgrade_needed:
            display = (
                f"## ✅ Firmware Upgrade Successful After Reboot!\n\n"
                f"Device was rebooted to activate new firmware. All components now match the desired versions.\n\n"
                f"**Final OS Version:** `{post_reboot_os_result.running_os}`\n\n"
                f"{_format_firmware_dict(post_reboot_firmware_result.running_firmware, 'Final Firmware Versions')}"
            )
            return NVLinkSwitchFirmwareUpgradeWorkflow.ValidateFirmwareUpgradeStageOutput(
                upgrade_successful=True,
                display=display,
            )

        # Final validation failed
        display = (
            f"## ❌ Firmware Upgrade Failed\n\n"
            f"Device was rebooted but firmware mismatches persist. Manual intervention may be required.\n\n"
            f"**Current OS Version:** `{post_reboot_os_result.running_os}`\n\n"
            f"{_format_firmware_differences(post_reboot_check.differences)}\n\n"
            f"{_format_firmware_dict(post_reboot_firmware_result.running_firmware, 'Current Firmware Versions')}"
        )

        raise ApplicationError(
            f"Firmware upgrade failed with persistent mismatches: {post_reboot_check.differences}"
        )

    @run_nv_config_manager_workflow
    async def run(self, workflow_input: NVLinkSwitchFirmwareUpgradeInput) -> bool:  # type: ignore[override, ty:invalid-method-override]
        """Execute NVLinkSwitch firmware upgrade workflow."""
        self.set_input(workflow_input)

        # Get current state
        current_state = await self.get_current_state(
            NVLinkSwitchFirmwareUpgradeWorkflow.GetCurrentStateStageInput(
                device_id=workflow_input.device_id
            )
        )

        if not current_state.supported:
            # Platform not supported
            self.set_stage_state("compare_versions", StateEnum.UNREACHABLE)
            self.set_stage_state("perform_backup", StateEnum.UNREACHABLE)
            self.set_stage_state("update_context_and_validate", StateEnum.UNREACHABLE)
            self.set_stage_state("execute_firmware_upgrade", StateEnum.UNREACHABLE)
            self.set_stage_state("validate_firmware_upgrade", StateEnum.UNREACHABLE)
            return False

        # Compare versions
        comparison = await self.compare_versions(
            NVLinkSwitchFirmwareUpgradeWorkflow.CompareVersionsStageInput(
                device_data=current_state.device_data,
                running_os=current_state.running_os,
                running_firmware=current_state.running_firmware,
                bundle_version=workflow_input.bundle_version,
            )
        )

        if not comparison.upgrade_needed:
            # No upgrade needed
            self.set_stage_state("perform_backup", StateEnum.UNREACHABLE)
            self.set_stage_state("update_context_and_validate", StateEnum.UNREACHABLE)
            self.set_stage_state("execute_firmware_upgrade", StateEnum.UNREACHABLE)
            self.set_stage_state("validate_firmware_upgrade", StateEnum.UNREACHABLE)
            return False

        # Perform backup
        backup_output = await self.perform_backup(
            NVLinkSwitchFirmwareUpgradeWorkflow.BackupStageInput(device_id=workflow_input.device_id)
        )
        if backup_output.config_drift:
            self.set_stage_state("update_context_and_validate", StateEnum.UNREACHABLE)
            self.set_stage_state("execute_firmware_upgrade", StateEnum.UNREACHABLE)
            self.set_stage_state("validate_firmware_upgrade", StateEnum.UNREACHABLE)
            return False

        # Update context and validate
        await self.update_context_and_validate(
            NVLinkSwitchFirmwareUpgradeWorkflow.UpdateContextAndValidateStageInput(
                device_data=current_state.device_data,
                bundle_version=workflow_input.bundle_version,
                desired_firmware=comparison.desired_firmware,
            )
        )

        # Execute firmware upgrade
        await self.execute_firmware_upgrade(
            NVLinkSwitchFirmwareUpgradeWorkflow.ExecuteFirmwareUpgradeStageInput(
                device_data=current_state.device_data,
            )
        )

        # Validate firmware upgrade
        validation = await self.validate_firmware_upgrade(
            NVLinkSwitchFirmwareUpgradeWorkflow.ValidateFirmwareUpgradeStageInput(
                device_data=current_state.device_data,
                desired_firmware=comparison.desired_firmware,
                bundle_version=workflow_input.bundle_version,
            )
        )

        await self.archive_results()
        return validation.upgrade_successful
