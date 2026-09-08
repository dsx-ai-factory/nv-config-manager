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
"""InfiniBand PKey Member Delete Workflow."""

from datetime import timedelta

from pydantic import BaseModel, Field, field_validator, model_validator
from temporalio import workflow

from nv_config_manager.temporal.common.decorators.workflow import run_nv_config_manager_workflow
from nv_config_manager.temporal.common.lock import WorkflowLockSpec
from nv_config_manager.temporal.common.mixins.metadata import WorkflowMetadataMixin
from nv_config_manager.temporal.common.mixins.stage import (
    StageInput,
    StageMixin,
    StageOutput,
    stage_executor,
)
from nv_config_manager.temporal.ngc.workflows._ib_pkey_lock import UFMHostLockMixin

with workflow.unsafe.imports_passed_through():
    from nv_config_manager.temporal.common.mixins.archive import ArchiveMixin
    from nv_config_manager.temporal.ngc.activities.ib_dcim import (
        CleanupEmptyPartitionInput,
        CleanupEmptyPartitionOutput,
        InterfaceRef,
        RemovePKeyAssignmentsInput,
        RemovePKeyAssignmentsOutput,
        ResolvedInterface,
        cleanup_empty_pkey_partition,
        remove_pkey_assignments,
    )
    from nv_config_manager.temporal.ngc.activities.ib_pkey import (
        RemoveGuidsInput,
        RemoveGuidsOutput,
        VerifyPKeyMembersAbsentInput,
        VerifyPKeyMembersAbsentOutput,
        remove_guids_from_pkey,
        verify_pkey_members_absent,
    )
    from nv_config_manager.temporal.ngc.workflows._ib_pkey_helpers import (
        DEFAULT_ACTIVITY_RETRY_POLICY,
        call_resolve_ib_context,
        resolve_members,
        validate_interfaces_xor_guids,
        validate_pkey_format,
    )


class IBPKeyMemberDeleteInput(BaseModel):
    """InfiniBand PKey Member Delete Workflow Input.

    Provide either ``interfaces`` (resolved to GUIDs via the DCIM) or
    ``guids`` directly, but not both. Site and Overlay are resolved
    server-side from ``host`` and ``pkey``.
    """

    host: str = Field(description="Hostname of the UFM server managing the InfiniBand fabric.")
    pkey: str = Field(description="Partition key whose members will be removed.")
    interfaces: list[InterfaceRef] = Field(
        default=[], description="DCIM interfaces to resolve to InfiniBand port GUIDs."
    )
    guids: list[str] = Field(
        default=[], description="InfiniBand port GUIDs to remove directly from the partition."
    )

    @field_validator("pkey")
    @classmethod
    def _validate_pkey(cls, v: str) -> str:
        return validate_pkey_format(v)

    @model_validator(mode="after")
    def _validate(self) -> "IBPKeyMemberDeleteInput":
        validate_interfaces_xor_guids(self.interfaces, self.guids)
        return self


class IBPKeyMemberDeleteOutput(BaseModel):
    """InfiniBand PKey Member Delete Workflow Output."""

    pkey: str
    overlay_id: str
    overlay_name: str
    members_removed: int
    verified: bool
    assignment_ids_removed: list[str]
    interface_ids_not_assigned: list[str]
    partition_empty: bool
    pkey_deleted: bool
    overlay_deleted: bool


