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
"""SpX Overlay Workflows."""

import asyncio
from datetime import timedelta
from typing import Annotated

from pydantic import BaseModel, Field
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError, ChildWorkflowError

from nv_config_manager.temporal.common.decorators.workflow import run_nv_config_manager_workflow
from nv_config_manager.temporal.common.mixins.metadata import WorkflowMetadataMixin
from nv_config_manager.temporal.common.mixins.stage import (
    StageInput,
    StageMixin,
    StageOutput,
    StateEnum,
    stage_executor,
)
from nv_config_manager.temporal.common.workflow_references import (
    DEVICE_REFERENCE,
    DeviceReference,
    LocationReference,
)

with workflow.unsafe.imports_passed_through():
    from nv_config_manager.dcim import DeviceVRF
    from nv_config_manager.temporal.common.mixins.archive import ArchiveMixin
    from nv_config_manager.temporal.common.mixins.device import DeviceMixin, NetworkDeviceData
    from nv_config_manager.temporal.ngc.activities.dcim import (
        AssignVrfToDeviceInput,
        AssignVrfToInterfaceInput,
        CheckRecordedConfigDriftInput,
        DeleteOverlayInput,
        GetAvailableRouteDistinguishersInput,
        GetDeviceInterfacesInput,
        GetDeviceVrfsInput,
        GetNetworkDeviceInput,
        ProvisionVrfInput,
        QueryVRFByVPCInput,
        ReconcileSpXOverlayAssignmentsInput,
        RemoveUnmappedDeviceVrfsInput,
        Vrf,
        VrfDeletionActivityInput,
        _vni_from_rd,
        assign_vrf_to_device,
        assign_vrf_to_interface,
        check_recorded_config_drift,
        delete_overlay,
        delete_vrf,
        get_available_route_distinguishers,
        get_device_interfaces,
        get_device_vrfs,
        get_network_device,
        get_vrfs_by_overlay_id,
        provision_vrf,
        reconcile_spx_overlay_assignments,
        remove_unmapped_device_vrfs,
    )
    from nv_config_manager.temporal.ngc.activities.deploy import (
        WaitForTenantRenderInput,
        wait_for_tenant_render,
    )
    from nv_config_manager.temporal.ngc.activities.render import (
        ExecuteRenderInput,
        execute_render,
    )
    from nv_config_manager.temporal.ngc.workflows.deploy import (
        TenantDeployInput,
        TenantDeployWorkflow,
    )


RD_MIN = 60000


RD_MAX = 65000


NAMESPACE_TAG = "spectrumx"


DEFAULT_ACTIVITY_RETRY_POLICY = RetryPolicy(
    maximum_attempts=3,
)


class SpXOverlayCreationInput(BaseModel):
    """SpX Overlay Creation Workflow Input Definition."""

    site: LocationReference = Field(description="Site where the SpX overlay will be created.")
    overlay_id: str = Field(
        title="Overlay ID",
        description="Unique identifier for the SpX overlay. Used as an idempotency key — re-running with the same ID returns existing VRFs without creating new ones.",
    )
    tenant: str = Field(description="Tenant that will own the SpX overlay.")
    namespace_tag: str = Field(
        default=NAMESPACE_TAG, description="Tag identifying the namespace used for allocation."
    )
    rd_min: int = Field(
        default=RD_MIN,
        title="RD Min",
        description="Lower bound of the route-distinguisher allocation range (0–65535). The first available RD in [rd_min, rd_max] is allocated.",
    )
    rd_max: int = Field(
        default=RD_MAX,
        title="RD Max",
        description="Upper bound of the route-distinguisher allocation range (0–65535). Must be greater than rd_min.",
    )


class SpXOverlayCreationWorkflowOutput(BaseModel):
    """SpX Overlay Creation Workflow Output Definition."""

    created_vrfs: list[Vrf]
    existing_vrfs: list[Vrf]


