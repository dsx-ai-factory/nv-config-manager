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
"""Infiniband Mellanox OS Upgrade Workflow Definition."""

from datetime import timedelta

from pydantic import BaseModel, Field
from temporalio import workflow
from temporalio.common import RetryPolicy

from nv_config_manager.temporal.common.decorators.workflow import run_nv_config_manager_workflow
from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData
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
    from nv_config_manager.temporal.ngc.activities.dcim import (
        GetNetworkDeviceInput,
        get_network_device,
    )
    from nv_config_manager.temporal.ngc.activities.os import (
        CleanupMlnxOSInput,
        DownloadMlnxOSInput,
        GetMlnxOSVersionInput,
        GetOSImageVersionsInput,
        InstallMlnxOSInput,
        ReloadMlnxOSInput,
        cleanup_mlnx_os,
        download_mlnx_os,
        get_mlnx_os_version,
        get_os_image_versions,
        install_mlnx_os,
        reload_mlnx_os,
    )

DEFAULT_ACTIVITY_RETRY_POLICY = RetryPolicy(
    maximum_attempts=3,
    non_retryable_error_types=[],
)


class InfinibandMlnxOSUpgradeInput(BaseModel):
    """Infiniband Mellanox OS Upgrade Workflow Input Definition."""

    device_id: DeviceReference = Field(
        description="Identifier of the InfiniBand switch to upgrade."
    )