@workflow.defn
class IBPKeyMemberDeleteWorkflow(UFMHostLockMixin, WorkflowMetadataMixin, StageMixin, ArchiveMixin):
    """Remove device interface GUIDs from an existing IB PKey partition."""

    workflow_name = "InfiniBand PKey Member Delete"
    workflow_description = "Remove device interfaces from an existing InfiniBand PKey partition"
    workflow_input_class = IBPKeyMemberDeleteInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/ngc/ib_pkey_member_delete"
    workflow_namespace = "ngc"
    workflow_lock = WorkflowLockSpec(key_fields=["host", "pkey"])

    def __init__(self) -> None:
        """Initialize workflow with five stages."""
        StageMixin.__init__(self)
        self.define_stage(
            name="resolve_context",
            description="Resolve site, overlay, and canonical pkey from the DCIM",
            requires_approval=False,
            depends_on=[],
        )
        self.define_stage(
            name="resolve_guids",
            description="Resolve IB GUIDs for interfaces from the DCIM",
            requires_approval=False,
            depends_on=["resolve_context"],
        )
        self.define_stage(
            name="remove_members",
            description="Remove GUIDs from PKey partition on UFM",
            requires_approval=False,
            depends_on=["resolve_guids"],
        )
        self.define_stage(
            name="verify_removed",
            description="Verify GUIDs are no longer present in PKey on UFM",
            requires_approval=False,
            depends_on=["remove_members"],
        )
        self.define_stage(
            name="remove_assignments",
            description="Delete OverlayAssignment records in the DCIM",
            requires_approval=False,
            depends_on=["verify_removed"],
        )
        self.define_stage(
            name="cleanup_partition",
            description="Delete the DCIM PKey/Overlay if the partition is now empty",
            requires_approval=False,
            depends_on=["remove_assignments"],
        )

    # ------------------------------------------------------------------
    # Stage 0: Resolve site / overlay / canonical pkey from the DCIM
    # ------------------------------------------------------------------

    class ResolveContextStageInput(StageInput):
        """Resolve Context Stage Input."""

        host: str
        pkey: str

    class ResolveContextStageOutput(StageOutput):
        """Resolve Context Stage Output."""

        host: str
        site: str
        pkey: str
        overlay_id: str
        pkey_id: str
        ufm_device_id: str
        location_id: str
        overlay_name: str

    @stage_executor("resolve_context")
    async def resolve_context(
        self, stage_input: ResolveContextStageInput
    ) -> ResolveContextStageOutput:
        """Resolve site/overlay from the DCIM and canonicalize pkey."""
        resolved = await call_resolve_ib_context(stage_input.host, stage_input.pkey)

        return self.ResolveContextStageOutput(
            host=stage_input.host,
            site=resolved.location_name,
            pkey=resolved.pkey,
            overlay_id=resolved.overlay_id,
            pkey_id=resolved.pkey_id,
            ufm_device_id=resolved.ufm_device_id,
            location_id=resolved.location_id,
            overlay_name=resolved.overlay_name,
            display=(
                f"Context: host={stage_input.host} site={resolved.location_name} "
                f"pkey={resolved.pkey} overlay={resolved.overlay_name}"
            ),
        )

    # ------------------------------------------------------------------
    # Stage 1: Resolve GUIDs from the DCIM
    # ------------------------------------------------------------------

    class ResolveGuidsStageInput(StageInput):
        """Resolve GUIDs Stage Input."""

        interfaces: list[InterfaceRef] = Field(default_factory=list)
        guids: list[str] = Field(default_factory=list)

    class ResolveGuidsStageOutput(StageOutput):
        """Resolve GUIDs Stage Output."""

        resolved: list[ResolvedInterface]

    @stage_executor("resolve_guids")
    async def resolve_guids(self, stage_input: ResolveGuidsStageInput) -> ResolveGuidsStageOutput:
        """Resolve members from interfaces or GUIDs into DCIM interface records."""
        resolved, display = await resolve_members(stage_input.interfaces, stage_input.guids)
        return self.ResolveGuidsStageOutput(resolved=resolved, display=display)

    # ------------------------------------------------------------------
    # Stage 2: Remove GUIDs from PKey on UFM
    # ------------------------------------------------------------------

    class RemoveMembersStageInput(StageInput):
        """Remove Members Stage Input."""

        host: str
        site: str | None
        pkey: str
        guids: list[str]

    class RemoveMembersStageOutput(StageOutput):
        """Remove Members Stage Output."""

        pkey: str
        guids_removed: list[str]

    @stage_executor("remove_members")
    async def remove_members(
        self, stage_input: RemoveMembersStageInput
    ) -> RemoveMembersStageOutput:
        """Remove the resolved GUIDs from the PKey partition on UFM."""
        result: RemoveGuidsOutput = await workflow.execute_activity(
            remove_guids_from_pkey,
            RemoveGuidsInput(
                host=stage_input.host,
                site=stage_input.site,
                pkey=stage_input.pkey,
                guids=stage_input.guids,
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        return self.RemoveMembersStageOutput(
            pkey=result.pkey,
            guids_removed=result.guids_removed,
            display=result.display,
        )

    # ------------------------------------------------------------------
    # Stage 3: Verify GUIDs are absent from UFM
    # ------------------------------------------------------------------

    class VerifyRemovedStageInput(StageInput):
        """Verify Removed Stage Input."""

        host: str
        site: str | None
        pkey: str
        forbidden_guids: list[str]

    class VerifyRemovedStageOutput(StageOutput):
        """Verify Removed Stage Output."""

        pkey: str
        verified: bool
        partition_empty: bool

    @stage_executor("verify_removed")
    async def verify_removed(
        self, stage_input: VerifyRemovedStageInput
    ) -> VerifyRemovedStageOutput:
        """Verify that none of the removed GUIDs remain in the PKey on UFM."""
        result: VerifyPKeyMembersAbsentOutput = await workflow.execute_activity(
            verify_pkey_members_absent,
            VerifyPKeyMembersAbsentInput(
                host=stage_input.host,
                site=stage_input.site,
                pkey=stage_input.pkey,
                forbidden_guids=stage_input.forbidden_guids,
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        if not result.partition_exists:
            partition_empty = True
        elif result.remaining_member_count is None:
            partition_empty = False
        else:
            partition_empty = result.remaining_member_count == 0
        return self.VerifyRemovedStageOutput(
            pkey=result.pkey,
            verified=result.verified,
            partition_empty=partition_empty,
            display=result.display,
        )

    # ------------------------------------------------------------------
    # Stage 4: Remove overlay assignments in the DCIM
    # ------------------------------------------------------------------

    class RemoveAssignmentsStageInput(StageInput):
        """Remove Assignments Stage Input."""

        overlay_id: str
        interface_ids: list[str]

    class RemoveAssignmentsStageOutput(StageOutput):
        """Remove Assignments Stage Output."""

        assignment_ids_removed: list[str]
        interface_ids_not_assigned: list[str]

    @stage_executor("remove_assignments")
    async def remove_assignments(
        self, stage_input: RemoveAssignmentsStageInput
    ) -> RemoveAssignmentsStageOutput:
        """Delete overlay assignment records in the DCIM for each interface."""
        result: RemovePKeyAssignmentsOutput = await workflow.execute_activity(
            remove_pkey_assignments,
            RemovePKeyAssignmentsInput(
                overlay_id=stage_input.overlay_id,
                interface_ids=stage_input.interface_ids,
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        return self.RemoveAssignmentsStageOutput(
            assignment_ids_removed=result.assignment_ids_removed,
            interface_ids_not_assigned=result.interface_ids_not_assigned,
            display=result.display,
        )

    # ------------------------------------------------------------------
    # Stage 5: Reconcile the DCIM when the partition is now empty
    # ------------------------------------------------------------------

    class CleanupPartitionStageInput(StageInput):
        """Cleanup Partition Stage Input."""

        overlay_id: str
        overlay_name: str
        pkey_id: str
        pkey: str
        ufm_partition_empty: bool

    class CleanupPartitionStageOutput(StageOutput):
        """Cleanup Partition Stage Output."""

        partition_empty: bool
        pkey_deleted: bool
        overlay_deleted: bool

    @stage_executor("cleanup_partition")
    async def cleanup_partition(
        self, stage_input: CleanupPartitionStageInput
    ) -> CleanupPartitionStageOutput:
        """Delete the DCIM PKey/overlay when the partition has no members left.

        Mirrors UFM, which auto-removes a PKey once its last member leaves.
        """
        result: CleanupEmptyPartitionOutput = await workflow.execute_activity(
            cleanup_empty_pkey_partition,
            CleanupEmptyPartitionInput(
                overlay_id=stage_input.overlay_id,
                overlay_name=stage_input.overlay_name,
                pkey_id=stage_input.pkey_id,
                pkey=stage_input.pkey,
                ufm_partition_empty=stage_input.ufm_partition_empty,
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        return self.CleanupPartitionStageOutput(
            partition_empty=result.partition_empty,
            pkey_deleted=result.pkey_deleted,
            overlay_deleted=result.overlay_deleted,
            display=result.display,
        )

    # ------------------------------------------------------------------
    # Main run
    # ------------------------------------------------------------------

    @run_nv_config_manager_workflow
    async def run(  # type: ignore[override, ty:invalid-method-override]  # pyright: ignore
        self, workflow_input: IBPKeyMemberDeleteInput
    ) -> IBPKeyMemberDeleteOutput:
        """Execute the IB PKey Member Delete workflow."""
        self.set_input(workflow_input)

        context = await self.resolve_context(
            self.ResolveContextStageInput(
                host=workflow_input.host,
                pkey=workflow_input.pkey,
            )
        )

        resolve_output = await self.resolve_guids(
            self.ResolveGuidsStageInput(
                interfaces=workflow_input.interfaces,
                guids=workflow_input.guids,
            )
        )

        guids = [r.guid for r in resolve_output.resolved]
        interface_ids = [r.interface_id for r in resolve_output.resolved]

        remove_output = await self.remove_members(
            self.RemoveMembersStageInput(
                host=context.host,
                site=context.site,
                pkey=context.pkey,
                guids=guids,
            )
        )

        verify_output = await self.verify_removed(
            self.VerifyRemovedStageInput(
                host=context.host,
                site=context.site,
                pkey=context.pkey,
                forbidden_guids=remove_output.guids_removed,
            )
        )

        assignments_output = await self.remove_assignments(
            self.RemoveAssignmentsStageInput(
                overlay_id=context.overlay_id,
                interface_ids=interface_ids,
            )
        )

        cleanup_output = await self.cleanup_partition(
            self.CleanupPartitionStageInput(
                overlay_id=context.overlay_id,
                overlay_name=context.overlay_name,
                pkey_id=context.pkey_id,
                pkey=context.pkey,
                ufm_partition_empty=verify_output.partition_empty,
            )
        )

        await self.archive_results()
        return IBPKeyMemberDeleteOutput(
            pkey=verify_output.pkey,
            overlay_id=context.overlay_id,
            overlay_name=context.overlay_name,
            members_removed=len(remove_output.guids_removed),
            verified=verify_output.verified,
            assignment_ids_removed=assignments_output.assignment_ids_removed,
            interface_ids_not_assigned=assignments_output.interface_ids_not_assigned,
            partition_empty=cleanup_output.partition_empty,
            pkey_deleted=cleanup_output.pkey_deleted,
            overlay_deleted=cleanup_output.overlay_deleted,
        )
