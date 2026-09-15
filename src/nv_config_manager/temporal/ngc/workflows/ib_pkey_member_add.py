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
"""InfiniBand PKey Member Add Workflow."""

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
        InterfaceRef,
        RecordPKeyAssignmentsInput,
        RecordPKeyAssignmentsOutput,
        ResolvedInterface,
        record_pkey_assignments,
    )
    from nv_config_manager.temporal.ngc.activities.ib_pkey import (
        AddGuidsInput,
        AddGuidsOutput,
        VerifyPKeyMembersInput,
        VerifyPKeyMembersOutput,
        add_guids_to_pkey,
        verify_pkey_members,
    )
    from nv_config_manager.temporal.ngc.workflows._ib_pkey_helpers import (
        DEFAULT_ACTIVITY_RETRY_POLICY,
        DEFAULT_MEMBERSHIP_TYPE,
        call_resolve_ib_context_for_add,
        normalize_guid_membership_list,
        normalize_membership_type,
        resolve_members,
        validate_guid_memberships,
        validate_interfaces_xor_guids,
        validate_pkey_format,
    )


class IBPKeyMemberAddInput(BaseModel):
    """InfiniBand PKey Member Add Workflow Input.

    Site and Overlay are resolved server-side from ``host`` and ``pkey``.
    """

    host: str = Field(description="Hostname of the UFM server managing the InfiniBand fabric.")
    pkey: str = Field(description="Partition key whose membership will be expanded.")
    interfaces: list[InterfaceRef] = Field(
        default=[], description="DCIM interfaces to resolve to InfiniBand port GUIDs."
    )
    guids: list[str] = Field(
        default=[], description="InfiniBand port GUIDs to add directly to the partition."
    )
    guid_memberships: list[str] = Field(
        default=[], description="Per-GUID membership types corresponding to the supplied GUIDs."
    )
    membership_type: str = Field(
        default="full", description="Default partition membership type for added members."
    )
    ip_over_ib: bool = Field(
        default=True, description="Whether IP over InfiniBand is enabled for the partition."
    )

    @field_validator("pkey")
    @classmethod
    def _validate_pkey(cls, v: str) -> str:
        return validate_pkey_format(v)

    @field_validator("membership_type", mode="before")
    @classmethod
    def _normalize_membership(cls, v: object) -> str:
        return normalize_membership_type(v)

    @field_validator("guid_memberships", mode="before")
    @classmethod
    def _normalize_guid_memberships(cls, v: object) -> list[str]:
        return normalize_guid_membership_list(v)

    @model_validator(mode="after")
    def _validate(self) -> "IBPKeyMemberAddInput":
        validate_interfaces_xor_guids(self.interfaces, self.guids)
        validate_guid_memberships(self.guids, self.guid_memberships)
        return self


class IBPKeyMemberAddOutput(BaseModel):
    """InfiniBand PKey Member Add Workflow Output."""

    pkey: str
    overlay_id: str
    overlay_name: str
    members_added: int
    verified: bool
    assignment_ids: list[str]


