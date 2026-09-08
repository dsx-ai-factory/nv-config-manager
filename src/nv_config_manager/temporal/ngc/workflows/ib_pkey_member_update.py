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
"""InfiniBand PKey Member Update (Reconcile) Workflow."""

from datetime import timedelta

from pydantic import BaseModel, Field, field_validator, model_validator
from temporalio import workflow
from temporalio.exceptions import ApplicationError

from nv_config_manager.temporal.common.decorators.workflow import run_nv_config_manager_workflow
from nv_config_manager.temporal.common.lock import WorkflowLockSpec
from nv_config_manager.temporal.common.mixins.metadata import WorkflowMetadataMixin
from nv_config_manager.temporal.common.mixins.stage import (
    StageInput,
    StageMixin,
    StageOutput,
    StateEnum,
    stage_executor,
)
from nv_config_manager.temporal.ngc.workflows._ib_pkey_lock import UFMHostLockMixin

with workflow.unsafe.imports_passed_through():
    from nv_config_manager.temporal.common.mixins.archive import ArchiveMixin
    from nv_config_manager.temporal.ngc.activities.ib_dcim import (
        CurrentAssignment,
        FetchPKeyAssignmentsInput,
        FetchPKeyAssignmentsOutput,
        InterfaceRef,
        ResolvedInterface,
        ResolveGuidsToInterfacesInput,
        ResolveGuidsToInterfacesOutput,
        SyncPKeyAssignmentsInput,
        SyncPKeyAssignmentsOutput,
        fetch_pkey_assignments,
        resolve_guids_to_interfaces,
        sync_pkey_assignments,
    )
    from nv_config_manager.temporal.ngc.activities.ib_pkey import (
        FetchPKeyMembersInput,
        FetchPKeyMembersOutput,
        SetGuidsInput,
        SetGuidsOutput,
        VerifyPKeyMembersInput,
        VerifyPKeyMembersOutput,
        fetch_pkey_members,
        set_pkey_members,
        verify_pkey_members,
    )
    from nv_config_manager.temporal.ngc.workflows._ib_pkey_helpers import (
        DEFAULT_ACTIVITY_RETRY_POLICY,
        DEFAULT_MEMBERSHIP_TYPE,
        call_resolve_ib_context,
        normalize_guid_membership_list,
        normalize_membership_type,
        resolve_members,
        validate_guid_memberships,
        validate_interfaces_xor_guids,
        validate_pkey_format,
    )


DCIM_STAGE_IDENTIFIERS_PATCH = "dcim-stage-identifiers-v1"


def _format_diff_lines(
    *,
    pkey: str,
    ifaces_to_add: list[ResolvedInterface],
    ifaces_to_remove: list[ResolvedInterface],
    guids_unchanged: list[str],
    ufm_only_removals: list[str] | None = None,
) -> str:
    """Format a human-readable diff with device/interface/GUID per member.

    ``ufm_only_removals`` are GUIDs present on UFM but absent from the DCIM; the
    exact-set PUT will drop them, so they are surfaced explicitly for approval.
    """
    lines: list[str] = [f"**PKey {pkey} membership diff:**"]

    lines.append(f"- Add {len(ifaces_to_add)} member(s):")
    if ifaces_to_add:
        for r in ifaces_to_add:
            lines.append(f"    + Add PKey {pkey} to {r.device}/{r.interface} (GUID {r.guid})")
    else:
        lines.append("    (none)")

    lines.append(f"- Remove {len(ifaces_to_remove)} member(s):")
    if ifaces_to_remove:
        for r in ifaces_to_remove:
            lines.append(f"    - Remove PKey {pkey} from {r.device}/{r.interface} (GUID {r.guid})")
    else:
        lines.append("    (none)")

    ufm_only_removals = ufm_only_removals or []
    if ufm_only_removals:
        lines.append(
            f"- Remove {len(ufm_only_removals)} untracked UFM-only member(s) "
            "(present on UFM, not in the DCIM):"
        )
        for guid in ufm_only_removals:
            lines.append(f"    - Remove PKey {pkey} from GUID {guid} (untracked on UFM)")

    lines.append(f"- Unchanged: {len(guids_unchanged)} member(s)")
    return "\n".join(lines)