@workflow.defn
class SpXOverlayCreationWorkflow(WorkflowMetadataMixin, StageMixin, ArchiveMixin):
    """SpX Overlay creation workflow for network virtualization."""

    # Workflow metadata
    workflow_name = "SpX Overlay Creation"
    workflow_description = (
        "Create a SpX Overlay with route distinguisher assignment and VRF/VXLAN provisioning"
    )
    workflow_input_class = SpXOverlayCreationInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/ngc/spx_overlay_creation"
    workflow_namespace = "ngc"

    def __init__(self) -> None:
        """Initialize workflow."""
        StageMixin.__init__(self)
        self.define_stage(
            name="create_spx_overlay",
            description="Assign an RD and create VRF.",
            requires_approval=False,
            depends_on=[],
        )

    class CreateSpXOverlayStageInput(StageInput):
        """Create VPC Stage Input."""

        namespace_tag: str
        overlay_id: str
        site: str
        tenant: str
        rd_min: int
        rd_max: int

    class CreateSpXOverlayStageOutput(StageOutput):
        """Create VPC Stage Output."""

        created_vrfs: list[Vrf]
        existing_vrfs: list[Vrf]

    @stage_executor("create_spx_overlay")
    async def create_spx_overlay(
        self, stage_input: CreateSpXOverlayStageInput
    ) -> CreateSpXOverlayStageOutput:
        """Create VPC Stage."""
        # Ensure no existing VRFs with this VPC ID
        existing_vrfs = await workflow.execute_activity(
            get_vrfs_by_overlay_id,
            QueryVRFByVPCInput(
                overlay_id=stage_input.overlay_id,
                namespace_tag=stage_input.namespace_tag,
                site=stage_input.site,
            ),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        if existing_vrfs:
            return self.CreateSpXOverlayStageOutput(
                created_vrfs=[],
                existing_vrfs=existing_vrfs,
                display=(
                    f"VRFs already exist for Overlay ID {stage_input.overlay_id}:\n "
                    f"{self.markdown_table(existing_vrfs, exclude={'interfaces'})}"
                ),
            )
        rd_results = await workflow.execute_activity(
            get_available_route_distinguishers,
            GetAvailableRouteDistinguishersInput(
                site=stage_input.site,
                namespace_tag=stage_input.namespace_tag,
                rd_min=stage_input.rd_min,
                rd_max=stage_input.rd_max,
            ),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        await workflow.execute_activity(
            provision_vrf,
            ProvisionVrfInput(
                namespaces=rd_results.namespaces,
                route_distinguisher=rd_results.route_distinguisher,
                overlay_id=stage_input.overlay_id,
                site=stage_input.site,
                tenant=stage_input.tenant,
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        created_vrfs = await workflow.execute_activity(
            get_vrfs_by_overlay_id,
            QueryVRFByVPCInput(
                overlay_id=stage_input.overlay_id,
                namespace_tag=stage_input.namespace_tag,
                site=stage_input.site,
            ),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        if created_vrfs:
            return self.CreateSpXOverlayStageOutput(
                created_vrfs=created_vrfs,
                existing_vrfs=[],
                display=(
                    f"Created VRFs:\n{self.markdown_table(created_vrfs, exclude={'interfaces'})}"
                ),
            )
        raise ApplicationError("Failed to create VRFs")

    @run_nv_config_manager_workflow
    async def run(  # type: ignore[override, ty:invalid-method-override]  # pyright: ignore
        self, workflow_input: SpXOverlayCreationInput
    ) -> SpXOverlayCreationWorkflowOutput:
        """Execute the VPC Creation workflow."""
        self.set_input(workflow_input)
        vrf_output = await self.create_spx_overlay(
            self.CreateSpXOverlayStageInput(
                namespace_tag=workflow_input.namespace_tag,
                overlay_id=workflow_input.overlay_id,
                site=workflow_input.site,
                tenant=workflow_input.tenant,
                rd_min=workflow_input.rd_min,
                rd_max=workflow_input.rd_max,
            )
        )

        await self.archive_results()
        return SpXOverlayCreationWorkflowOutput(
            created_vrfs=vrf_output.created_vrfs, existing_vrfs=vrf_output.existing_vrfs
        )


class SpXOverlayDeletionInput(BaseModel):
    """SpX Overlay Deletion Workflow Input Definition."""

    site: LocationReference = Field(description="Site containing the SpX overlay to delete.")
    overlay_id: str = Field(
        title="Overlay ID",
        description="Identifier of the SpX overlay to delete.",
    )
    namespace_tag: str = Field(
        default=NAMESPACE_TAG, description="Tag identifying the namespace used for allocation."
    )


class SpXOverlayDeletionWorkflowOutput(BaseModel):
    """SpX Overlay Deletion Workflow Output Definition."""

    deleted_vrfs: list[Vrf]
    in_use_vrfs: list[Vrf]


@workflow.defn
class SpXOverlayDeletionWorkflow(WorkflowMetadataMixin, StageMixin, ArchiveMixin):
    """SpX Overlay deletion workflow for network virtualization cleanup."""

    # Workflow metadata
    workflow_name = "SpX Overlay Deletion"
    workflow_description = (
        "Delete a SpX Overlay and its associated VRFs/VXLANs with validation checks"
    )
    workflow_input_class = SpXOverlayDeletionInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/ngc/spx_overlay_deletion"
    workflow_namespace = "ngc"

    def __init__(self) -> None:
        """Initialize workflow."""
        StageMixin.__init__(self)
        self.define_stage(
            name="delete_spx_overlay",
            description="Validate and delete DCIM VRFs tied to the VPC.",
            requires_approval=False,
            depends_on=[],
        )

    class DeleteSpXOverlayStageInput(StageInput):
        """Create VPC Stage Input."""

        overlay_id: str
        site: str
        namespace_tag: str = NAMESPACE_TAG

    class DeleteSpXOverlayStageOutput(StageOutput):
        """Create VPC Stage Output."""

        deleted_vrfs: list[Vrf]
        in_use_vrfs: list[Vrf]

    @stage_executor("delete_spx_overlay")
    async def delete_spx_overlay(
        self, stage_input: DeleteSpXOverlayStageInput
    ) -> DeleteSpXOverlayStageOutput:
        """Delete VPC Stage."""
        existing_vrfs = await workflow.execute_activity(
            get_vrfs_by_overlay_id,
            QueryVRFByVPCInput(
                overlay_id=stage_input.overlay_id,
                namespace_tag=stage_input.namespace_tag,
                site=stage_input.site,
            ),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        if not existing_vrfs:
            overlay_result = await workflow.execute_activity(
                delete_overlay,
                DeleteOverlayInput(
                    overlay_id=stage_input.overlay_id,
                    site=stage_input.site,
                ),
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            )
            display = (
                f"Deleted overlay {overlay_result.overlay_name}"
                if overlay_result.deleted
                else f"No VRFs or overlay exist for Overlay ID {stage_input.overlay_id}"
            )
            return self.DeleteSpXOverlayStageOutput(
                deleted_vrfs=[],
                in_use_vrfs=[],
                display=display,
            )

        in_use_vrfs = [vrf for vrf in existing_vrfs if vrf.interface_count > 0]
        if in_use_vrfs:
            return self.DeleteSpXOverlayStageOutput(
                in_use_vrfs=in_use_vrfs,
                deleted_vrfs=[],
                display=(
                    f"Unable to delete Overlay {stage_input.overlay_id}, "
                    f"the following VRFs are in use:\n "
                    f"{self.markdown_table(in_use_vrfs, exclude={'interfaces'})}"
                ),
            )

        # Delete VRFs and their bound VXLANs
        tasks = []
        for vrf in existing_vrfs:
            task = workflow.execute_activity(
                delete_vrf,
                VrfDeletionActivityInput(
                    vrf_id=vrf.id,
                    vnid=_vni_from_rd(vrf.rd),
                ),
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            )
            tasks.append(task)
        await asyncio.gather(*tasks)

        # Clean up the SpectrumX overlay if no VXLANs/assignments remain
        overlay_result = await workflow.execute_activity(
            delete_overlay,
            DeleteOverlayInput(
                overlay_id=stage_input.overlay_id,
                site=stage_input.site,
            ),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        overlay_message = (
            f"Deleted overlay {overlay_result.overlay_name}"
            if overlay_result.deleted
            else f"Left overlay {overlay_result.overlay_name} in place"
        )
        return self.DeleteSpXOverlayStageOutput(
            deleted_vrfs=existing_vrfs,
            in_use_vrfs=[],
            display=(
                f"VRFs deleted for Overlay ID {stage_input.overlay_id}:\n"
                f"{self.markdown_table(existing_vrfs, exclude={'interfaces'})}\n\n"
                f"{overlay_message}"
            ),
        )

    @run_nv_config_manager_workflow
    async def run(  # type: ignore[override, ty:invalid-method-override]  # pyright: ignore
        self, workflow_input: SpXOverlayDeletionInput
    ) -> SpXOverlayDeletionWorkflowOutput:
        """Execute the VPC Deletion workflow."""
        self.set_input(workflow_input)
        vrf_output = await self.delete_spx_overlay(
            self.DeleteSpXOverlayStageInput(
                overlay_id=workflow_input.overlay_id,
                site=workflow_input.site,
                namespace_tag=workflow_input.namespace_tag,
            )
        )

        await self.archive_results()
        return SpXOverlayDeletionWorkflowOutput(
            deleted_vrfs=vrf_output.deleted_vrfs, in_use_vrfs=vrf_output.in_use_vrfs
        )


class SpXOverlayAssignmentInput(BaseModel):
    """SpX Overlay Assignment Workflow Input Definition."""

    overlay_id: str | None = Field(
        default=None,
        title="Overlay ID",
        description=(
            "Identifier of the SpX overlay whose VRF will be assigned to the device and ports. "
            "Omit the overlay_id property or explicitly set it to null to remove the selected "
            "ports' current SpX assignment."
        ),
    )
    device: Annotated[str | NetworkDeviceData, DEVICE_REFERENCE] = Field(
        description="Identifier or preloaded data for the target network device."
    )
    port_names: list[str] = Field(
        min_length=1, description="Names of the device interfaces to assign to the overlay."
    )
    site: LocationReference = Field(description="Site containing the target network device.")
    namespace_tag: str = Field(
        default=NAMESPACE_TAG, description="Tag identifying the namespace used for allocation."
    )


class SpXOverlayAssignmentWorkflowOutput(BaseModel):
    """SpX Overlay Assignment Workflow Output Definition."""

    assigned_ports: list[str]
    unassigned_ports: list[str]
    vrf_assigned: bool
    removed_vrf_ids: list[str]
    overlay_assignments_created: int
    overlay_assignments_removed: int
    overlay_reconciliation_changed: bool = False
    vrf: DeviceVRF | None


@workflow.defn
class SpXOverlayAssignmentWorkflow(WorkflowMetadataMixin, StageMixin, DeviceMixin, ArchiveMixin):
    """SpX Overlay assignment workflow for assigning VRFs to devices and ports."""

    # Workflow metadata
    workflow_name = "SpX Overlay Assignment"
    workflow_description = "Change or remove a SpX Overlay/VRF assignment on device ports"
    workflow_input_class = SpXOverlayAssignmentInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/ngc/spx_overlay_assignment"
    workflow_namespace = "ngc"

    def __init__(self) -> None:
        """Initialize workflow."""
        StageMixin.__init__(self)
        self.define_stage(
            name="get_device_and_vrf",
            description="Get device and VRF information from the DCIM.",
            requires_approval=False,
            depends_on=[],
        )
        self.define_stage(
            name="assign_vrf_to_device",
            description="Ensure the target VRF is assigned to the device when requested.",
            requires_approval=False,
            depends_on=["get_device_and_vrf"],
        )
        self.define_stage(
            name="assign_vrf_to_ports",
            description="Set or clear the VRF on specified ports and clean up stale associations.",
            requires_approval=False,
            depends_on=["assign_vrf_to_device"],
        )

    class GetDeviceAndVrfStageInput(StageInput):
        """Get Device and VRF Stage Input."""

        overlay_id: str | None
        device: str | NetworkDeviceData
        site: str
        namespace_tag: str

    class GetDeviceAndVrfStageOutput(StageOutput):
        """Get Device and VRF Stage Output."""

        device: NetworkDeviceData
        vrf: Vrf | None

    @stage_executor("get_device_and_vrf")
    async def get_device_and_vrf(
        self, stage_input: GetDeviceAndVrfStageInput
    ) -> GetDeviceAndVrfStageOutput:
        """Get Device and VRF Stage."""
        if isinstance(stage_input.device, str):
            device_output = await workflow.execute_activity(
                get_network_device,
                GetNetworkDeviceInput(device_id=stage_input.device),
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            )
            device = device_output.device
        else:
            device = stage_input.device

        if stage_input.overlay_id is None:
            return self.GetDeviceAndVrfStageOutput(
                device=device,
                vrf=None,
                display=f"Found device: {device.name}; selected ports will be unassigned",
            )

        vrfs = await workflow.execute_activity(
            get_vrfs_by_overlay_id,
            QueryVRFByVPCInput(
                overlay_id=stage_input.overlay_id,
                namespace_tag=stage_input.namespace_tag,
                site=stage_input.site,
            ),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        if not vrfs:
            raise ApplicationError(
                f"No VRF found for Overlay ID {stage_input.overlay_id} in site {stage_input.site}"
            )
        if len(vrfs) > 1:
            raise ApplicationError(
                f"Multiple VRFs found for Overlay ID {stage_input.overlay_id} in site {stage_input.site}"
            )

        return self.GetDeviceAndVrfStageOutput(
            device=device,
            vrf=vrfs[0],
            display=f"Found device: {device.name} and VRF: {vrfs[0].name}",
        )

    class AssignVrfToDeviceStageInput(StageInput):
        """Assign VRF to Device Stage Input."""

        device_id: str
        vrf_id: str | None
        vrf_name: str | None

    class AssignVrfToDeviceStageOutput(StageOutput):
        """Assign VRF to Device Stage Output."""

        already_assigned: bool

    @stage_executor("assign_vrf_to_device")
    async def assign_vrf_to_device_stage(
        self, stage_input: AssignVrfToDeviceStageInput
    ) -> AssignVrfToDeviceStageOutput:
        """Assign VRF to Device Stage."""
        if stage_input.vrf_id is None:
            return self.AssignVrfToDeviceStageOutput(
                already_assigned=True,
                display="No target VRF requested; skipping device assignment",
            )

        device_vrfs = await workflow.execute_activity(
            get_device_vrfs,
            GetDeviceVrfsInput(device_id=stage_input.device_id),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        already_assigned = stage_input.vrf_id in {vrf.vrf_id for vrf in device_vrfs.vrfs}

        if not already_assigned:
            await workflow.execute_activity(
                assign_vrf_to_device,
                AssignVrfToDeviceInput(
                    device_id=stage_input.device_id,
                    vrf_id=stage_input.vrf_id,
                ),
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            )
            display = f"VRF {stage_input.vrf_name} assigned to device"
        else:
            display = f"VRF {stage_input.vrf_name} already assigned to device"

        return self.AssignVrfToDeviceStageOutput(
            already_assigned=already_assigned,
            display=display,
        )

    class AssignVrfToPortsStageInput(StageInput):
        """Assign VRF to Ports Stage Input."""

        device_id: str
        overlay_id: str | None
        site: str
        vrf_id: str | None
        vrf_name: str | None
        port_names: list[str]

    class AssignVrfToPortsStageOutput(StageOutput):
        """Assign VRF to Ports Stage Output."""

        assigned_ports: list[str]
        unassigned_ports: list[str]
        already_assigned_ports: list[str]
        removed_vrf_ids: list[str]
        overlay_assignments_created: int
        overlay_assignments_removed: int
        overlay_reconciliation_changed: bool = False

    @stage_executor("assign_vrf_to_ports")
    async def assign_vrf_to_ports_stage(
        self, stage_input: AssignVrfToPortsStageInput
    ) -> AssignVrfToPortsStageOutput:
        """Assign VRF to Ports Stage."""
        interfaces_output = await workflow.execute_activity(
            get_device_interfaces,
            GetDeviceInterfacesInput(
                device_id=stage_input.device_id,
                interface_names=stage_input.port_names,
            ),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        all_interfaces_output = await workflow.execute_activity(
            get_device_interfaces,
            GetDeviceInterfacesInput(device_id=stage_input.device_id),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        tasks = []
        assigned_ports = []
        unassigned_ports = []
        already_assigned_ports = []
        previous_vrf_ids: set[str] = set()
        for interface in interfaces_output.interfaces:
            if interface.vrf_id == stage_input.vrf_id:
                already_assigned_ports.append(interface.name)
                continue
            if interface.vrf_id is not None:
                previous_vrf_ids.add(interface.vrf_id)
            task = workflow.execute_activity(
                assign_vrf_to_interface,
                AssignVrfToInterfaceInput(
                    interface_id=interface.id,
                    vrf_id=stage_input.vrf_id,
                ),
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            )
            tasks.append(task)
            if stage_input.vrf_id is None:
                unassigned_ports.append(interface.name)
            else:
                assigned_ports.append(interface.name)

        await asyncio.gather(*tasks)

        overlay_assignments = await workflow.execute_activity(
            reconcile_spx_overlay_assignments,
            ReconcileSpXOverlayAssignmentsInput(
                overlay_id=stage_input.overlay_id,
                site=stage_input.site,
                device_id=stage_input.device_id,
                interface_ids=[interface.id for interface in interfaces_output.interfaces],
                device_interface_ids=[
                    interface.id for interface in all_interfaces_output.interfaces
                ],
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        removed_device_vrfs = await workflow.execute_activity(
            remove_unmapped_device_vrfs,
            RemoveUnmappedDeviceVrfsInput(
                device_id=stage_input.device_id,
                vrf_ids=sorted(previous_vrf_ids),
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        if stage_input.vrf_id is None:
            change_display = f"Ports unassigned: {', '.join(unassigned_ports)}"
        else:
            change_display = (
                f"VRF {stage_input.vrf_name} assigned to ports: {', '.join(assigned_ports)}"
            )

        return self.AssignVrfToPortsStageOutput(
            assigned_ports=assigned_ports,
            unassigned_ports=unassigned_ports,
            already_assigned_ports=already_assigned_ports,
            removed_vrf_ids=removed_device_vrfs.removed_vrf_ids,
            overlay_assignments_created=overlay_assignments.created,
            overlay_assignments_removed=overlay_assignments.removed,
            overlay_reconciliation_changed=overlay_assignments.reconciliation_changed,
            display=(
                f"{change_display}\n"
                f"Ports already assigned: {', '.join(already_assigned_ports)}\n"
                f"Overlay assignments created: {overlay_assignments.created}; "
                f"stale assignments removed: {overlay_assignments.removed}\n"
                "Unused device/VRF associations removed: "
                f"{', '.join(removed_device_vrfs.removed_vrf_ids)}"
            ),
        )

    @run_nv_config_manager_workflow
    async def run(  # type: ignore[override, ty:invalid-method-override]  # pyright: ignore
        self, workflow_input: SpXOverlayAssignmentInput
    ) -> SpXOverlayAssignmentWorkflowOutput:
        """Execute the VPC Assignment workflow."""
        self.set_input(workflow_input)

        device_vrf_output = await self.get_device_and_vrf(
            self.GetDeviceAndVrfStageInput(
                overlay_id=workflow_input.overlay_id,
                device=workflow_input.device,
                site=workflow_input.site,
                namespace_tag=workflow_input.namespace_tag,
            )
        )
        DeviceMixin.attach_device_search_attributes(device_vrf_output.device)

        device_output = await self.assign_vrf_to_device_stage(
            self.AssignVrfToDeviceStageInput(
                device_id=device_vrf_output.device.id,
                vrf_id=device_vrf_output.vrf.id if device_vrf_output.vrf else None,
                vrf_name=device_vrf_output.vrf.name if device_vrf_output.vrf else None,
            )
        )

        ports_output = await self.assign_vrf_to_ports_stage(
            self.AssignVrfToPortsStageInput(
                device_id=device_vrf_output.device.id,
                overlay_id=workflow_input.overlay_id,
                site=workflow_input.site,
                vrf_id=device_vrf_output.vrf.id if device_vrf_output.vrf else None,
                vrf_name=device_vrf_output.vrf.name if device_vrf_output.vrf else None,
                port_names=workflow_input.port_names,
            )
        )

        await self.archive_results()
        return SpXOverlayAssignmentWorkflowOutput(
            assigned_ports=ports_output.assigned_ports,
            unassigned_ports=ports_output.unassigned_ports,
            vrf_assigned=not device_output.already_assigned,
            removed_vrf_ids=ports_output.removed_vrf_ids,
            overlay_assignments_created=ports_output.overlay_assignments_created,
            overlay_assignments_removed=ports_output.overlay_assignments_removed,
            overlay_reconciliation_changed=ports_output.overlay_reconciliation_changed,
            vrf=(
                DeviceVRF(
                    vrf_id=device_vrf_output.vrf.id,
                    vrf_name=device_vrf_output.vrf.name,
                )
                if device_vrf_output.vrf
                else None
            ),
        )


class SpXOverlayTenantChangeInput(BaseModel):
    """SpX Overlay Tenant Change Workflow Input Definition."""

    overlay_id: str | None = Field(
        default=None,
        title="Overlay ID",
        description=(
            "Identifier of the SpX overlay to assign and deploy tenant configuration for. "
            "Omit the overlay_id property or explicitly set it to null to remove the selected "
            "ports' current SpX assignment."
        ),
    )
    device_id: DeviceReference = Field(
        title="Device ID", description="Identifier of the target network device."
    )
    port_names: list[str] = Field(
        min_length=1, description="Names of the device interfaces to assign to the overlay."
    )
    site: LocationReference = Field(description="Site containing the target network device.")
    namespace_tag: str = Field(
        default=NAMESPACE_TAG, description="Tag identifying the namespace used for allocation."
    )


class SpXOverlayTenantChangeWorkflowOutput(BaseModel):
    """SpX Overlay Tenant Change Workflow Output Definition."""

    assigned_ports: list[str]
    unassigned_ports: list[str]
    vrf_assigned: bool
    removed_vrf_ids: list[str]
    overlay_assignments_created: int
    overlay_assignments_removed: int
    vrf: DeviceVRF | None
    device_deployed: str | None


@workflow.defn
class SpXOverlayTenantChangeWorkflow(WorkflowMetadataMixin, StageMixin, DeviceMixin, ArchiveMixin):
    """SpX Overlay tenant change workflow for assigning overlays and deploying tenant config."""

    workflow_name = "SpX Overlay Tenant Change"
    workflow_description = (
        "Change or remove a SpX Overlay assignment and deploy tenant configuration"
    )
    workflow_input_class = SpXOverlayTenantChangeInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/ngc/spx_overlay_tenant_change"
    workflow_namespace = "ngc"

    def __init__(self) -> None:
        """Initialize workflow."""
        StageMixin.__init__(self)
        self.define_stage(
            name="get_device",
            description="Get device information from the DCIM",
            requires_approval=False,
            depends_on=[],
        )

        self.define_stage(
            name="assign_spx_overlay",
            description="Assign VPC to device and ports",
            requires_approval=False,
            depends_on=["get_device"],
        )

        self.define_stage(
            name="determine_deployment_action",
            description="Determine whether the tenant change needs deployment",
            requires_approval=False,
            depends_on=["assign_spx_overlay"],
        )

        self.define_stage(
            name="render_tenant_config",
            description="Render tenant configuration",
            requires_approval=False,
            depends_on=["determine_deployment_action"],
        )

        self.define_stage(
            name="wait_for_render",
            description="Wait for tenant render to be updated",
            requires_approval=False,
            depends_on=["render_tenant_config"],
        )

        self.define_stage(
            name="deploy",
            description="Deploy tenant configuration to device",
            requires_approval=False,
            depends_on=["wait_for_render"],
        )

    class GetDeviceStageInput(StageInput):
        """Get Device Stage Input."""

        device_id: str

    class GetDeviceStageOutput(StageOutput):
        """Get Device Stage Output."""

        device: NetworkDeviceData

    @stage_executor("get_device")
    async def get_device_stage(self, stage_input: GetDeviceStageInput) -> GetDeviceStageOutput:
        """Get device information from the configured DCIM."""
        device_output = await workflow.execute_activity(
            get_network_device,
            GetNetworkDeviceInput(device_id=stage_input.device_id),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        return self.GetDeviceStageOutput(
            device=device_output.device,
            display=f"Retrieved device: {device_output.device.name}",
        )

    class AssignSpXOverlayStageInput(StageInput):
        """Assign VPC Stage Input."""

        overlay_id: str | None
        device: NetworkDeviceData
        port_names: list[str]
        site: str
        namespace_tag: str

    class AssignSpXOverlayStageOutput(StageOutput):
        """Assign SpX Overlay Stage Output."""

        assigned_ports: list[str]
        unassigned_ports: list[str]
        vrf_assigned: bool
        removed_vrf_ids: list[str]
        overlay_assignments_created: int
        overlay_assignments_removed: int
        overlay_reconciliation_changed: bool = False
        vrf: DeviceVRF | None
        overlay_name: str | None
        vxlan_name: str | None
        error: str | None = None

    @stage_executor("assign_spx_overlay")
    async def assign_spx_overlay_stage(
        self, stage_input: AssignSpXOverlayStageInput
    ) -> AssignSpXOverlayStageOutput:
        """Assign SpX Overlay to device and ports."""
        try:
            assignment_handle = await workflow.start_child_workflow(
                SpXOverlayAssignmentWorkflow.run,
                SpXOverlayAssignmentInput(
                    overlay_id=stage_input.overlay_id,
                    device=stage_input.device,
                    port_names=stage_input.port_names,
                    site=stage_input.site,
                    namespace_tag=stage_input.namespace_tag,
                ),
                run_timeout=timedelta(minutes=10),
            )
            self.append_child_workflow("assign_spx_overlay", assignment_handle.id)
            self.set_stage_output(
                "assign_spx_overlay",
                StageOutput(
                    display=(
                        f"Assigning via workflow "
                        f"[{assignment_handle.id}](/workflows/{assignment_handle.id})"
                    )
                ),
            )
        except Exception as exc:
            raise ApplicationError(str(exc)) from exc

        try:
            result = await assignment_handle
        except ChildWorkflowError as exc:
            error = str(exc.cause or exc)
            return self.AssignSpXOverlayStageOutput(
                assigned_ports=[],
                unassigned_ports=[],
                vrf_assigned=False,
                removed_vrf_ids=[],
                overlay_assignments_created=0,
                overlay_assignments_removed=0,
                overlay_reconciliation_changed=False,
                vrf=None,
                overlay_name=stage_input.overlay_id,
                vxlan_name=None,
                error=error,
                display=(
                    "**Assignment failed, check workflow "
                    f"[{assignment_handle.id}](/workflows/{assignment_handle.id}) "
                    "for details.**"
                ),
            )

        overlay_name = stage_input.overlay_id
        vxlan_name = result.vrf.vrf_name if result.vrf else None

        if result.vrf:
            vrf_status = "assigned" if result.vrf_assigned else "already assigned"
            assigned_ports = ", ".join(result.assigned_ports) or "None"
            change_details = (
                f"- **Overlay:** {overlay_name}\n"
                f"- **L3 VXLAN:** {vxlan_name}\n"
                f"- **VRF:** {result.vrf.vrf_name} ({vrf_status})\n"
                f"- **Ports assigned ({len(result.assigned_ports)}):** {assigned_ports}"
            )
        else:
            unassigned_ports = ", ".join(result.unassigned_ports) or "None"
            change_details = (
                "- **Overlay:** None (remove assignment)\n"
                f"- **Ports unassigned ({len(result.unassigned_ports)}):** {unassigned_ports}"
            )
        removed_vrfs = ", ".join(result.removed_vrf_ids) or "None"
        display = (
            f"{change_details}\n"
            f"- **Device/VRF associations removed:** {removed_vrfs}\n\n"
            f"Changing via workflow [{assignment_handle.id}]"
            f"(/workflows/{assignment_handle.id})"
        )
        return self.AssignSpXOverlayStageOutput(
            assigned_ports=result.assigned_ports,
            unassigned_ports=result.unassigned_ports,
            vrf_assigned=result.vrf_assigned,
            removed_vrf_ids=result.removed_vrf_ids,
            overlay_assignments_created=result.overlay_assignments_created,
            overlay_assignments_removed=result.overlay_assignments_removed,
            overlay_reconciliation_changed=result.overlay_reconciliation_changed,
            vrf=result.vrf,
            overlay_name=overlay_name,
            vxlan_name=vxlan_name,
            display=display,
        )

    class DetermineDeploymentActionStageInput(StageInput):
        """Determine Deployment Action Stage Input."""

        device_id: str
        assignment_changed: bool

    class DetermineDeploymentActionStageOutput(StageOutput):
        """Determine Deployment Action Stage Output."""

        deploy_required: bool
        use_latest_render: bool = False

    @stage_executor("determine_deployment_action")
    async def determine_deployment_action_stage(
        self, stage_input: DetermineDeploymentActionStageInput
    ) -> DetermineDeploymentActionStageOutput:
        """Determine whether a tenant deployment is required."""
        if stage_input.assignment_changed:
            return self.DetermineDeploymentActionStageOutput(
                deploy_required=True,
                display="DCIM assignment changed; tenant render and deploy are required.",
            )

        has_pending_deployment = await workflow.execute_activity(
            check_recorded_config_drift,
            CheckRecordedConfigDriftInput(device_id=stage_input.device_id),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        if has_pending_deployment:
            return self.DetermineDeploymentActionStageOutput(
                deploy_required=True,
                use_latest_render=True,
                display=(
                    "DCIM assignment was already complete, but the device has a pending "
                    "deployment; deploying the latest rendered tenant configuration."
                ),
            )

        return self.DetermineDeploymentActionStageOutput(
            deploy_required=False,
            display="DCIM assignment is already complete and no deployment is pending.",
        )

    class RenderStageInput(StageInput):
        """Render Stage Input."""

        device: NetworkDeviceData

    class RenderStageOutput(StageOutput):
        """Render Stage Output."""

        tenant_config_commit_id: str
        intended_config_commit_id: str

    @stage_executor("render_tenant_config")
    async def render_stage(self, stage_input: RenderStageInput) -> RenderStageOutput:
        """Render tenant configuration."""
        result = await workflow.execute_activity(
            execute_render,
            ExecuteRenderInput(
                device_id=stage_input.device.id,
                workflow_id=workflow.info().workflow_id,
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        # Resolve both commit IDs from the same post-render Config Store snapshot.
        tenant_config_file = stage_input.device.tenant_config_file
        tenant_config_commit_id = result.get_commit(tenant_config_file)
        intended_config_commit_id = result.get_commit(stage_input.device.intended_config_file)

        if tenant_config_commit_id is None or intended_config_commit_id is None:
            raise ApplicationError("Failed to resolve rendered configuration commit IDs")

        display_message = f"Rendered tenant configuration (config ID: {tenant_config_commit_id})"

        return self.RenderStageOutput(
            tenant_config_commit_id=tenant_config_commit_id,
            intended_config_commit_id=intended_config_commit_id,
            display=display_message,
        )

    class WaitForRenderStageInput(StageInput):
        """Wait For Render Stage Input."""

        device: NetworkDeviceData
        config_id: str

    class WaitForRenderStageOutput(StageOutput):
        """Wait For Render Stage Output."""

        config_id: str | None = None

    @stage_executor("wait_for_render")
    async def wait_for_render_stage(
        self, stage_input: WaitForRenderStageInput
    ) -> WaitForRenderStageOutput:
        """Wait for tenant render to be updated with expected changes."""
        result = await workflow.execute_activity(
            wait_for_tenant_render,
            WaitForTenantRenderInput(
                device=stage_input.device,
                config_id=stage_input.config_id,
            ),
            start_to_close_timeout=timedelta(minutes=15),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        display_message = "Tenant render available"
        if result.config_id:
            display_message += f" (config ID: {result.config_id})"

        return self.WaitForRenderStageOutput(
            config_id=result.config_id,
            display=display_message,
        )

    class DeployStageInput(StageInput):
        """Deploy Stage Input."""

        device: NetworkDeviceData
        tenant_config_commit_id: str | None = None
        intended_config_commit_id: str | None = None
        use_full_intended_config: bool = False

    class DeployStageOutput(StageOutput):
        """Deploy Stage Output."""

        device_id: str
        error: str | None = None

    @stage_executor("deploy")
    async def deploy_stage(self, stage_input: DeployStageInput) -> DeployStageOutput:
        """Deploy tenant configuration to device."""
        if (stage_input.tenant_config_commit_id is None) != (
            stage_input.intended_config_commit_id is None
        ):
            raise ApplicationError(
                "tenant_config_commit_id and intended_config_commit_id must both be supplied "
                "or both be omitted"
            )

        if (
            stage_input.tenant_config_commit_id is None
            and stage_input.intended_config_commit_id is None
        ):
            tenant_deploy_input = TenantDeployInput(
                device=stage_input.device,
                use_full_intended_config=stage_input.use_full_intended_config,
            )
        else:
            tenant_deploy_input = TenantDeployInput(
                device=stage_input.device,
                tenant_config_commit_id=stage_input.tenant_config_commit_id,
                intended_config_commit_id=stage_input.intended_config_commit_id,
                use_full_intended_config=stage_input.use_full_intended_config,
            )

        try:
            deploy_handle = await workflow.start_child_workflow(
                TenantDeployWorkflow.run,
                tenant_deploy_input,
                run_timeout=timedelta(minutes=10),
            )
            self.append_child_workflow("deploy", deploy_handle.id)
            self.set_stage_output(
                "deploy",
                StageOutput(
                    display=(
                        f"Deploying configuration via workflow "
                        f"[{deploy_handle.id}](/workflows/{deploy_handle.id})"
                    )
                ),
            )
        except Exception as exc:
            raise ApplicationError(str(exc)) from exc

        try:
            await deploy_handle
        except ChildWorkflowError as exc:
            error = str(exc.cause or exc)
            return self.DeployStageOutput(
                device_id=stage_input.device.id,
                error=error,
                display=(
                    "**Configuration deployment failed, check workflow "
                    f"[{deploy_handle.id}](/workflows/{deploy_handle.id}) "
                    "for details.**"
                ),
            )

        return self.DeployStageOutput(
            device_id=stage_input.device.id,
            display=(
                f"Configuration deployed via workflow "
                f"[{deploy_handle.id}](/workflows/{deploy_handle.id})"
            ),
        )

    @run_nv_config_manager_workflow
    async def run(  # type: ignore[override, ty:invalid-method-override]  # pyright: ignore
        self, workflow_input: SpXOverlayTenantChangeInput
    ) -> SpXOverlayTenantChangeWorkflowOutput:
        """Execute the VPC Tenant Change workflow."""
        self.set_input(workflow_input)

        device_output = await self.get_device_stage(
            self.GetDeviceStageInput(device_id=workflow_input.device_id)
        )
        DeviceMixin.attach_device_search_attributes(device_output.device)
        assign_output = await self.assign_spx_overlay_stage(
            self.AssignSpXOverlayStageInput(
                overlay_id=workflow_input.overlay_id,
                device=device_output.device,
                port_names=workflow_input.port_names,
                site=workflow_input.site,
                namespace_tag=workflow_input.namespace_tag,
            )
        )
        if assign_output.error:
            for stage_name in (
                "determine_deployment_action",
                "render_tenant_config",
                "wait_for_render",
                "deploy",
            ):
                self.set_stage_state(
                    stage_name,
                    StateEnum.UNREACHABLE,
                    cascade_unreachable=False,
                )
            raise ApplicationError(
                f"SpX Overlay Assignment child workflow failed: {assign_output.error}",
                non_retryable=True,
            )

        deployment_action_output = await self.determine_deployment_action_stage(
            self.DetermineDeploymentActionStageInput(
                device_id=device_output.device.id,
                assignment_changed=bool(
                    assign_output.assigned_ports
                    or assign_output.unassigned_ports
                    or assign_output.vrf_assigned
                    or assign_output.removed_vrf_ids
                    or assign_output.overlay_assignments_created
                    or assign_output.overlay_assignments_removed
                    or assign_output.overlay_reconciliation_changed
                ),
            )
        )
        use_full_intended_config = deployment_action_output.use_latest_render or bool(
            assign_output.unassigned_ports
            or assign_output.removed_vrf_ids
            or assign_output.overlay_assignments_removed
        )

        if not deployment_action_output.deploy_required:
            self.set_stage_state("render_tenant_config", StateEnum.UNREACHABLE)
            self.set_stage_state("wait_for_render", StateEnum.UNREACHABLE)
            self.set_stage_state("deploy", StateEnum.UNREACHABLE)
            assigned_ports = []
            unassigned_ports = []
            vrf_assigned = False
            removed_vrf_ids = []
            overlay_assignments_created = 0
            overlay_assignments_removed = 0
            vrf = None
            device_deployed = None
        elif deployment_action_output.use_latest_render:
            self.set_stage_state(
                "render_tenant_config", StateEnum.UNREACHABLE, cascade_unreachable=False
            )
            self.set_stage_state(
                "wait_for_render", StateEnum.UNREACHABLE, cascade_unreachable=False
            )

            deploy_output = await self.deploy_stage(
                self.DeployStageInput(
                    device=device_output.device,
                    use_full_intended_config=use_full_intended_config,
                )
            )
            if deploy_output.error:
                raise ApplicationError(
                    f"Tenant Deploy child workflow failed: {deploy_output.error}",
                    non_retryable=True,
                )
            device_deployed = deploy_output.device_id
            assigned_ports = assign_output.assigned_ports
            unassigned_ports = assign_output.unassigned_ports
            vrf_assigned = assign_output.vrf_assigned
            removed_vrf_ids = assign_output.removed_vrf_ids
            overlay_assignments_created = assign_output.overlay_assignments_created
            overlay_assignments_removed = assign_output.overlay_assignments_removed
            vrf = assign_output.vrf
        else:
            render_output = await self.render_stage(
                self.RenderStageInput(
                    device=device_output.device,
                )
            )

            await self.wait_for_render_stage(
                self.WaitForRenderStageInput(
                    device=device_output.device,
                    config_id=render_output.tenant_config_commit_id,
                )
            )

            deploy_output = await self.deploy_stage(
                self.DeployStageInput(
                    device=device_output.device,
                    tenant_config_commit_id=render_output.tenant_config_commit_id,
                    intended_config_commit_id=render_output.intended_config_commit_id,
                    use_full_intended_config=use_full_intended_config,
                )
            )
            if deploy_output.error:
                raise ApplicationError(
                    f"Tenant Deploy child workflow failed: {deploy_output.error}",
                    non_retryable=True,
                )
            device_deployed = deploy_output.device_id
            assigned_ports = assign_output.assigned_ports
            unassigned_ports = assign_output.unassigned_ports
            vrf_assigned = assign_output.vrf_assigned
            removed_vrf_ids = assign_output.removed_vrf_ids
            overlay_assignments_created = assign_output.overlay_assignments_created
            overlay_assignments_removed = assign_output.overlay_assignments_removed
            vrf = assign_output.vrf

        await self.archive_results()
        return SpXOverlayTenantChangeWorkflowOutput(
            assigned_ports=assigned_ports,
            unassigned_ports=unassigned_ports,
            vrf_assigned=vrf_assigned,
            removed_vrf_ids=removed_vrf_ids,
            overlay_assignments_created=overlay_assignments_created,
            overlay_assignments_removed=overlay_assignments_removed,
            vrf=vrf,
            device_deployed=device_deployed,
        )