@workflow.defn
class IBPKeyMemberAddWorkflow(UFMHostLockMixin, WorkflowMetadataMixin, StageMixin, ArchiveMixin):
    """Add device interface GUIDs to an existing IB PKey partition."""

    workflow_name = "InfiniBand PKey Member Add"
    workflow_description = "Add device interfaces to an existing InfiniBand PKey partition"
    workflow_input_class = IBPKeyMemberAddInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/ngc/ib_pkey_member_add"
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
            name="add_members",
            description="Add GUIDs to PKey partition on UFM",
            requires_approval=False,
            depends_on=["resolve_guids"],
        )
        self.define_stage(
            name="verify_members",
            description="Verify GUIDs are present in PKey on UFM",
            requires_approval=False,
            depends_on=["add_members"],
        )
        self.define_stage(
            name="record_assignments",
            description="Record OverlayAssignment entries in the DCIM",
            requires_approval=False,
            depends_on=["verify_members"],
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
        ufm_device_id: str
        location_id: str
        overlay_name: str

    @stage_executor("resolve_context")
    async def resolve_context(
        self, stage_input: ResolveContextStageInput
    ) -> ResolveContextStageOutput:
        """Resolve site/overlay from the DCIM and canonicalize pkey.

        Uses the add-specific resolver which lazily creates an Overlay at the
        device's Site when only an orphan PKey row exists in the DCIM.
        """
        resolved = await call_resolve_ib_context_for_add(stage_input.host, stage_input.pkey)

        return self.ResolveContextStageOutput(
            host=stage_input.host,
            site=resolved.location_name,
            pkey=resolved.pkey,
            overlay_id=resolved.overlay_id,
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
        guid_memberships: list[str] = Field(default_factory=list)
        default_membership: str = DEFAULT_MEMBERSHIP_TYPE

    class ResolveGuidsStageOutput(StageOutput):
        """Resolve GUIDs Stage Output."""

        resolved: list[ResolvedInterface]

    @stage_executor("resolve_guids")
    async def resolve_guids(self, stage_input: ResolveGuidsStageInput) -> ResolveGuidsStageOutput:
        """Resolve members from interfaces or GUIDs into DCIM interface records."""
        resolved, display = await resolve_members(
            stage_input.interfaces,
            stage_input.guids,
            stage_input.default_membership,
            stage_input.guid_memberships,
        )
        return self.ResolveGuidsStageOutput(resolved=resolved, display=display)

    # ------------------------------------------------------------------
    # Stage 2: Add GUIDs to PKey on UFM
    # ------------------------------------------------------------------

    class AddMembersStageInput(StageInput):
        """Add Members Stage Input."""

        host: str
        site: str | None
        pkey: str
        guids: list[str]
        memberships: list[str]
        ip_over_ib: bool

    class AddMembersStageOutput(StageOutput):
        """Add Members Stage Output."""

        pkey: str
        guids_added: list[str]

    @stage_executor("add_members")
    async def add_members(self, stage_input: AddMembersStageInput) -> AddMembersStageOutput:
        """Add the resolved GUIDs to the PKey partition on UFM."""
        result: AddGuidsOutput = await workflow.execute_activity(
            add_guids_to_pkey,
            AddGuidsInput(
                host=stage_input.host,
                site=stage_input.site,
                pkey=stage_input.pkey,
                guids=stage_input.guids,
                memberships=stage_input.memberships,
                ip_over_ib=stage_input.ip_over_ib,
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        return self.AddMembersStageOutput(
            pkey=result.pkey,
            guids_added=result.guids_added,
            display=result.display,
        )

    # ------------------------------------------------------------------
    # Stage 3: Verify GUIDs are present on UFM
    # ------------------------------------------------------------------

    class VerifyMembersStageInput(StageInput):
        """Verify Members Stage Input."""

        host: str
        site: str | None
        pkey: str
        expected_guids: list[str]
        expected_memberships: list[str]

    class VerifyMembersStageOutput(StageOutput):
        """Verify Members Stage Output."""

        pkey: str
        verified: bool

    @stage_executor("verify_members")
    async def verify_members(
        self, stage_input: VerifyMembersStageInput
    ) -> VerifyMembersStageOutput:
        """Verify all submitted GUIDs appear in the PKey on UFM."""
        result: VerifyPKeyMembersOutput = await workflow.execute_activity(
            verify_pkey_members,
            VerifyPKeyMembersInput(
                host=stage_input.host,
                site=stage_input.site,
                pkey=stage_input.pkey,
                expected_guids=stage_input.expected_guids,
                expected_memberships=stage_input.expected_memberships,
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        return self.VerifyMembersStageOutput(
            pkey=result.pkey,
            verified=result.verified,
            display=result.display,
        )

    # ------------------------------------------------------------------
    # Stage 4: Record overlay assignments in the DCIM
    # ------------------------------------------------------------------

    class RecordAssignmentsStageInput(StageInput):
        """Record Assignments Stage Input."""

        overlay_id: str
        resolved: list[ResolvedInterface]
        membership_type: str

    class RecordAssignmentsStageOutput(StageOutput):
        """Record Assignments Stage Output."""

        assignment_ids: list[str]

    @stage_executor("record_assignments")
    async def record_assignments(
        self, stage_input: RecordAssignmentsStageInput
    ) -> RecordAssignmentsStageOutput:
        """Create overlay assignment records in the DCIM for each interface."""
        result: RecordPKeyAssignmentsOutput = await workflow.execute_activity(
            record_pkey_assignments,
            RecordPKeyAssignmentsInput(
                overlay_id=stage_input.overlay_id,
                resolved=stage_input.resolved,
                membership_type=stage_input.membership_type,
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        return self.RecordAssignmentsStageOutput(
            assignment_ids=result.assignment_ids,
            display=result.display,
        )

    # ------------------------------------------------------------------
    # Main run
    # ------------------------------------------------------------------

    @run_nv_config_manager_workflow
    async def run(  # type: ignore[override, ty:invalid-method-override]  # pyright: ignore
        self, workflow_input: IBPKeyMemberAddInput
    ) -> IBPKeyMemberAddOutput:
        """Execute the IB PKey Member Add workflow."""
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
                guid_memberships=workflow_input.guid_memberships,
                default_membership=workflow_input.membership_type,
            )
        )

        guids = [r.guid for r in resolve_output.resolved]
        memberships = [r.membership for r in resolve_output.resolved]
        membership_by_guid = {r.guid: r.membership for r in resolve_output.resolved}

        add_output = await self.add_members(
            self.AddMembersStageInput(
                host=context.host,
                site=context.site,
                pkey=context.pkey,
                guids=guids,
                memberships=memberships,
                ip_over_ib=workflow_input.ip_over_ib,
            )
        )

        verify_output = await self.verify_members(
            self.VerifyMembersStageInput(
                host=context.host,
                site=context.site,
                pkey=context.pkey,
                expected_guids=add_output.guids_added,
                expected_memberships=[membership_by_guid[g] for g in add_output.guids_added],
            )
        )

        record_output = await self.record_assignments(
            self.RecordAssignmentsStageInput(
                overlay_id=context.overlay_id,
                resolved=resolve_output.resolved,
                membership_type=workflow_input.membership_type,
            )
        )

        await self.archive_results()
        return IBPKeyMemberAddOutput(
            pkey=verify_output.pkey,
            overlay_id=context.overlay_id,
            overlay_name=context.overlay_name,
            members_added=len(add_output.guids_added),
            verified=verify_output.verified,
            assignment_ids=record_output.assignment_ids,
        )