def _unresolved_guid_values(
    requested_guids: list[str], resolved_interfaces: list[ResolvedInterface]
) -> list[str]:
    """Return requested GUIDs that are missing from a reverse-resolution result."""
    resolved_guids = {resolved.guid.lower() for resolved in resolved_interfaces}
    return [guid for guid in requested_guids if guid.lower() not in resolved_guids]


class IBPKeyMemberUpdateInput(BaseModel):
    """InfiniBand PKey Member Update Workflow Input.

    Site and Overlay are resolved server-side from ``host`` and ``pkey``.
    """

    host: str = Field(description="Hostname of the UFM server managing the InfiniBand fabric.")
    pkey: str = Field(description="Partition key whose membership will be replaced.")
    interfaces: list[InterfaceRef] = Field(
        default=[], description="DCIM interfaces to resolve to InfiniBand port GUIDs."
    )
    guids: list[str] = Field(
        default=[], description="InfiniBand port GUIDs that should belong to the partition."
    )
    guid_memberships: list[str] = Field(
        default=[], description="Per-GUID membership types corresponding to the supplied GUIDs."
    )
    membership_type: str = Field(
        default="full", description="Default partition membership type for updated members."
    )
    ip_over_ib: bool = Field(
        default=True,
        description="IP over InfiniBand setting used when the partition does not already exist.",
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
    def _validate(self) -> "IBPKeyMemberUpdateInput":
        validate_interfaces_xor_guids(self.interfaces, self.guids)
        validate_guid_memberships(self.guids, self.guid_memberships)
        return self


class IBPKeyMemberUpdateOutput(BaseModel):
    """InfiniBand PKey Member Update Workflow Output."""

    pkey: str
    overlay_id: str
    overlay_name: str
    members_added: int
    members_removed: int
    members_unchanged: int
    verified: bool
    assignment_ids_added: list[str]
    assignment_ids_removed: list[str]
    assignment_ids_unchanged: list[str]


@workflow.defn
class IBPKeyMemberUpdateWorkflow(UFMHostLockMixin, WorkflowMetadataMixin, StageMixin, ArchiveMixin):
    """Declarative reconciliation of IB PKey membership."""

    workflow_name = "InfiniBand PKey Member Update"
    workflow_description = "Reconcile InfiniBand PKey membership to a desired interface list"
    workflow_input_class = IBPKeyMemberUpdateInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/ngc/ib_pkey_member_update"
    workflow_namespace = "ngc"
    workflow_lock = WorkflowLockSpec(key_fields=["host", "pkey"])

    def __init__(self) -> None:
        """Initialize workflow with seven stages."""
        StageMixin.__init__(self)
        self._update_dcim_stage_name = (
            "update_dcim" if workflow.patched(DCIM_STAGE_IDENTIFIERS_PATCH) else "update_nautobot"
        )
        self.define_stage(
            name="resolve_context",
            description="Resolve site, overlay, and canonical pkey from the DCIM",
            requires_approval=False,
            depends_on=[],
        )
        self.define_stage(
            name="resolve_desired",
            description="Resolve desired interfaces to IB GUIDs from the DCIM",
            requires_approval=False,
            depends_on=["resolve_context"],
        )
        self.define_stage(
            name="query_current",
            description="Fetch current OverlayAssignments from the DCIM and compute diff",
            requires_approval=False,
            depends_on=["resolve_desired"],
        )
        self.define_stage(
            name="validate_diff",
            description="Review membership changes — approval required when members are removed",
            requires_approval=True,
            approval_threshold=1,
            depends_on=["query_current"],
        )
        self.define_stage(
            name=self._update_dcim_stage_name,
            description="Sync OverlayAssignment records in the DCIM",
            requires_approval=False,
            depends_on=["validate_diff"],
        )
        self.define_stage(
            name="update_ufm",
            description="Remove stale and add new GUIDs on UFM",
            requires_approval=False,
            depends_on=[self._update_dcim_stage_name],
        )
        self.define_stage(
            name="verify_ufm",
            description="Verify final UFM GUID membership matches desired set",
            requires_approval=False,
            depends_on=["update_ufm"],
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
        """Resolve site/overlay from the DCIM and canonicalize pkey."""
        resolved = await call_resolve_ib_context(stage_input.host, stage_input.pkey)

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
    # Stage 1: Resolve desired interfaces → GUIDs
    # ------------------------------------------------------------------

    class ResolveDesiredStageInput(StageInput):
        """Resolve Desired Stage Input."""

        interfaces: list[InterfaceRef] = Field(default_factory=list)
        guids: list[str] = Field(default_factory=list)
        guid_memberships: list[str] = Field(default_factory=list)
        default_membership: str = DEFAULT_MEMBERSHIP_TYPE

    class ResolveDesiredStageOutput(StageOutput):
        """Resolve Desired Stage Output."""

        resolved: list[ResolvedInterface]

    @stage_executor("resolve_desired")
    async def resolve_desired(
        self, stage_input: ResolveDesiredStageInput
    ) -> ResolveDesiredStageOutput:
        """Resolve desired members from interfaces or GUIDs into DCIM interface records."""
        resolved, display = await resolve_members(
            stage_input.interfaces,
            stage_input.guids,
            stage_input.default_membership,
            stage_input.guid_memberships,
        )
        return self.ResolveDesiredStageOutput(resolved=resolved, display=display)

    # ------------------------------------------------------------------
    # Stage 2: Query current DCIM state, compute diff
    # ------------------------------------------------------------------

    class QueryCurrentStageInput(StageInput):
        """Query Current Stage Input."""

        host: str
        site: str | None
        pkey: str
        overlay_id: str
        resolved: list[ResolvedInterface]

    class QueryCurrentStageOutput(StageOutput):
        """Query Current Stage Output."""

        current_assignments: list[CurrentAssignment]
        guids_to_add: list[str]
        guids_to_remove: list[str]
        guids_unchanged: list[str]
        ifaces_to_add: list[ResolvedInterface]
        ifaces_to_remove: list[ResolvedInterface]
        ifaces_unchanged: list[ResolvedInterface]
        membership_changed: bool
        ufm_only_removals: list[str]
        ufm_ip_over_ib: bool | None

    @stage_executor("query_current")
    async def query_current(self, stage_input: QueryCurrentStageInput) -> QueryCurrentStageOutput:
        """Fetch current DCIM assignments and compute the membership diff."""
        result: FetchPKeyAssignmentsOutput = await workflow.execute_activity(
            fetch_pkey_assignments,
            FetchPKeyAssignmentsInput(overlay_id=stage_input.overlay_id),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        desired_iface_ids = {r.interface_id for r in stage_input.resolved}
        current_iface_ids = {a.interface_id for a in result.assignments}

        to_add_iface_ids = desired_iface_ids - current_iface_ids
        to_remove_iface_ids = current_iface_ids - desired_iface_ids
        unchanged_iface_ids = desired_iface_ids & current_iface_ids

        desired_by_id = {r.interface_id: r for r in stage_input.resolved}
        current_by_id = {a.interface_id: a for a in result.assignments}

        guids_to_add = [desired_by_id[i].guid for i in to_add_iface_ids]
        guids_to_remove = [current_by_id[i].guid for i in to_remove_iface_ids]
        guids_unchanged = [desired_by_id[i].guid for i in unchanged_iface_ids]
        ifaces_to_add = [desired_by_id[i] for i in to_add_iface_ids]
        ifaces_unchanged = [desired_by_id[i] for i in unchanged_iface_ids]

        # A member that stays in the set but flips full<->limited produces no GUID
        # add/remove, yet still needs a UFM PUT so verify_ufm sees the new membership.
        membership_changed = any(
            desired_by_id[i].membership != current_by_id[i].membership_type
            for i in unchanged_iface_ids
        )

        ifaces_to_remove: list[ResolvedInterface] = []
        if guids_to_remove:
            remove_resolution: ResolveGuidsToInterfacesOutput = await workflow.execute_activity(
                resolve_guids_to_interfaces,
                ResolveGuidsToInterfacesInput(guids=guids_to_remove),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            )
            ifaces_to_remove = remove_resolution.resolved
            unresolved_guids = _unresolved_guid_values(guids_to_remove, ifaces_to_remove)
            if unresolved_guids:
                raise ApplicationError(
                    f"Unable to resolve removal GUID(s) to DCIM interfaces: {unresolved_guids}",
                    non_retryable=True,
                )

        ufm_state: FetchPKeyMembersOutput = await workflow.execute_activity(
            fetch_pkey_members,
            FetchPKeyMembersInput(
                host=stage_input.host,
                site=stage_input.site,
                pkey=stage_input.pkey,
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        desired_guid_set = {r.guid.lower() for r in stage_input.resolved}
        tracked_removals = {g.lower() for g in guids_to_remove}
        ufm_only_removals = sorted(set(ufm_state.guids) - desired_guid_set - tracked_removals)

        display = _format_diff_lines(
            pkey=stage_input.pkey,
            ifaces_to_add=ifaces_to_add,
            ifaces_to_remove=ifaces_to_remove,
            guids_unchanged=guids_unchanged,
            ufm_only_removals=ufm_only_removals,
        )

        return self.QueryCurrentStageOutput(
            current_assignments=result.assignments,
            guids_to_add=guids_to_add,
            guids_to_remove=guids_to_remove,
            guids_unchanged=guids_unchanged,
            ifaces_to_add=ifaces_to_add,
            ifaces_to_remove=ifaces_to_remove,
            ifaces_unchanged=ifaces_unchanged,
            membership_changed=membership_changed,
            ufm_only_removals=ufm_only_removals,
            ufm_ip_over_ib=ufm_state.ip_over_ib if ufm_state.exists else None,
            display=display,
        )

    # ------------------------------------------------------------------
    # Stage 3: Validate diff — approval gate when removals exist
    # ------------------------------------------------------------------

    class ValidateDiffStageInput(StageInput):
        """Validate Diff Stage Input."""

        pkey: str
        ifaces_to_add: list[ResolvedInterface]
        ifaces_to_remove: list[ResolvedInterface]
        guids_unchanged: list[str]
        ufm_only_removals: list[str]

    class ValidateDiffStageOutput(StageOutput):
        """Validate Diff Stage Output."""

        approved: bool

    @stage_executor("validate_diff")
    async def validate_diff(self, stage_input: ValidateDiffStageInput) -> ValidateDiffStageOutput:
        """Gate on approval when the diff drops any UFM member; auto-approve additions-only.

        Removals include both DCIM-tracked members and untracked UFM-only
        members, since the exact-set PUT drops both.
        """
        stage_name = "validate_diff"

        if not stage_input.ifaces_to_remove and not stage_input.ufm_only_removals:
            self.get_stage_by_name(stage_name).requires_approval = False
            return self.ValidateDiffStageOutput(
                approved=True,
                display=(
                    f"Auto-approved: no members being removed from PKey {stage_input.pkey} "
                    f"(+{len(stage_input.ifaces_to_add)} additions, "
                    f"{len(stage_input.guids_unchanged)} unchanged)"
                ),
            )

        display = "Approval required.\n" + _format_diff_lines(
            pkey=stage_input.pkey,
            ifaces_to_add=stage_input.ifaces_to_add,
            ifaces_to_remove=stage_input.ifaces_to_remove,
            guids_unchanged=stage_input.guids_unchanged,
            ufm_only_removals=stage_input.ufm_only_removals,
        )

        output = self.ValidateDiffStageOutput(approved=False, display=display)
        self.set_stage_output(stage_name, output)
        self.set_stage_state(stage_name, StateEnum.PENDING_APPROVAL)
        await workflow.wait_condition(
            lambda: self.get_stage_state(stage_name) != StateEnum.PENDING_APPROVAL
        )

        approved = self.get_stage_state(stage_name) == StateEnum.APPROVED
        approval_word = "Approved" if approved else "Rejected"
        return self.ValidateDiffStageOutput(
            approved=approved,
            display=f"{approval_word}.\n{display}",
        )

    # ------------------------------------------------------------------
    # Stage 4: Update the DCIM
    # ------------------------------------------------------------------

    class UpdateDCIMStageInput(StageInput):
        """Update DCIM stage input."""

        overlay_id: str
        desired: list[ResolvedInterface]
        membership_type: str

    class UpdateDCIMStageOutput(StageOutput):
        """Update DCIM stage output."""

        added: list[str]
        removed: list[str]
        unchanged: list[str]

    @stage_executor("update_nautobot", name_attribute="_update_dcim_stage_name")
    async def update_dcim(self, stage_input: UpdateDCIMStageInput) -> UpdateDCIMStageOutput:
        """Sync overlay assignment records in the DCIM to match the desired list."""
        result: SyncPKeyAssignmentsOutput = await workflow.execute_activity(
            sync_pkey_assignments,
            SyncPKeyAssignmentsInput(
                overlay_id=stage_input.overlay_id,
                desired=stage_input.desired,
                membership_type=stage_input.membership_type,
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        return self.UpdateDCIMStageOutput(
            added=result.added,
            removed=result.removed,
            unchanged=result.unchanged,
            display=result.display,
        )

    # ------------------------------------------------------------------
    # Stage 5: Update UFM
    # ------------------------------------------------------------------

    class UpdateUFMStageInput(StageInput):
        """Update UFM Stage Input."""

        host: str
        site: str | None
        pkey: str
        desired_guids: list[str]
        desired_memberships: list[str]
        guids_to_add: list[str]
        guids_to_remove: list[str]
        membership_changed: bool
        ip_over_ib: bool

    class UpdateUFMStageOutput(StageOutput):
        """Update UFM Stage Output."""

        guids_set: list[str]

    @stage_executor("update_ufm")
    async def update_ufm(self, stage_input: UpdateUFMStageInput) -> UpdateUFMStageOutput:
        """Reconcile UFM membership to the exact desired GUID set."""
        if not stage_input.desired_guids:
            return self.UpdateUFMStageOutput(
                guids_set=[],
                display=f"No desired members for PKey {stage_input.pkey}; nothing to set on UFM",
            )

        set_result: SetGuidsOutput = await workflow.execute_activity(
            set_pkey_members,
            SetGuidsInput(
                host=stage_input.host,
                site=stage_input.site,
                pkey=stage_input.pkey,
                guids=stage_input.desired_guids,
                memberships=stage_input.desired_memberships,
                ip_over_ib=stage_input.ip_over_ib,
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        lines = [
            f"UFM membership set for PKey {stage_input.pkey}:",
            f"- Now {len(set_result.guids_set)} member(s)",
            f"- (+{len(stage_input.guids_to_add)} added, "
            f"-{len(stage_input.guids_to_remove)} removed vs previous)",
        ]
        if (
            stage_input.membership_changed
            and not stage_input.guids_to_add
            and not stage_input.guids_to_remove
        ):
            lines.append("- Membership type changed on existing member(s)")
        return self.UpdateUFMStageOutput(
            guids_set=set_result.guids_set,
            display="\n".join(lines),
        )

    # ------------------------------------------------------------------
    # Stage 6: Verify UFM state
    # ------------------------------------------------------------------

    class VerifyUFMStageInput(StageInput):
        """Verify UFM Stage Input."""

        host: str
        site: str | None
        pkey: str
        expected_guids: list[str]
        expected_memberships: list[str]

    class VerifyUFMStageOutput(StageOutput):
        """Verify UFM Stage Output."""

        pkey: str
        verified: bool

    @stage_executor("verify_ufm")
    async def verify_ufm(self, stage_input: VerifyUFMStageInput) -> VerifyUFMStageOutput:
        """Verify that UFM membership exactly matches the desired GUID set."""
        result: VerifyPKeyMembersOutput = await workflow.execute_activity(
            verify_pkey_members,
            VerifyPKeyMembersInput(
                host=stage_input.host,
                site=stage_input.site,
                pkey=stage_input.pkey,
                expected_guids=stage_input.expected_guids,
                expected_memberships=stage_input.expected_memberships,
                exact=True,
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        return self.VerifyUFMStageOutput(
            pkey=result.pkey,
            verified=result.verified,
            display=result.display,
        )

    # ------------------------------------------------------------------
    # Main run
    # ------------------------------------------------------------------

    @run_nv_config_manager_workflow
    async def run(  # type: ignore[override, ty:invalid-method-override]  # pyright: ignore
        self, workflow_input: IBPKeyMemberUpdateInput
    ) -> IBPKeyMemberUpdateOutput:
        """Execute the IB PKey Member Update workflow."""
        self.set_input(workflow_input)

        context = await self.resolve_context(
            self.ResolveContextStageInput(
                host=workflow_input.host,
                pkey=workflow_input.pkey,
            )
        )

        resolve_output = await self.resolve_desired(
            self.ResolveDesiredStageInput(
                interfaces=workflow_input.interfaces,
                guids=workflow_input.guids,
                guid_memberships=workflow_input.guid_memberships,
                default_membership=workflow_input.membership_type,
            )
        )

        query_output = await self.query_current(
            self.QueryCurrentStageInput(
                host=context.host,
                site=context.site,
                pkey=context.pkey,
                overlay_id=context.overlay_id,
                resolved=resolve_output.resolved,
            )
        )

        validate_output = await self.validate_diff(
            self.ValidateDiffStageInput(
                pkey=context.pkey,
                ifaces_to_add=query_output.ifaces_to_add,
                ifaces_to_remove=query_output.ifaces_to_remove,
                guids_unchanged=query_output.guids_unchanged,
                ufm_only_removals=query_output.ufm_only_removals,
            )
        )

        if not validate_output.approved:
            self.set_stage_state(self._update_dcim_stage_name, StateEnum.UNREACHABLE)
            self.set_stage_state("update_ufm", StateEnum.UNREACHABLE)
            self.set_stage_state("verify_ufm", StateEnum.UNREACHABLE)
            await self.archive_results()
            return IBPKeyMemberUpdateOutput(
                pkey=context.pkey,
                overlay_id=context.overlay_id,
                overlay_name=context.overlay_name,
                members_added=0,
                members_removed=0,
                members_unchanged=len(query_output.guids_unchanged),
                verified=False,
                assignment_ids_added=[],
                assignment_ids_removed=[],
                assignment_ids_unchanged=[],
            )

        dcim_output = await self.update_dcim(
            self.UpdateDCIMStageInput(
                overlay_id=context.overlay_id,
                desired=resolve_output.resolved,
                membership_type=workflow_input.membership_type,
            )
        )

        desired_guids = [r.guid for r in resolve_output.resolved]
        desired_memberships = [r.membership for r in resolve_output.resolved]
        # Preserve the partition's existing ip_over_ib instead of forcing the
        # input default onto an already-configured partition.
        ip_over_ib = (
            query_output.ufm_ip_over_ib
            if query_output.ufm_ip_over_ib is not None
            else workflow_input.ip_over_ib
        )
        await self.update_ufm(
            self.UpdateUFMStageInput(
                host=context.host,
                site=context.site,
                pkey=context.pkey,
                desired_guids=desired_guids,
                desired_memberships=desired_memberships,
                guids_to_add=query_output.guids_to_add,
                guids_to_remove=query_output.guids_to_remove,
                membership_changed=query_output.membership_changed,
                ip_over_ib=ip_over_ib,
            )
        )

        verify_output = await self.verify_ufm(
            self.VerifyUFMStageInput(
                host=context.host,
                site=context.site,
                pkey=context.pkey,
                expected_guids=desired_guids,
                expected_memberships=desired_memberships,
            )
        )

        await self.archive_results()
        return IBPKeyMemberUpdateOutput(
            pkey=verify_output.pkey,
            overlay_id=context.overlay_id,
            overlay_name=context.overlay_name,
            members_added=len(dcim_output.added),
            members_removed=len(dcim_output.removed),
            members_unchanged=len(dcim_output.unchanged),
            verified=verify_output.verified,
            assignment_ids_added=dcim_output.added,
            assignment_ids_removed=dcim_output.removed,
            assignment_ids_unchanged=dcim_output.unchanged,
        )