@workflow.defn
class InfinibandMlnxOSUpgradeWorkflow(WorkflowMetadataMixin, StageMixin):
    """Infiniband Mellanox OS upgrade workflow.

    For high-performance computing infrastructure.
    """

    # Workflow metadata
    workflow_name = "InfiniBand MLNX-OS Upgrade"
    workflow_description = (
        "Upgrade MLNX-OS on Infiniband switches with validation and rollback capabilities"
    )
    workflow_input_class = InfinibandMlnxOSUpgradeInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/ngc/infiniband_mlnx_os_upgrade"
    workflow_namespace = "ngc"

    class GetOSImageVersionsStageInput(StageInput):
        """Get OS Image Versions Stage Input."""

        device_id: str

    class GetOSImageVersionsStageOutput(StageOutput):
        """Get OS Image Versions Stage Output."""

        intended_firmware: str
        ztp_ipv4_address: str
        display: str

    class GetDeviceInfoStageInput(StageInput):
        """Get Device Info Stage Input."""

        device_id: str

    class GetDeviceInfoStageOutput(StageOutput):
        """Get Device Info Stage Output."""

        device_data: NetworkDeviceData
        display: str

    class GetVersionsStageInput(StageInput):
        """Get Versions Stage Input."""

        device_data: NetworkDeviceData

    class GetVersionsStageOutput(StageOutput):
        """Get Versions Stage Output."""

        initial_versions: list[str]
        display: str

    class DownloadOSStageInput(StageInput):
        """Download OS Stage Input."""

        device_data: NetworkDeviceData
        ztp_ipv4_address: str
        intended_version: str

    class DownloadOSStageOutput(StageOutput):
        """Download OS Stage Output."""

        download_status: str
        image_name: str
        display: str

    class ApproveUpgradeStageInput(StageInput):
        """Approve Upgrade Stage Input."""

        device_data: NetworkDeviceData
        intended_version: str
        initial_versions: list[str]

    class ApproveUpgradeStageOutput(StageOutput):
        """Approve Upgrade Stage Output."""

        device_data: NetworkDeviceData
        intended_version: str
        initial_versions: list[str]
        approved: bool = False
        display: str

    class UpgradePartitionStageInput(StageInput):
        """Upgrade Partition Stage Input."""

        device_data: NetworkDeviceData
        intended_version: str
        image_name: str
        partition: int

    class UpgradePartitionStageOutput(StageOutput):
        """Upgrade Partition Stage Output."""

        install_status: str
        save_config_status: str
        reload_status: str
        is_online: bool
        display: str

    class CleanupOSStageInput(StageInput):
        """Cleanup OS Stage Input."""

        device_data: NetworkDeviceData
        image_name: str

    class CleanupOSStageOutput(StageOutput):
        """Cleanup OS Stage Output."""

        cleanup_status: str
        display: str

    def __init__(self) -> None:
        """Initialize workflow."""
        StageMixin.__init__(self)
        self.define_stage(
            name="get_intended_os_version",
            description="Get intended OS version.",
            requires_approval=False,
            depends_on=[],
        )
        self.define_stage(
            name="get_device_info",
            description="Get device information from the DCIM.",
            requires_approval=False,
            depends_on=[],
        )
        self.define_stage(
            name="get_current_os_versions",
            description="Get current OS image versions on the device.",
            requires_approval=False,
            depends_on=["get_device_info"],
        )
        self.define_stage(
            name="approve_upgrade",
            description="Review and approve OS upgrade.",
            requires_approval=True,
            approval_threshold=1,
            depends_on=["get_current_os_versions"],
        )
        self.define_stage(
            name="download_os",
            description="Download OS image.",
            requires_approval=False,
            depends_on=["approve_upgrade"],
        )
        self.define_stage(
            name="upgrade_partition_1",
            description="Upgrade first partition and reload.",
            requires_approval=False,
            depends_on=["download_os"],
        )
        self.define_stage(
            name="upgrade_partition_2",
            description="Upgrade second partition and reload.",
            requires_approval=False,
            depends_on=["upgrade_partition_1"],
        )
        self.define_stage(
            name="cleanup_os",
            description="Clean up OS images.",
            requires_approval=False,
            depends_on=["upgrade_partition_2"],
        )

    @stage_executor("get_intended_os_version")
    async def get_intended_os_version(
        self, stage_input: GetOSImageVersionsStageInput
    ) -> GetOSImageVersionsStageOutput:
        """Get available OS image versions."""
        firmware_versions = await workflow.execute_activity(
            get_os_image_versions,
            GetOSImageVersionsInput(device_id=stage_input.device_id),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        return InfinibandMlnxOSUpgradeWorkflow.GetOSImageVersionsStageOutput(
            intended_firmware=firmware_versions.intended_firmware,
            ztp_ipv4_address=firmware_versions.ztp_ipv4_address,
            display="Intended OS version retrieved successfully.",
        )  # noqa: E501

    @stage_executor("get_device_info")
    async def get_device_info(
        self, stage_input: GetDeviceInfoStageInput
    ) -> GetDeviceInfoStageOutput:
        """Execute device lookup."""
        device_result = await workflow.execute_activity(
            get_network_device,
            GetNetworkDeviceInput(
                device_id=stage_input.device_id,
            ),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        if not device_result.device.host:
            raise ValueError("Device has no primary IP address set in the DCIM.")

        return InfinibandMlnxOSUpgradeWorkflow.GetDeviceInfoStageOutput(
            device_data=device_result.device,
            display="Device information retrieved successfully.",
        )

    @stage_executor("get_current_os_versions")
    async def get_current_os_versions(
        self, stage_input: GetVersionsStageInput
    ) -> GetVersionsStageOutput:
        """Get initial OS versions."""
        initial_version_result = await workflow.execute_activity(
            get_mlnx_os_version,
            GetMlnxOSVersionInput(device_data=stage_input.device_data),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        return InfinibandMlnxOSUpgradeWorkflow.GetVersionsStageOutput(
            initial_versions=initial_version_result.current_os_versions,
            display="Current OS versions retrieved successfully.",
        )

    @stage_executor("approve_upgrade")
    async def approve_upgrade(
        self, stage_input: ApproveUpgradeStageInput
    ) -> ApproveUpgradeStageOutput:
        """Review and approve OS upgrade."""
        stage_name = "approve_upgrade"
        markdown = (
            f"OS Upgrade For Approval\n"
            f"Current Versions: {', '.join(stage_input.initial_versions)}\n"
            f"Intended Version: {stage_input.intended_version}"
        )
        output = InfinibandMlnxOSUpgradeWorkflow.ApproveUpgradeStageOutput(
            device_data=stage_input.device_data,
            intended_version=stage_input.intended_version,
            initial_versions=stage_input.initial_versions,
            display=markdown,
        )
        self.set_stage_output(stage_name, output)
        self.set_stage_state(stage_name, StateEnum.PENDING_APPROVAL)
        await workflow.wait_condition(
            lambda: self.get_stage_state(stage_name) != StateEnum.PENDING_APPROVAL
        )

        approved = self.get_stage_state(stage_name) == StateEnum.APPROVED
        if approved:
            approval_state = "Approved"
            stage = self.get_stage_by_name(stage_name)
            reviewers = [approver.user for approver in stage.approvers]
        else:
            approval_state = "Rejected"
            stage = self.get_stage_by_name(stage_name)
            reviewers = [rejecter.user for rejecter in stage.rejecters]

        reviewmd = ",".join(reviewers)
        markdown = (
            f"OS Upgrade {approval_state} by {reviewmd}:\n"
            f"Current Versions: {', '.join(stage_input.initial_versions)}\n"
            f"Intended Version: {stage_input.intended_version}"
        )
        return InfinibandMlnxOSUpgradeWorkflow.ApproveUpgradeStageOutput(
            device_data=stage_input.device_data,
            intended_version=stage_input.intended_version,
            initial_versions=stage_input.initial_versions,
            display=markdown,
            approved=approved,
        )

    @stage_executor("download_os")
    async def download_os(self, stage_input: DownloadOSStageInput) -> DownloadOSStageOutput:
        """Download OS image."""
        download_result = await workflow.execute_activity(
            download_mlnx_os,
            DownloadMlnxOSInput(
                device_data=stage_input.device_data,
                ztp_ipv4_address=stage_input.ztp_ipv4_address,
                intended_version=stage_input.intended_version,
            ),
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        return InfinibandMlnxOSUpgradeWorkflow.DownloadOSStageOutput(
            download_status=download_result.download_status,
            image_name=download_result.image_name,
            display="OS image downloaded successfully.",
        )

    @stage_executor("upgrade_partition_1")
    async def upgrade_partition_1(
        self, stage_input: UpgradePartitionStageInput
    ) -> UpgradePartitionStageOutput:
        """Upgrade first partition and reload."""
        install_result = await workflow.execute_activity(
            install_mlnx_os,
            InstallMlnxOSInput(
                device_data=stage_input.device_data,
                image_name=stage_input.image_name,
            ),
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        reload_result = await workflow.execute_activity(
            reload_mlnx_os,
            ReloadMlnxOSInput(
                device_data=stage_input.device_data,
            ),
            start_to_close_timeout=timedelta(minutes=35),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        await workflow.sleep(timedelta(minutes=3))

        return InfinibandMlnxOSUpgradeWorkflow.UpgradePartitionStageOutput(
            install_status=install_result.install_status,
            save_config_status=reload_result.save_config_status,
            reload_status=reload_result.reload_status,
            is_online=reload_result.is_online,
            display="First partition upgrade completed successfully.",
        )

    @stage_executor("upgrade_partition_2")
    async def upgrade_partition_2(
        self, stage_input: UpgradePartitionStageInput
    ) -> UpgradePartitionStageOutput:
        """Upgrade second partition and reload."""
        install_result = await workflow.execute_activity(
            install_mlnx_os,
            InstallMlnxOSInput(
                device_data=stage_input.device_data,
                image_name=stage_input.image_name,
            ),
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        reload_result = await workflow.execute_activity(
            reload_mlnx_os,
            ReloadMlnxOSInput(
                device_data=stage_input.device_data,
            ),
            start_to_close_timeout=timedelta(minutes=35),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        await workflow.sleep(timedelta(minutes=3))

        final_version_result = await workflow.execute_activity(
            get_mlnx_os_version,
            GetMlnxOSVersionInput(device_data=stage_input.device_data),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        return InfinibandMlnxOSUpgradeWorkflow.UpgradePartitionStageOutput(
            install_status=install_result.install_status,
            save_config_status=reload_result.save_config_status,
            reload_status=reload_result.reload_status,
            is_online=reload_result.is_online,
            display=(
                f"Second partition upgrade completed successfully. "
                f"Final versions: "
                f"{', '.join(final_version_result.current_os_versions)}"
            ),
        )

    @stage_executor("cleanup_os")
    async def cleanup_os(self, stage_input: CleanupOSStageInput) -> CleanupOSStageOutput:
        """Clean up OS images."""
        cleanup_result = await workflow.execute_activity(  # noqa: E501
            cleanup_mlnx_os,
            CleanupMlnxOSInput(
                device_data=stage_input.device_data,
                image_name=stage_input.image_name,
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        return InfinibandMlnxOSUpgradeWorkflow.CleanupOSStageOutput(
            cleanup_status=cleanup_result.cleanup_status,
            display="OS cleanup completed successfully.",
        )

    @run_nv_config_manager_workflow
    async def run(  # type: ignore[override, ty:invalid-method-override]
        self, workflow_input: InfinibandMlnxOSUpgradeInput
    ) -> str:
        """Run the workflow."""

        intended = await self.get_intended_os_version(
            self.GetOSImageVersionsStageInput(device_id=workflow_input.device_id)
        )

        intended_version = intended.intended_firmware

        device_info = await self.get_device_info(
            self.GetDeviceInfoStageInput(device_id=workflow_input.device_id)
        )

        current = await self.get_current_os_versions(
            self.GetVersionsStageInput(device_data=device_info.device_data)
        )

        if (
            current.initial_versions[0] == intended_version
            and current.initial_versions[1] == intended_version
        ):
            self.set_stage_state("approve_upgrade", StateEnum.UNREACHABLE)
            self.set_stage_state("download_os", StateEnum.UNREACHABLE)
            self.set_stage_state("upgrade_partition_1", StateEnum.UNREACHABLE)
            self.set_stage_state("upgrade_partition_2", StateEnum.UNREACHABLE)
            self.set_stage_state("cleanup_os", StateEnum.UNREACHABLE)
            return (
                f"Device is already running the intended version "
                f"{intended_version} on both partitions. No upgrade needed."
            )

        approve_output = await self.approve_upgrade(
            self.ApproveUpgradeStageInput(
                device_data=device_info.device_data,
                intended_version=intended_version,
                initial_versions=current.initial_versions,
            )
        )

        if not approve_output.approved:
            return "OS upgrade was rejected by approvers."

        download_output = await self.download_os(
            self.DownloadOSStageInput(
                device_data=device_info.device_data,
                ztp_ipv4_address=intended.ztp_ipv4_address,
                intended_version=intended_version,
            )
        )

        await self.upgrade_partition_1(
            self.UpgradePartitionStageInput(
                device_data=device_info.device_data,
                intended_version=intended_version,
                image_name=download_output.image_name,
                partition=1,
            )
        )

        await self.upgrade_partition_2(
            self.UpgradePartitionStageInput(
                device_data=device_info.device_data,
                intended_version=intended_version,
                image_name=download_output.image_name,
                partition=2,
            )
        )

        await self.cleanup_os(
            self.CleanupOSStageInput(
                device_data=device_info.device_data,
                image_name=download_output.image_name,
            )  # noqa: E501
        )

        return (
            f"\nCommand Output: Successfully upgraded device to "
            f"{intended_version} on both partitions.\n"
        )
