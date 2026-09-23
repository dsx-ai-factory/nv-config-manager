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
"""Sandbox-only staged Backbone operational workflows."""

from __future__ import annotations

import asyncio
import difflib
import ipaddress
from datetime import timedelta

from pydantic import BaseModel, Field, field_validator, model_validator
from temporalio import workflow
from temporalio.common import RetryPolicy
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
from nv_config_manager.temporal.common.search_attributes import ISSUE_KEY_SEARCH_ATTRIBUTE

with workflow.unsafe.imports_passed_through():
    from nv_config_manager.temporal.bb_sandbox.activities import (
        MAINTENANCE_STATUS,
        MOCK_DRAIN_METRIC,
        ActivateBackboneRoutingInput,
        ActivationDeviceCheckInput,
        ActivationDeviceCheckOutput,
        ApplyBackboneAddressingInput,
        CircuitReservationLookupInput,
        CircuitReservationPlan,
        CircuitTurnupEndpoint,
        CircuitTurnupIntent,
        CircuitTurnupLookupInput,
        DrainApplyInput,
        DrainCandidateInput,
        DrainIntent,
        DrainLookupInput,
        EnableBackboneInterfacesInput,
        InternalBackboneIntent,
        InternalBackboneLookupInput,
        MockAppliedIntentInput,
        MockDiffInput,
        MockNeighborInput,
        MockPingInput,
        MockRoutingInput,
        PlannedCircuitObjects,
        ProposedInterfaceRenderInput,
        ProposedNautobotMutation,
        RelatedIgpChange,
        RenderRevisionDiffInput,
        SetInterfaceStatusInput,
        activate_backbone_routing,
        activate_planned_circuit,
        apply_backbone_addressing,
        apply_drain_candidate,
        build_circuit_turnup_plan,
        check_activation_device_state,
        enable_backbone_interfaces,
        load_render_revision_diff,
        mock_apply_candidate,
        mock_ping_rtt,
        mock_validate_applied_intent,
        mock_validate_circuit_turnup,
        mock_validate_neighbor,
        mock_validate_routing,
        perform_drain_candidate_diff,
        persist_circuit_reservation,
        persist_circuit_turnup_intent,
        plan_circuit_reservation,
        render_proposed_interface_intent,
        resolve_circuit_turnup_intent,
        resolve_drain_intent,
        resolve_internal_backbone_intent,
        resolve_planned_circuit,
        set_interface_status,
    )
    from nv_config_manager.temporal.common.mixins.device import DeviceMixin, NetworkDeviceData
    from nv_config_manager.temporal.ngc.activities.config import (
        build_workflow_url,
        get_ui_base_url,
    )
    from nv_config_manager.temporal.ngc.activities.deploy import (
        LoadPartialConfigurationActivityInput,
        load_partial_configuration,
    )
    from nv_config_manager.temporal.ngc.activities.nautobot import (
        GetNetworkDeviceInput,
        get_network_device,
    )
    from nv_config_manager.temporal.ngc.activities.render import ExecuteRenderInput, execute_render
    from nv_config_manager.temporal.ngc.activities.ticketing import (
        AddCommentInput,
        ValidateTicketInput,
        add_ticket_comment,
        validate_ticket,
    )

ACTIVITY_TIMEOUT = timedelta(minutes=1)
ACTIVITY_RETRY_POLICY = RetryPolicy(maximum_attempts=3)
MOCK_NOTICE = "This result is simulated; no network device connection was attempted."
DRAIN_CONFIG_FILE = "interfaces"


def _markdown_diff(diff: str) -> str:
    """Format a diff as a standalone fenced block for the workflow UI."""
    content = diff.rstrip() or "(no configuration changes)"
    return f"```diff\n{content}\n```"


def _jira_diff(diff: str) -> str:
    """Format a diff with Jira wiki markup that preserves whitespace."""
    content = diff.rstrip() or "(no configuration changes)"
    return f"{{noformat}}\n{content}\n{{noformat}}"


def _jira_written_objects(mutations: list[ProposedNautobotMutation]) -> str:
    if not mutations:
        return ""
    lines = "\n".join(f"* {item.model}: {item.summary}" for item in mutations)
    return f"\n\nh4. Planned objects written\n{lines}"


def _jira_validation_checks(checks: list[str], *, mocked: bool) -> str:
    if not checks:
        return ""
    lines = "\n".join(f"* {check}" for check in checks)
    notice = f"\n\n{MOCK_NOTICE}" if mocked else ""
    return f"\n\nh4. Post-change validation\n{lines}{notice}"


def _verify_execution_render(proposed: str, persisted: str) -> None:
    """Reject an execution render that differs from the approved plan."""
    if persisted != proposed:
        raise ApplicationError(
            "Persisted render differs from the approved plan; re-plan before deploying",
            non_retryable=True,
        )


class DrainPreflightComparison(BaseModel):
    """Comparison of approved and freshly rendered drain artifacts."""

    matched: bool
    reason: str | None = None
    render_diff: str = ""
    fresh_candidate_diff: str

    @classmethod
    def from_artifacts(
        cls,
        *,
        approved_configuration: str,
        fresh_configuration: str,
        approved_candidate_diff: str,
        fresh_candidate_diff: str,
    ) -> DrainPreflightComparison:
        """Compare a fresh non-persistent proposal with the approved plan."""
        configuration_changed = fresh_configuration != approved_configuration
        candidate_changed = fresh_candidate_diff != approved_candidate_diff
        reasons = []
        if configuration_changed:
            reasons.append("render changed since approval")
        if candidate_changed:
            reasons.append("candidate diff changed since approval")
        render_diff = ""
        if configuration_changed:
            render_diff = "\n".join(
                difflib.unified_diff(
                    approved_configuration.splitlines(),
                    fresh_configuration.splitlines(),
                    fromfile="approved",
                    tofile="current",
                    lineterm="",
                )
            )
        return cls(
            matched=not reasons,
            reason="; ".join(reasons) or None,
            render_diff=render_diff,
            fresh_candidate_diff=fresh_candidate_diff,
        )


class ActivationPreflightComparison(BaseModel):
    """Nautobot candidate drift plus device occupancy for circuit activate."""

    matched: bool
    reason: str | None = None
    conflicts: list[str] = Field(default_factory=list)

    @classmethod
    def from_checks(
        cls,
        *,
        candidate_matched: bool,
        conflicts: list[str],
    ) -> ActivationPreflightComparison:
        reasons: list[str] = []
        if not candidate_matched:
            reasons.append("candidate changed since approval")
        if conflicts:
            reasons.append("device already has reserved interfaces: " + "; ".join(conflicts))
        return cls(
            matched=not reasons,
            reason="; ".join(reasons) or None,
            conflicts=conflicts,
        )


class DrainInterfaceInput(BaseModel):
    """Operator input for the Backbone interface drain demonstration."""

    device: str = Field(min_length=1, description="Nautobot device name or UUID.")
    port: str = Field(min_length=1, description="Physical interface or LAG to drain.")
    jira: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z][A-Za-z0-9]+-\d+$",
        description="Optional Jira issue used to record the change and review.",
    )
    user: str | None = Field(default=None, description="Submitting user, populated by the API.")


class DrainInterfaceOutput(BaseModel):
    """Final drain result."""

    applied: bool
    device: str
    port: str
    status: str
    approvers: list[str]
    jira: str | None


class InternalBackboneBringupInput(BaseModel):
    """Operator intent for a two-ended internal Backbone circuit."""

    circuit_id: str = Field(min_length=1, description="Circuit ID already modeled in Nautobot.")
    jira: str = Field(
        pattern=r"^[A-Za-z][A-Za-z0-9]+-\d+$",
        description="Jira issue used for the staged audit record.",
    )
    local_device: str = Field(min_length=1, description="Local Nautobot device name or UUID.")
    local_ports: list[str] = Field(min_length=1, description="Local physical LAG members.")
    remote_device: str = Field(min_length=1, description="Remote Nautobot device name or UUID.")
    remote_ports: list[str] = Field(min_length=1, description="Remote physical LAG members.")
    lag_name: str | None = Field(
        default=None,
        pattern=r"^ae\d+$",
        description="Optional common LAG name; defaults to the first aeN free on both sides from ae100.",
    )
    ipv4_prefix: str = Field(description="Point-to-point IPv4 /31 prefix.")
    ipv6_prefix: str = Field(description="Point-to-point IPv6 /127 prefix.")
    expected_rtt_ms: float = Field(gt=0, description="Maximum acceptable average RTT in ms.")
    igp_metric_override: int | None = Field(
        default=None,
        ge=1,
        le=16_777_214,
        description="Optional stored IS-IS metric; defaults to max(10, round(RTT ms × 10)).",
    )
    minimum_links: int = Field(
        ge=1,
        description="Minimum active LAG members, written to both endpoint LAGs.",
    )
    user: str | None = Field(default=None, description="Submitting user, populated by the API.")

    @field_validator("local_ports", "remote_ports")
    @classmethod
    def unique_ports(cls, ports: list[str]) -> list[str]:
        """Reject duplicate or empty port names."""
        normalized = [port.strip() for port in ports]
        if any(not port for port in normalized):
            raise ValueError("ports cannot contain empty names")
        if len(set(normalized)) != len(normalized):
            raise ValueError("ports must be unique")
        return normalized

    @field_validator("ipv4_prefix")
    @classmethod
    def validate_ipv4_prefix(cls, prefix: str) -> str:
        """Require a canonical point-to-point IPv4 network."""
        network = ipaddress.ip_network(prefix, strict=True)
        if network.version != 4 or network.prefixlen != 31:
            raise ValueError("ipv4_prefix must be an IPv4 /31 network")
        return str(network)

    @field_validator("ipv6_prefix")
    @classmethod
    def validate_ipv6_prefix(cls, prefix: str) -> str:
        """Require a canonical point-to-point IPv6 network."""
        network = ipaddress.ip_network(prefix, strict=True)
        if network.version != 6 or network.prefixlen != 127:
            raise ValueError("ipv6_prefix must be an IPv6 /127 network")
        return str(network)

    @model_validator(mode="after")
    def validate_endpoints(self) -> InternalBackboneBringupInput:
        """Require distinct endpoints and feasible minimum-links intent."""
        if self.local_device == self.remote_device:
            raise ValueError("local and remote devices must be distinct")
        if self.minimum_links > min(len(self.local_ports), len(self.remote_ports)):
            raise ValueError("minimum_links cannot exceed either endpoint's member count")
        return self

    def selected_igp_metric(self) -> int:
        """Choose the one-time metric value that will be persisted to Nautobot."""
        return self.igp_metric_override or max(10, round(self.expected_rtt_ms * 10))


class InternalBackboneBringupOutput(BaseModel):
    """Final internal Backbone turnup result."""

    applied: bool
    circuit_id: str
    local_endpoint: str
    remote_endpoint: str
    ipv4_prefix: str
    ipv6_prefix: str
    igp_metric: int
    average_rtt_ms: float | None
    approvers: dict[str, list[str]]
    jira: str


class BringupEndpointRender(BaseModel):
    """One router's pinned post-mutation render and revision delta."""

    device_name: str
    device_id: str
    commit_id: str
    config_url: str
    diff: str


class _ApprovalMixin(StageMixin):
    """Shared deterministic approval and audit formatting."""

    async def wait_for_review(
        self, stage_name: str, diff: str, *, mocked: bool = True
    ) -> tuple[bool, list[str]]:
        """Publish a candidate diff, wait for review, and return the reviewers."""
        heading = "Mock candidate diff" if mocked else "Rendered configuration diff"
        notice = f"\n\n{MOCK_NOTICE}" if mocked else ""
        self.set_stage_output(
            stage_name,
            StageOutput(display=f"{heading} for approval\n\n{_markdown_diff(diff)}{notice}"),
        )
        self.set_stage_state(stage_name, StateEnum.PENDING_APPROVAL)
        await workflow.wait_condition(
            lambda: self.get_stage_state(stage_name) != StateEnum.PENDING_APPROVAL
        )
        approved = self.get_stage_state(stage_name) == StateEnum.APPROVED
        stage = self.get_stage_by_name(stage_name)
        reviewers = [review.user for review in (stage.approvers if approved else stage.rejecters)]
        return approved, reviewers

    async def workflow_url(self) -> str:
        """Resolve the operator-facing URL for the current workflow."""
        ui_base_url = await workflow.execute_activity(
            get_ui_base_url,
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        return build_workflow_url(ui_base_url, workflow.info().workflow_id)


@workflow.defn
class BBDrainInterfaceWorkflow(WorkflowMetadataMixin, _ApprovalMixin, DeviceMixin):
    """Plan a Maintenance render, then persist and deploy it after approval."""

    workflow_name = "BB Sandbox: Drain Interface"
    workflow_description = "Render proposed Maintenance intent, review it, then persist and deploy"
    workflow_input_class = DrainInterfaceInput
    workflow_api_endpoint = "/bb_sandbox/drain_interface"
    workflow_namespace = "bb_sandbox"
    workflow_lock = WorkflowLockSpec(key_fields=["device", "port"])

    def __init__(self) -> None:
        """Define the drain demonstration stages."""
        StageMixin.__init__(self)
        self.define_stage(
            name="resolve",
            description="Validate interface and Jira if selected.",
            requires_approval=False,
            depends_on=[],
        )
        self.define_stage(
            name="plan",
            description="Render the proposed change in memory.",
            requires_approval=False,
            depends_on=["resolve"],
        )
        self.define_stage(
            name="record_plan",
            description="Post the diff to Jira.",
            requires_approval=False,
            depends_on=["plan"],
        )
        self.define_stage(
            name="review",
            description="Approve or reject the diff.",
            requires_approval=True,
            approval_threshold=1,
            depends_on=["record_plan"],
        )
        self.define_stage(
            name="preflight",
            description="Re-check that the plan is still current.",
            requires_approval=False,
            depends_on=["review"],
        )
        self.define_stage(
            name="persist",
            description="Write status and metric to Nautobot.",
            requires_approval=False,
            depends_on=["preflight"],
        )
        self.define_stage(
            name="render",
            description="Render persisted intent; verify against the plan.",
            requires_approval=False,
            depends_on=["persist"],
        )
        self.define_stage(
            name="apply",
            description="Commit to the device.",
            requires_approval=False,
            depends_on=["render"],
        )
        self.define_stage(
            name="validate",
            description="Verify the metric on the device.",
            requires_approval=False,
            depends_on=["apply"],
        )
        self.define_stage(
            name="audit",
            description="Post the result to Jira.",
            requires_approval=False,
            depends_on=["review"],
        )

    class ResolveInput(StageInput):
        """Drain resolution input."""

        device: str
        port: str
        jira: str | None

    class ResolveOutput(StageOutput):
        """Drain resolution output."""

        intent: DrainIntent

    @stage_executor("resolve")
    async def resolve(self, stage_input: ResolveInput) -> ResolveOutput:
        """Resolve real sandbox records before proposing a change."""
        ticket_line = "No Jira supplied (ticketless drain)."
        if stage_input.jira:
            ticket = await workflow.execute_activity(
                validate_ticket,
                ValidateTicketInput(ticketing_platform="jira", issue_key=stage_input.jira),
                start_to_close_timeout=ACTIVITY_TIMEOUT,
                retry_policy=ACTIVITY_RETRY_POLICY,
            )
            ticket_line = f"Jira [{stage_input.jira}]({ticket.url}): {ticket.summary}"
        intent = await workflow.execute_activity(
            resolve_drain_intent,
            DrainLookupInput(device=stage_input.device, port=stage_input.port),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        return self.ResolveOutput(
            intent=intent,
            display=(
                f"{ticket_line}\n\nResolved `{intent.device_name}:{intent.interface.name}`; "
                f"current status: **{intent.interface.status}**."
            ),
        )

    class PlanInput(StageInput):
        """Non-persistent drain proposal input."""

        intent: DrainIntent

    class PlanOutput(StageOutput):
        """Proposed configuration and device data used to calculate the plan."""

        device: NetworkDeviceData
        intended_config: str
        diff: str
        mocked: bool

    @stage_executor("plan")
    async def plan_proposed_configuration(self, stage_input: PlanInput) -> PlanOutput:
        """Render the exact proposed values without changing Nautobot or Config Store."""
        device_result = await workflow.execute_activity(
            get_network_device,
            GetNetworkDeviceInput(device_id=stage_input.intent.device_id),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        DeviceMixin.attach_device_search_attributes(device_result.device)
        proposed = await workflow.execute_activity(
            render_proposed_interface_intent,
            ProposedInterfaceRenderInput(
                device_id=stage_input.intent.device_id,
                interface_name=stage_input.intent.interface.name,
                status=MAINTENANCE_STATUS,
                isis_metric=MOCK_DRAIN_METRIC,
                filename=DRAIN_CONFIG_FILE,
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        candidate = await workflow.execute_activity(
            perform_drain_candidate_diff,
            DrainCandidateInput(
                device_data=device_result.device,
                configuration=proposed.configuration,
                interface_name=stage_input.intent.interface.name,
                current_metric=stage_input.intent.interface.isis_metric,
            ),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        return self.PlanOutput(
            device=device_result.device,
            intended_config=proposed.configuration,
            diff=candidate.diff,
            mocked=candidate.mocked,
            display=(
                f"Rendered proposed **{MAINTENANCE_STATUS}** intent for "
                f"`{stage_input.intent.device_name}:{stage_input.intent.interface.name}` "
                "in memory. Nautobot and Config Store are unchanged.\n\n"
                f"{_markdown_diff(candidate.diff)}"
            ),
        )

    class RecordPlanInput(StageInput):
        """Proposed diff audit input."""

        jira: str | None
        intent: DrainIntent
        diff: str
        workflow_url: str

    class RecordPlanOutput(StageOutput):
        """Jira plan comment result."""

        comment_id: str | None

    @stage_executor("record_plan")
    async def record_change_plan(self, stage_input: RecordPlanInput) -> RecordPlanOutput:
        """Publish the generated plan before waiting for approval."""
        target = f"{stage_input.intent.device_name}:{stage_input.intent.interface.name}"
        summary = (
            "### Proposed interface drain\n\n"
            f"- **Workflow:** [Open workflow]({stage_input.workflow_url})\n"
            f"- **Target:** `{target}`\n"
            "- **Nautobot:** unchanged pending approval\n\n"
            f"#### Proposed candidate diff\n\n{_markdown_diff(stage_input.diff)}"
        )
        if not stage_input.jira:
            return self.RecordPlanOutput(comment_id=None, display=summary)
        jira_body = (
            "h3. Proposed BB sandbox interface drain\n\n"
            f"*Workflow:* [Open workflow|{stage_input.workflow_url}]\n"
            f"*Target:* {target}\n"
            "*Nautobot:* unchanged pending approval\n\n"
            "h4. Proposed candidate diff\n"
            f"{_jira_diff(stage_input.diff)}"
        )
        result = await workflow.execute_activity(
            add_ticket_comment,
            AddCommentInput(
                ticketing_platform="jira",
                issue_key=stage_input.jira,
                body=jira_body,
            ),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        return self.RecordPlanOutput(
            comment_id=result.comment_id,
            display=f"Recorded change plan `{result.comment_id}` on {stage_input.jira}.\n\n{summary}",
        )

    class PersistIntentInput(StageInput):
        """Approved Nautobot drain mutation input."""

        intent: DrainIntent

    class PersistIntentOutput(StageOutput):
        """Persisted Nautobot status."""

        status: str

    @stage_executor("persist")
    async def persist_intent(self, stage_input: PersistIntentInput) -> PersistIntentOutput:
        """Persist only after the proposal has been approved."""
        await workflow.execute_activity(
            set_interface_status,
            SetInterfaceStatusInput(
                interface_ids=[stage_input.intent.interface.id],
                status=MAINTENANCE_STATUS,
                isis_metric=MOCK_DRAIN_METRIC,
                expected_status=stage_input.intent.interface.status,
                expected_isis_metric=stage_input.intent.interface.isis_metric,
            ),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        return self.PersistIntentOutput(
            status=MAINTENANCE_STATUS,
            display=(
                f"Persisted **{MAINTENANCE_STATUS}** and IS-IS metric "
                f"`{MOCK_DRAIN_METRIC}` for "
                f"`{stage_input.intent.device_name}:{stage_input.intent.interface.name}`."
            ),
        )

    class PreflightInput(StageInput):
        """Approved artifacts and original intent for a fresh non-persistent check."""

        intent: DrainIntent
        approved_configuration: str
        approved_candidate_diff: str

    class PreflightOutput(StageOutput):
        """Fresh artifacts and whether they still match the approved plan."""

        matched: bool
        reason: str | None
        render_diff: str
        fresh_candidate_diff: str

    @stage_executor("preflight")
    async def preflight_approved_plan(self, stage_input: PreflightInput) -> PreflightOutput:
        """Rebuild and re-diff the proposal without changing Nautobot or Config Store."""
        fresh_intent = await workflow.execute_activity(
            resolve_drain_intent,
            DrainLookupInput(
                device=stage_input.intent.device_id,
                port=stage_input.intent.interface.name,
            ),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        device_result = await workflow.execute_activity(
            get_network_device,
            GetNetworkDeviceInput(device_id=fresh_intent.device_id),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        fresh_proposal = await workflow.execute_activity(
            render_proposed_interface_intent,
            ProposedInterfaceRenderInput(
                device_id=fresh_intent.device_id,
                interface_name=fresh_intent.interface.name,
                status=MAINTENANCE_STATUS,
                isis_metric=MOCK_DRAIN_METRIC,
                filename=DRAIN_CONFIG_FILE,
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        fresh_candidate = await workflow.execute_activity(
            perform_drain_candidate_diff,
            DrainCandidateInput(
                device_data=device_result.device,
                configuration=fresh_proposal.configuration,
                interface_name=fresh_intent.interface.name,
                current_metric=fresh_intent.interface.isis_metric,
            ),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        comparison = DrainPreflightComparison.from_artifacts(
            approved_configuration=stage_input.approved_configuration,
            fresh_configuration=fresh_proposal.configuration,
            approved_candidate_diff=stage_input.approved_candidate_diff,
            fresh_candidate_diff=fresh_candidate.diff,
        )
        if comparison.matched:
            display = "Still matches the approved plan. Nothing changed yet."
        else:
            render_details = (
                f"\n\n#### Render drift\n\n{_markdown_diff(comparison.render_diff)}"
                if comparison.render_diff
                else ""
            )
            display = (
                f"**BLOCKED** - {comparison.reason}. No changes made.\n\n\n"
                "#### Approved\n\n"
                f"{_markdown_diff(stage_input.approved_candidate_diff)}"
                "\n\n#### Current\n\n"
                f"{_markdown_diff(comparison.fresh_candidate_diff)}"
                f"{render_details}"
            )
        return self.PreflightOutput(
            matched=comparison.matched,
            reason=comparison.reason,
            render_diff=comparison.render_diff,
            fresh_candidate_diff=comparison.fresh_candidate_diff,
            display=display,
        )

    class RenderInput(StageInput):
        """Approved proposal verification input."""

        device_id: str
        proposed_configuration: str

    class RenderOutput(StageOutput):
        """Pinned post-mutation intended configuration."""

        device: NetworkDeviceData
        intended_config: str
        config_url: str

    @stage_executor("render")
    async def render_intended_configuration(self, stage_input: RenderInput) -> RenderOutput:
        """Render persisted intent and reject drift from the approved proposal."""
        device_result = await workflow.execute_activity(
            get_network_device,
            GetNetworkDeviceInput(device_id=stage_input.device_id),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        DeviceMixin.attach_device_search_attributes(device_result.device)
        render_result = await workflow.execute_activity(
            execute_render,
            ExecuteRenderInput(
                device_id=stage_input.device_id,
                workflow_id=workflow.info().workflow_id,
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        config_file = DRAIN_CONFIG_FILE
        commit_id = render_result.get_commit(config_file)
        if commit_id is None:
            raise ApplicationError(
                f"Fresh render did not produce {config_file!r} for {device_result.device.name}",
                non_retryable=True,
            )
        intended_config, _, config_url = await workflow.execute_activity(
            load_partial_configuration,
            LoadPartialConfigurationActivityInput(
                device_data=device_result.device,
                config_file=config_file,
                commit_id=commit_id,
            ),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        _verify_execution_render(stage_input.proposed_configuration, intended_config)
        return self.RenderOutput(
            device=device_result.device,
            intended_config=intended_config,
            config_url=config_url,
            display=f"[{config_file}]({config_url}) matches the approved plan.",
        )

    class ReviewDiffInput(StageInput):
        """Proposed candidate review input."""

        diff: str
        mocked: bool

    class ReviewDiffOutput(StageOutput):
        """Rendered-to-device candidate diff decision."""

        approved: bool
        reviewers: list[str]
        diff: str

    @stage_executor("review")
    async def review_configuration_diff(self, stage_input: ReviewDiffInput) -> ReviewDiffOutput:
        """Gate the already-recorded non-persistent candidate diff."""
        stage_name = "review"
        if not stage_input.diff.strip():
            self.get_stage_by_name(stage_name).requires_approval = False
            return self.ReviewDiffOutput(
                approved=False,
                reviewers=[],
                diff="",
                display=(
                    "No diff between the fresh post-Maintenance render and the device "
                    "configuration; no apply is required."
                ),
            )
        approved, reviewers = await self.wait_for_review(
            stage_name, stage_input.diff, mocked=stage_input.mocked
        )
        decision = "Approved" if approved else "Rejected"
        return self.ReviewDiffOutput(
            approved=approved,
            reviewers=reviewers,
            diff=stage_input.diff,
            display=(
                f"Rendered configuration diff {decision.lower()} by "
                f"{', '.join(reviewers)}\n\n{_markdown_diff(stage_input.diff)}"
            ),
        )

    class ApplyInput(StageInput):
        """Approved rendered configuration input."""

        device: NetworkDeviceData
        intended_config: str
        interface_name: str
        current_metric: int
        approved_diff: str

    class ApplyOutput(StageOutput):
        """Rendered configuration application output."""

    @stage_executor("apply")
    async def apply_drain(self, stage_input: ApplyInput) -> ApplyOutput:
        """Apply through the same guarded activity used by standard deploy workflows."""
        result = await workflow.execute_activity(
            apply_drain_candidate,
            DrainApplyInput(
                device_data=stage_input.device,
                configuration=stage_input.intended_config,
                interface_name=stage_input.interface_name,
                current_metric=stage_input.current_metric,
                approved_diff=stage_input.approved_diff,
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        return self.ApplyOutput(
            display=(
                "Approved rendered interfaces configuration applied successfully."
                if not result.mocked
                else "Mock device accepted the approved Junos interfaces candidate."
            ),
        )

    class ValidateDrainInput(StageInput):
        """Expected drain state after the approved deployment."""

        device_name: str
        interface_name: str

    class ValidateDrainOutput(StageOutput):
        """Mocked post-deployment drain observation."""

        healthy: bool

    @stage_executor("validate")
    async def validate_drain(self, stage_input: ValidateDrainInput) -> ValidateDrainOutput:
        """Verify the device reports the rendered maintenance metric."""
        result = await workflow.execute_activity(
            mock_validate_applied_intent,
            MockAppliedIntentInput(
                phase="drain",
                device=stage_input.device_name,
                lag_name=stage_input.interface_name,
                igp_metric=1_000_000,
            ),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        return self.ValidateDrainOutput(
            healthy=result.healthy,
            display="MOCK validation passed: " + "; ".join(result.observations),
        )

    class AuditInput(StageInput):
        """Drain audit input."""

        jira: str | None
        intent: DrainIntent
        decision: str
        reviewers: list[str]
        diff: str
        config_url: str | None
        workflow_url: str
        failure_reason: str | None = None
        fresh_candidate_diff: str | None = None
        render_diff: str | None = None

    class AuditOutput(StageOutput):
        """Drain audit output."""

        comment_id: str | None

    @stage_executor("audit")
    async def record_audit(self, stage_input: AuditInput) -> AuditOutput:
        """Write the review result to Jira when requested."""
        target = f"{stage_input.intent.device_name}:{stage_input.intent.interface.name}"
        reviewers = ", ".join(stage_input.reviewers) or "none"
        persisted = stage_input.config_url is not None
        nautobot_status = MAINTENANCE_STATUS if persisted else "unchanged"
        blocked_prefix = "blocked: "
        if stage_input.decision.startswith(blocked_prefix):
            blocked_reason = stage_input.decision.removeprefix(blocked_prefix)
            result_md = f"**BLOCKED** - {blocked_reason}"
            result_jira = f"*BLOCKED* - {blocked_reason}"
        else:
            result_md = stage_input.decision
            result_jira = stage_input.decision
        failure_summary = ""
        failure_jira = ""
        if stage_input.failure_reason:
            fresh_diff = stage_input.fresh_candidate_diff or "(none)"
            render_diff = stage_input.render_diff or "(render matched)"
            failure_summary = (
                f"\n\n\n#### Current candidate diff\n\n{_markdown_diff(fresh_diff)}\n\n"
                f"#### Render drift\n\n{_markdown_diff(render_diff)}"
            )
            failure_jira = (
                f"\n\nh4. Current candidate diff\n{_jira_diff(fresh_diff)}\n\n"
                f"h4. Render drift\n{_jira_diff(render_diff)}"
            )
        rendered_summary = (
            f"- **Rendered config:** [{DRAIN_CONFIG_FILE}]({stage_input.config_url})\n"
            if persisted
            else ""
        )
        rendered_jira = (
            f"*Rendered config:* [{DRAIN_CONFIG_FILE}|{stage_input.config_url}]\n"
            if persisted
            else ""
        )
        summary = (
            "\n### Interface drain\n\n"
            f"- **Workflow:** [Open workflow]({stage_input.workflow_url})\n"
            f"- **Target:** `{target}`\n"
            f"- **Nautobot status:** {nautobot_status}\n"
            f"{rendered_summary}"
            f"- **Reviewers:** {reviewers}\n"
            f"- **Result:** {result_md}\n\n\n"
            "#### Candidate diff\n\n"
            f"{_markdown_diff(stage_input.diff)}"
            f"{failure_summary}"
        )
        jira_body = (
            "h3. BB sandbox interface drain\n\n"
            f"*Workflow:* [Open workflow|{stage_input.workflow_url}]\n"
            f"*Target:* {target}\n"
            f"*Nautobot status:* {nautobot_status}\n"
            f"{rendered_jira}"
            f"*Reviewers:* {reviewers}\n"
            f"*Result:* {result_jira}\n\n"
            "h4. Candidate diff\n"
            f"{_jira_diff(stage_input.diff)}"
            f"{failure_jira}"
        )
        if not stage_input.jira:
            return self.AuditOutput(comment_id=None, display=summary)
        result = await workflow.execute_activity(
            add_ticket_comment,
            AddCommentInput(
                ticketing_platform="jira",
                issue_key=stage_input.jira,
                body=jira_body,
            ),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        return self.AuditOutput(
            comment_id=result.comment_id,
            display=(
                f"Recorded audit comment `{result.comment_id}` on {stage_input.jira}.\n\n{summary}"
            ),
        )

    @run_nv_config_manager_workflow
    async def run(self, workflow_input: DrainInterfaceInput) -> DrainInterfaceOutput:  # type: ignore[override, ty:invalid-method-override]
        """Execute the interface drain demonstration."""
        self.set_input(workflow_input)
        if workflow_input.jira:
            workflow.upsert_search_attributes({ISSUE_KEY_SEARCH_ATTRIBUTE: [workflow_input.jira]})
        resolved = await self.resolve(
            self.ResolveInput(
                device=workflow_input.device,
                port=workflow_input.port,
                jira=workflow_input.jira,
            )
        )
        planned = await self.plan_proposed_configuration(self.PlanInput(intent=resolved.intent))
        workflow_url = await self.workflow_url()
        await self.record_change_plan(
            self.RecordPlanInput(
                jira=workflow_input.jira,
                intent=resolved.intent,
                diff=planned.diff,
                workflow_url=workflow_url,
            )
        )
        reviewed = await self.review_configuration_diff(
            self.ReviewDiffInput(
                diff=planned.diff,
                mocked=planned.mocked,
            )
        )
        config_url: str | None = None
        status = resolved.intent.interface.status
        failure_reason: str | None = None
        fresh_candidate_diff: str | None = None
        render_diff: str | None = None
        if reviewed.diff and reviewed.approved:
            preflight = await self.preflight_approved_plan(
                self.PreflightInput(
                    intent=resolved.intent,
                    approved_configuration=planned.intended_config,
                    approved_candidate_diff=reviewed.diff,
                )
            )
            if preflight.matched:
                await self.persist_intent(self.PersistIntentInput(intent=resolved.intent))
                rendered = await self.render_intended_configuration(
                    self.RenderInput(
                        device_id=resolved.intent.device_id,
                        proposed_configuration=planned.intended_config,
                    )
                )
                config_url = rendered.config_url
                status = MAINTENANCE_STATUS
                await self.apply_drain(
                    self.ApplyInput(
                        device=rendered.device,
                        intended_config=rendered.intended_config,
                        interface_name=resolved.intent.interface.name,
                        current_metric=resolved.intent.interface.isis_metric,
                        approved_diff=reviewed.diff,
                    )
                )
                await self.validate_drain(
                    self.ValidateDrainInput(
                        device_name=resolved.intent.device_name,
                        interface_name=resolved.intent.interface.name,
                    )
                )
                decision = "applied to device"
            else:
                self.set_stage_state("persist", StateEnum.UNREACHABLE)
                self.set_stage_state("render", StateEnum.UNREACHABLE)
                self.set_stage_state("apply", StateEnum.UNREACHABLE)
                self.set_stage_state("validate", StateEnum.UNREACHABLE)
                failure_reason = preflight.reason
                fresh_candidate_diff = preflight.fresh_candidate_diff
                render_diff = preflight.render_diff
                decision = f"blocked: {preflight.reason}"
        elif reviewed.diff:
            self.set_stage_state("preflight", StateEnum.UNREACHABLE)
            self.set_stage_state("persist", StateEnum.UNREACHABLE)
            self.set_stage_state("render", StateEnum.UNREACHABLE)
            self.set_stage_state("apply", StateEnum.UNREACHABLE)
            self.set_stage_state("validate", StateEnum.UNREACHABLE)
            decision = "rejected; nothing changed"
        else:
            self.set_stage_state("preflight", StateEnum.UNREACHABLE)
            self.set_stage_state("persist", StateEnum.UNREACHABLE)
            self.set_stage_state("render", StateEnum.UNREACHABLE)
            self.set_stage_state("apply", StateEnum.UNREACHABLE)
            self.set_stage_state("validate", StateEnum.UNREACHABLE)
            decision = "no device diff; nothing changed"
        await self.record_audit(
            self.AuditInput(
                jira=workflow_input.jira,
                intent=resolved.intent,
                decision=decision,
                reviewers=reviewed.reviewers,
                diff=reviewed.diff,
                config_url=config_url,
                workflow_url=workflow_url,
                failure_reason=failure_reason,
                fresh_candidate_diff=fresh_candidate_diff,
                render_diff=render_diff,
            )
        )
        return DrainInterfaceOutput(
            applied=bool(reviewed.diff and reviewed.approved and config_url),
            device=resolved.intent.device_name,
            port=resolved.intent.interface.name,
            status=status,
            approvers=reviewed.reviewers,
            jira=workflow_input.jira,
        )


@workflow.defn
class BBInternalBackboneBringupWorkflow(WorkflowMetadataMixin, _ApprovalMixin):
    """Persist and activate a two-ended internal Backbone circuit in stages."""

    workflow_name = "BB Sandbox: Internal Backbone Bringup"
    workflow_description = (
        "Write two-ended Backbone intent to Nautobot with approved, mocked device interactions"
    )
    workflow_input_class = InternalBackboneBringupInput
    workflow_api_endpoint = "/bb_sandbox/internal_backbone_bringup"
    workflow_namespace = "bb_sandbox"
    workflow_lock = WorkflowLockSpec(key_fields=["circuit_id"])

    def __init__(self) -> None:
        """Define the staged internal circuit demonstration."""
        StageMixin.__init__(self)
        self.define_stage(
            name="resolve_circuit",
            description="Validate Jira and both Nautobot endpoints.",
            requires_approval=False,
            depends_on=[],
        )
        self.define_stage(
            name="write_physical_intent",
            description="Write physical intent to Nautobot and render both routers.",
            requires_approval=False,
            depends_on=["resolve_circuit"],
        )
        for endpoint in ("router_a", "router_z"):
            self.define_stage(
                name=f"physical_{endpoint}",
                description=f"Approve and deploy physical configuration on {endpoint.replace('_', ' ').title()}.",
                requires_approval=True,
                approval_threshold=1,
                depends_on=["write_physical_intent"],
            )
        self.define_stage(
            name="validate_neighbors",
            description="Validate bidirectional LLDP after the physical deployment.",
            requires_approval=False,
            depends_on=["physical_router_a", "physical_router_z"],
        )
        self.define_stage(
            name="write_addressing_intent",
            description="Write dual-stack intent to Nautobot and render both routers.",
            requires_approval=False,
            depends_on=["validate_neighbors"],
        )
        for endpoint in ("router_a", "router_z"):
            self.define_stage(
                name=f"addressing_{endpoint}",
                description=f"Approve and deploy addressing on {endpoint.replace('_', ' ').title()}.",
                requires_approval=True,
                approval_threshold=1,
                depends_on=["write_addressing_intent"],
            )
        self.define_stage(
            name="validate_rtt",
            description="Validate point-to-point reachability and expected RTT after deployment.",
            requires_approval=False,
            depends_on=["addressing_router_a", "addressing_router_z"],
        )
        self.define_stage(
            name="write_routing_intent",
            description="Create IS-IS intent in Nautobot and render both routers.",
            requires_approval=False,
            depends_on=["validate_rtt"],
        )
        for endpoint in ("router_a", "router_z"):
            self.define_stage(
                name=f"routing_{endpoint}",
                description=f"Approve and deploy routing on {endpoint.replace('_', ' ').title()}.",
                requires_approval=True,
                approval_threshold=1,
                depends_on=["write_routing_intent"],
            )
        self.define_stage(
            name="validate_routing",
            description="Validate routing protocols and iBGP reachability after deployment.",
            requires_approval=False,
            depends_on=["routing_router_a", "routing_router_z"],
        )
        self.define_stage(
            name="record_jira_audit",
            description="Record all diffs, decisions, and reviewers on Jira.",
            requires_approval=False,
            depends_on=["validate_routing"],
        )

    class ResolveInput(StageInput):
        """Internal circuit resolution input."""

        request: InternalBackboneBringupInput
        igp_metric: int

    class ResolveOutput(StageOutput):
        """Resolved two-ended circuit intent."""

        intent: InternalBackboneIntent

    @stage_executor("resolve_circuit")
    async def resolve(self, stage_input: ResolveInput) -> ResolveOutput:
        """Validate Jira and resolve all native Nautobot targets."""
        request = stage_input.request
        ticket = await workflow.execute_activity(
            validate_ticket,
            ValidateTicketInput(ticketing_platform="jira", issue_key=request.jira),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        intent = await workflow.execute_activity(
            resolve_internal_backbone_intent,
            InternalBackboneLookupInput(
                circuit_id=request.circuit_id,
                local_device=request.local_device,
                local_ports=request.local_ports,
                remote_device=request.remote_device,
                remote_ports=request.remote_ports,
                lag_name=request.lag_name,
                ipv4_prefix=request.ipv4_prefix,
                ipv6_prefix=request.ipv6_prefix,
                igp_metric=stage_input.igp_metric,
                minimum_links=request.minimum_links,
            ),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        return self.ResolveOutput(
            intent=intent,
            display=(
                f"Jira [{request.jira}]({ticket.url}): {ticket.summary}\n\n"
                f"Resolved `{intent.local.device_name}:{intent.local.lag.name}` ↔ "
                f"`{intent.remote.device_name}:{intent.remote.lag.name}` on circuit "
                f"`{intent.circuit_id}`. Metric `{intent.igp_metric}` will be stored in Nautobot."
            ),
        )

    class ChangeInput(StageInput):
        """Common turnup phase input."""

        intent: InternalBackboneIntent
        expected_rtt_ms: float
        jira: str
        requested_by: str | None

    class ChangeOutput(StageOutput):
        """Common turnup phase output."""

        approved: bool
        reviewers: list[str]
        diff: str
        average_rtt_ms: float | None = None
        local_lag_id: str | None = None
        remote_lag_id: str | None = None

    class PreparedChangeOutput(StageOutput):
        """Two-ended Nautobot mutation followed by two endpoint renders."""

        intent: InternalBackboneIntent
        router_a: BringupEndpointRender
        router_z: BringupEndpointRender

    class EndpointDeployInput(StageInput):
        """Pinned rendered delta awaiting one endpoint's approval."""

        phase: str
        rendered: BringupEndpointRender

    class EndpointDeployOutput(StageOutput):
        """One endpoint's independent approval and mock deployment."""

        approved: bool
        reviewers: list[str]
        diff: str

    class ValidationOutput(StageOutput):
        """Post-deployment validation result kept separate from approval history."""

        healthy: bool
        average_rtt_ms: float | None = None

    async def _render_endpoint(self, device_id: str) -> BringupEndpointRender:
        """Render one device and load its actual delta from the previous revision."""
        device_result = await workflow.execute_activity(
            get_network_device,
            GetNetworkDeviceInput(device_id=device_id),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        render_result = await workflow.execute_activity(
            execute_render,
            ExecuteRenderInput(device_id=device_id, workflow_id=workflow.info().workflow_id),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        commit_id = next(
            (
                file_commit.commit
                for file_commit in render_result.updated_files
                if file_commit.filename == DRAIN_CONFIG_FILE
            ),
            None,
        )
        if commit_id is None:
            raise ApplicationError(
                f"Nautobot mutation did not change rendered {DRAIN_CONFIG_FILE!r} "
                f"for {device_result.device.name}",
                non_retryable=True,
            )
        _, loaded_commit, config_url = await workflow.execute_activity(
            load_partial_configuration,
            LoadPartialConfigurationActivityInput(
                device_data=device_result.device,
                config_file=DRAIN_CONFIG_FILE,
                commit_id=commit_id,
            ),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        revision_diff = await workflow.execute_activity(
            load_render_revision_diff,
            RenderRevisionDiffInput(
                device_id=device_id,
                device_name=device_result.device.name,
                filename=DRAIN_CONFIG_FILE,
                to_version=int(loaded_commit),
            ),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        return BringupEndpointRender(
            device_name=device_result.device.name,
            device_id=device_id,
            commit_id=loaded_commit,
            config_url=config_url,
            diff=revision_diff.diff,
        )

    async def _render_both(
        self, intent: InternalBackboneIntent
    ) -> tuple[BringupEndpointRender, BringupEndpointRender]:
        """Render router A and router Z concurrently."""
        router_a, router_z = await asyncio.gather(
            self._render_endpoint(intent.local.device_id),
            self._render_endpoint(intent.remote.device_id),
        )
        return router_a, router_z

    @stage_executor("write_physical_intent")
    async def physical(self, stage_input: ChangeInput) -> PreparedChangeOutput:
        """Persist both LAG/member relationships, then render both routers."""
        intent = stage_input.intent
        mutation = await workflow.execute_activity(
            enable_backbone_interfaces,
            EnableBackboneInterfacesInput(
                local_interface_ids=[interface.id for interface in intent.local.interfaces],
                local_device_id=intent.local.device_id,
                local_lag_name=intent.local.lag.name,
                local_remote_device=intent.remote.device_name,
                remote_interface_ids=[interface.id for interface in intent.remote.interfaces],
                remote_device_id=intent.remote.device_id,
                remote_lag_name=intent.remote.lag.name,
                remote_remote_device=intent.local.device_name,
                minimum_links=intent.minimum_links,
            ),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        updated_intent = intent.model_copy(
            update={
                "local": intent.local.model_copy(
                    update={
                        "lag": intent.local.lag.model_copy(update={"id": mutation.local_lag_id})
                    }
                ),
                "remote": intent.remote.model_copy(
                    update={
                        "lag": intent.remote.lag.model_copy(update={"id": mutation.remote_lag_id})
                    }
                ),
            }
        )
        router_a, router_z = await self._render_both(updated_intent)
        return self.PreparedChangeOutput(
            intent=updated_intent,
            router_a=router_a,
            router_z=router_z,
            display=(
                "Physical intent written to Nautobot. Rendered "
                f"[{router_a.device_name}]({router_a.config_url}) at `{router_a.commit_id}` and "
                f"[{router_z.device_name}]({router_z.config_url}) at `{router_z.commit_id}`."
            ),
        )

    async def _deploy_endpoint(
        self, stage_name: str, stage_input: EndpointDeployInput
    ) -> EndpointDeployOutput:
        """Independently approve and mock-deploy one router's pinned rendered delta."""
        approved, reviewers = await self.wait_for_review(
            stage_name, stage_input.rendered.diff, mocked=False
        )
        if not approved:
            return self.EndpointDeployOutput(
                approved=False,
                reviewers=reviewers,
                diff=stage_input.rendered.diff,
                display=f"Rejected by {', '.join(reviewers)}; device was not changed.",
            )
        applied = await workflow.execute_activity(
            mock_apply_candidate,
            MockDiffInput(
                phase=stage_input.phase, device=stage_input.rendered.device_name, ports=[]
            ),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        return self.EndpointDeployOutput(
            approved=True,
            reviewers=reviewers,
            diff=stage_input.rendered.diff,
            display=(
                f"Approved by {', '.join(reviewers)}. {applied}\n\n"
                f"Pinned render config ID: `{stage_input.rendered.commit_id}`\n\n"
                f"#### Approved rendered diff\n\n{_markdown_diff(stage_input.rendered.diff)}"
            ),
        )

    @stage_executor("physical_router_a")
    async def physical_router_a(self, stage_input: EndpointDeployInput) -> EndpointDeployOutput:
        """Approve and deploy router A physical intent."""
        return await self._deploy_endpoint("physical_router_a", stage_input)

    @stage_executor("physical_router_z")
    async def physical_router_z(self, stage_input: EndpointDeployInput) -> EndpointDeployOutput:
        """Approve and deploy router Z physical intent."""
        return await self._deploy_endpoint("physical_router_z", stage_input)

    @stage_executor("validate_neighbors")
    async def validate_neighbors(self, stage_input: ChangeInput) -> ValidationOutput:
        """Validate LAG membership and bidirectional LLDP after physical deployment."""
        intent = stage_input.intent
        applied_checks = await asyncio.gather(
            *(
                workflow.execute_activity(
                    mock_validate_applied_intent,
                    MockAppliedIntentInput(
                        phase="physical",
                        device=endpoint.device_name,
                        lag_name=endpoint.lag.name,
                        member_ports=[interface.name for interface in endpoint.interfaces],
                    ),
                    start_to_close_timeout=ACTIVITY_TIMEOUT,
                    retry_policy=ACTIVITY_RETRY_POLICY,
                )
                for endpoint in (intent.local, intent.remote)
            )
        )
        observations = []
        for endpoint, expected in (
            (intent.local, intent.remote.device_name),
            (intent.remote, intent.local.device_name),
        ):
            observations.append(
                await workflow.execute_activity(
                    mock_validate_neighbor,
                    MockNeighborInput(
                        device=endpoint.device_name,
                        ports=[interface.name for interface in endpoint.interfaces],
                        expected_neighbor=expected,
                    ),
                    start_to_close_timeout=ACTIVITY_TIMEOUT,
                    retry_policy=ACTIVITY_RETRY_POLICY,
                )
            )
        if not all(observation.matched for observation in observations):
            raise ApplicationError("Mock bidirectional LLDP validation failed")
        return self.ValidationOutput(
            healthy=True,
            display=(
                "MOCK physical and LLDP validation passed in both directions.\n\n"
                + "\n".join(
                    f"- {observation}"
                    for check in applied_checks
                    for observation in check.observations
                )
                + "\n"
                f"- `{intent.local.device_name}` observed `{intent.remote.device_name}`\n"
                f"- `{intent.remote.device_name}` observed `{intent.local.device_name}`"
            ),
        )

    @stage_executor("write_addressing_intent")
    async def addressing(self, stage_input: ChangeInput) -> PreparedChangeOutput:
        """Persist dual-stack addressing, then render both routers."""
        intent = stage_input.intent
        await workflow.execute_activity(
            apply_backbone_addressing,
            ApplyBackboneAddressingInput(
                circuit_uuid=intent.circuit_uuid,
                local_lag_id=intent.local.lag.id,
                remote_lag_id=intent.remote.lag.id,
                local_ipv4=intent.local.ipv4_address,
                remote_ipv4=intent.remote.ipv4_address,
                local_ipv6=intent.local.ipv6_address,
                remote_ipv6=intent.remote.ipv6_address,
                expected_rtt_ms=stage_input.expected_rtt_ms,
                jira=stage_input.jira,
                requested_by=stage_input.requested_by,
            ),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        router_a, router_z = await self._render_both(intent)
        return self.PreparedChangeOutput(
            intent=intent,
            router_a=router_a,
            router_z=router_z,
            display=(
                "Nautobot now owns both prefixes, assignments, expected RTT, and Jira. "
                f"Rendered `{router_a.device_name}` at `{router_a.commit_id}` and "
                f"`{router_z.device_name}` at `{router_z.commit_id}`."
            ),
        )

    @stage_executor("addressing_router_a")
    async def addressing_router_a(self, stage_input: EndpointDeployInput) -> EndpointDeployOutput:
        """Approve and deploy router A addressing intent."""
        return await self._deploy_endpoint("addressing_router_a", stage_input)

    @stage_executor("addressing_router_z")
    async def addressing_router_z(self, stage_input: EndpointDeployInput) -> EndpointDeployOutput:
        """Approve and deploy router Z addressing intent."""
        return await self._deploy_endpoint("addressing_router_z", stage_input)

    @stage_executor("validate_rtt")
    async def validate_rtt(self, stage_input: ChangeInput) -> ValidationOutput:
        """Validate applied dual-stack addresses and point-to-point RTT."""
        intent = stage_input.intent
        address_checks = await asyncio.gather(
            *(
                workflow.execute_activity(
                    mock_validate_applied_intent,
                    MockAppliedIntentInput(
                        phase="addressing",
                        device=endpoint.device_name,
                        lag_name=endpoint.lag.name,
                        ipv4_address=endpoint.ipv4_address,
                        ipv6_address=endpoint.ipv6_address,
                    ),
                    start_to_close_timeout=ACTIVITY_TIMEOUT,
                    retry_policy=ACTIVITY_RETRY_POLICY,
                )
                for endpoint in (intent.local, intent.remote)
            )
        )
        ping = await workflow.execute_activity(
            mock_ping_rtt,
            MockPingInput(
                source=intent.local.ipv4_address,
                destination=intent.remote.ipv4_address,
                expected_rtt_ms=stage_input.expected_rtt_ms,
            ),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        if not ping.healthy:
            raise ApplicationError(
                f"Mock RTT {ping.average_rtt_ms}ms exceeds {stage_input.expected_rtt_ms}ms"
            )
        return self.ValidationOutput(
            healthy=True,
            average_rtt_ms=ping.average_rtt_ms,
            display=(
                "MOCK dual-stack intent validation passed: "
                + "; ".join(
                    observation for check in address_checks for observation in check.observations
                )
                + f". RTT: {ping.received}/{ping.transmitted} replies, "
                f"average **{ping.average_rtt_ms} ms**, allowed maximum "
                f"**{stage_input.expected_rtt_ms} ms**."
            ),
        )

    @stage_executor("write_routing_intent")
    async def routing(self, stage_input: ChangeInput) -> PreparedChangeOutput:
        """Persist explicit IS-IS intent, then render both routers."""
        intent = stage_input.intent
        await workflow.execute_activity(
            activate_backbone_routing,
            ActivateBackboneRoutingInput(
                interface_ids=[intent.local.lag.id, intent.remote.lag.id],
                igp_metric=intent.igp_metric,
            ),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        router_a, router_z = await self._render_both(intent)
        return self.PreparedChangeOutput(
            intent=intent,
            router_a=router_a,
            router_z=router_z,
            display=(
                f"Stored IS-IS metric `{intent.igp_metric}` on both LAGs in Nautobot. "
                f"Rendered `{router_a.device_name}` at `{router_a.commit_id}` and "
                f"`{router_z.device_name}` at `{router_z.commit_id}`."
            ),
        )

    @stage_executor("routing_router_a")
    async def routing_router_a(self, stage_input: EndpointDeployInput) -> EndpointDeployOutput:
        """Approve and deploy router A routing intent."""
        return await self._deploy_endpoint("routing_router_a", stage_input)

    @stage_executor("routing_router_z")
    async def routing_router_z(self, stage_input: EndpointDeployInput) -> EndpointDeployOutput:
        """Approve and deploy router Z routing intent."""
        return await self._deploy_endpoint("routing_router_z", stage_input)

    @stage_executor("validate_routing")
    async def validate_routing(self, stage_input: ChangeInput) -> ValidationOutput:
        """Validate routing protocols and reachability after routing deployment."""
        intent = stage_input.intent
        applied_checks = await asyncio.gather(
            *(
                workflow.execute_activity(
                    mock_validate_applied_intent,
                    MockAppliedIntentInput(
                        phase="routing",
                        device=endpoint.device_name,
                        lag_name=endpoint.lag.name,
                        igp_metric=intent.igp_metric,
                    ),
                    start_to_close_timeout=ACTIVITY_TIMEOUT,
                    retry_policy=ACTIVITY_RETRY_POLICY,
                )
                for endpoint in (intent.local, intent.remote)
            )
        )
        health = await workflow.execute_activity(
            mock_validate_routing,
            MockRoutingInput(
                device=intent.local.device_name,
                lag_name=intent.local.lag.name,
                remote_device=intent.remote.device_name,
            ),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        return self.ValidationOutput(
            healthy=True,
            display=(
                "MOCK rendered routing intent matched on both routers: "
                + "; ".join(
                    observation for check in applied_checks for observation in check.observations
                )
                + f". Protocol health: IS-IS **{health.igp_state}**, "
                f"MPLS **{health.mpls_state}**, RSVP **{health.rsvp_state}**, "
                f"iBGP **{health.ibgp_reachability}**."
            ),
        )

    class AuditInput(StageInput):
        """Internal Backbone Jira audit input."""

        jira: str
        intent: InternalBackboneIntent
        outcome: str
        reviews: dict[str, list[str]]
        diffs: dict[str, str]
        average_rtt_ms: float | None
        workflow_url: str

    class AuditOutput(StageOutput):
        """Jira audit output."""

        comment_id: str

    @stage_executor("record_jira_audit")
    async def record_audit(self, stage_input: AuditInput) -> AuditOutput:
        """Record the entire staged decision trail on Jira."""
        markdown_review_lines = "\n".join(
            f"- **{phase.title()}:** {', '.join(reviewers) or 'not reached'}"
            for phase, reviewers in stage_input.reviews.items()
        )
        jira_review_lines = "\n".join(
            f"* *{phase.title()}:* {', '.join(reviewers) or 'not reached'}"
            for phase, reviewers in stage_input.reviews.items()
        )
        markdown_diff_blocks = "\n\n".join(
            f"#### {phase.title()}\n\n{_markdown_diff(diff)}"
            for phase, diff in stage_input.diffs.items()
        )
        jira_diff_blocks = "\n\n".join(
            f"h4. {phase.title()}\n{_jira_diff(diff)}" for phase, diff in stage_input.diffs.items()
        )
        intent = stage_input.intent
        endpoints = (
            f"{intent.local.device_name}:{intent.local.lag.name} ↔ "
            f"{intent.remote.device_name}:{intent.remote.lag.name}"
        )
        rtt = stage_input.average_rtt_ms if stage_input.average_rtt_ms is not None else "not run"
        summary = (
            "### Internal Backbone bringup summary\n\n"
            f"- **Workflow:** [Open workflow]({stage_input.workflow_url})\n"
            f"- **Outcome:** {stage_input.outcome}\n"
            f"- **Circuit:** `{intent.circuit_id}`\n"
            f"- **Endpoints:** {endpoints}\n"
            f"- **Prefixes:** `{intent.ipv4_prefix}`, `{intent.ipv6_prefix}`\n"
            f"- **Stored IS-IS metric:** `{intent.igp_metric}`\n"
            f"- **Mock RTT:** {rtt} ms\n\n"
            f"#### Reviews\n\n{markdown_review_lines}\n\n"
            f"### Candidate diffs\n\n{markdown_diff_blocks}"
        )
        jira_body = (
            "h3. BB sandbox internal Backbone bringup\n\n"
            f"*Workflow:* [Open workflow|{stage_input.workflow_url}]\n"
            f"*Outcome:* {stage_input.outcome}\n"
            f"*Circuit:* {intent.circuit_id}\n"
            f"*Endpoints:* {endpoints}\n"
            f"*Prefixes:* {intent.ipv4_prefix}, {intent.ipv6_prefix}\n"
            f"*Stored IS-IS metric:* {intent.igp_metric}\n"
            f"*Mock RTT:* {rtt} ms\n"
            "*Device interaction:* MOCKED\n\n"
            f"h4. Reviews\n{jira_review_lines}\n\n"
            f"h3. Candidate diffs\n{jira_diff_blocks}"
        )
        result = await workflow.execute_activity(
            add_ticket_comment,
            AddCommentInput(
                ticketing_platform="jira",
                issue_key=stage_input.jira,
                body=jira_body,
            ),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        return self.AuditOutput(
            comment_id=result.comment_id,
            display=(
                f"Recorded staged audit comment `{result.comment_id}` on {stage_input.jira}.\n\n"
                f"{summary}"
            ),
        )

    def _unreachable_after(self, stage_name: str) -> None:
        """Mark later change stages unreachable after rejection."""
        order = [
            "write_physical_intent",
            "physical_router_a",
            "physical_router_z",
            "validate_neighbors",
            "write_addressing_intent",
            "addressing_router_a",
            "addressing_router_z",
            "validate_rtt",
            "write_routing_intent",
            "routing_router_a",
            "routing_router_z",
            "validate_routing",
        ]
        for later in order[order.index(stage_name) + 1 :]:
            self.set_stage_state(later, StateEnum.UNREACHABLE)

    @run_nv_config_manager_workflow
    async def run(
        self, workflow_input: InternalBackboneBringupInput
    ) -> InternalBackboneBringupOutput:  # type: ignore[override, ty:invalid-method-override]
        """Execute the staged internal Backbone turnup."""
        self.set_input(workflow_input)
        workflow.upsert_search_attributes({ISSUE_KEY_SEARCH_ATTRIBUTE: [workflow_input.jira]})
        metric = workflow_input.selected_igp_metric()
        resolved = await self.resolve(self.ResolveInput(request=workflow_input, igp_metric=metric))
        physical_input = self.ChangeInput(
            intent=resolved.intent,
            expected_rtt_ms=workflow_input.expected_rtt_ms,
            jira=workflow_input.jira,
            requested_by=workflow_input.user,
        )
        reviews: dict[str, list[str]] = {}
        diffs: dict[str, str] = {}
        average_rtt_ms: float | None = None
        applied = False
        physical = await self.physical(physical_input)
        physical_a, physical_z = await asyncio.gather(
            self.physical_router_a(
                self.EndpointDeployInput(phase="physical", rendered=physical.router_a)
            ),
            self.physical_router_z(
                self.EndpointDeployInput(phase="physical", rendered=physical.router_z)
            ),
        )
        for label, result in (("physical router A", physical_a), ("physical router Z", physical_z)):
            reviews[label] = result.reviewers
            diffs[label] = result.diff
        if not physical_a.approved or not physical_z.approved:
            self._unreachable_after("physical_router_z")
            outcome = "rejected for one or more routers at physical stage"
        else:
            intent = physical.intent
            common_input = self.ChangeInput(
                intent=intent,
                expected_rtt_ms=workflow_input.expected_rtt_ms,
                jira=workflow_input.jira,
                requested_by=workflow_input.user,
            )
            await self.validate_neighbors(common_input)
            addressing = await self.addressing(common_input)
            addressing_a, addressing_z = await asyncio.gather(
                self.addressing_router_a(
                    self.EndpointDeployInput(phase="addressing", rendered=addressing.router_a)
                ),
                self.addressing_router_z(
                    self.EndpointDeployInput(phase="addressing", rendered=addressing.router_z)
                ),
            )
            for label, result in (
                ("addressing router A", addressing_a),
                ("addressing router Z", addressing_z),
            ):
                reviews[label] = result.reviewers
                diffs[label] = result.diff
            if not addressing_a.approved or not addressing_z.approved:
                self._unreachable_after("addressing_router_z")
                outcome = "rejected for one or more routers at addressing stage"
            else:
                rtt_validation = await self.validate_rtt(common_input)
                average_rtt_ms = rtt_validation.average_rtt_ms
                routing = await self.routing(common_input)
                routing_a, routing_z = await asyncio.gather(
                    self.routing_router_a(
                        self.EndpointDeployInput(phase="routing", rendered=routing.router_a)
                    ),
                    self.routing_router_z(
                        self.EndpointDeployInput(phase="routing", rendered=routing.router_z)
                    ),
                )
                for label, result in (
                    ("routing router A", routing_a),
                    ("routing router Z", routing_z),
                ):
                    reviews[label] = result.reviewers
                    diffs[label] = result.diff
                if not routing_a.approved or not routing_z.approved:
                    self._unreachable_after("routing_router_z")
                    outcome = "rejected for one or more routers at routing stage"
                else:
                    await self.validate_routing(common_input)
                    outcome = "all stages approved and validated"
                    applied = True
        workflow_url = await self.workflow_url()
        await self.record_audit(
            self.AuditInput(
                jira=workflow_input.jira,
                intent=resolved.intent,
                outcome=outcome,
                reviews=reviews,
                diffs=diffs,
                average_rtt_ms=average_rtt_ms,
                workflow_url=workflow_url,
            )
        )
        return InternalBackboneBringupOutput(
            applied=applied,
            circuit_id=resolved.intent.circuit_id,
            local_endpoint=f"{resolved.intent.local.device_name}:{resolved.intent.local.lag.name}",
            remote_endpoint=f"{resolved.intent.remote.device_name}:{resolved.intent.remote.lag.name}",
            ipv4_prefix=resolved.intent.ipv4_prefix,
            ipv6_prefix=resolved.intent.ipv6_prefix,
            igp_metric=resolved.intent.igp_metric,
            average_rtt_ms=average_rtt_ms,
            approvers=reviews,
            jira=workflow_input.jira,
        )


class CircuitTurnupInput(BaseModel):
    """Operator input for a WAN circuit turn-up with related IGP retunes."""

    circuit_id: str = Field(min_length=1, description="Circuit CID already modeled in Nautobot.")
    jira: str = Field(
        pattern=r"^[A-Za-z][A-Za-z0-9]+-\d+$",
        description="Jira issue used for the plan and audit record.",
    )
    local_device: str = Field(min_length=1)
    local_ports: list[str] = Field(min_length=1)
    local_lag: str = Field(pattern=r"^ae\d+$")
    remote_device: str = Field(min_length=1)
    remote_ports: list[str] = Field(min_length=1)
    remote_lag: str = Field(pattern=r"^ae\d+$")
    ipv4_prefix: str
    minimum_links: int = Field(ge=1)
    path_metric: int = Field(ge=1, le=16_777_214)
    macsec: bool = True
    related_metrics: list[RelatedIgpChange] = Field(default_factory=list)
    user: str | None = None

    @field_validator("local_ports", "remote_ports")
    @classmethod
    def unique_ports(cls, ports: list[str]) -> list[str]:
        """Reject duplicate or empty port names."""
        normalized = [port.strip() for port in ports]
        if any(not port for port in normalized):
            raise ValueError("ports cannot contain empty names")
        if len(set(normalized)) != len(normalized):
            raise ValueError("ports must be unique")
        return normalized

    @field_validator("ipv4_prefix")
    @classmethod
    def validate_ipv4_prefix(cls, prefix: str) -> str:
        """Require a canonical point-to-point IPv4 network."""
        network = ipaddress.ip_network(prefix, strict=True)
        if network.version != 4 or network.prefixlen != 31:
            raise ValueError("ipv4_prefix must be an IPv4 /31 network")
        return str(network)

    @model_validator(mode="after")
    def validate_endpoints(self) -> CircuitTurnupInput:
        """Require distinct routers and feasible min-links."""
        if self.local_device == self.remote_device:
            raise ValueError("local and remote devices must be distinct")
        if self.minimum_links > min(len(self.local_ports), len(self.remote_ports)):
            raise ValueError("minimum_links cannot exceed either endpoint's member count")
        return self


class CircuitTurnupOutput(BaseModel):
    """Final circuit turn-up result."""

    applied: bool
    persisted: bool
    circuit_id: str
    local_endpoint: str
    remote_endpoint: str
    impacted_devices: list[str]
    approvers: list[str]
    jira: str


@workflow.defn
class BBCircuitTurnupWorkflow(WorkflowMetadataMixin, _ApprovalMixin):
    """Plan a WAN circuit turn-up, gate on the candidate, then mock persist and apply."""

    workflow_name = "BB Sandbox: Circuit Turn-up"
    workflow_description = (
        "Plan a WAN circuit turn-up and related POP-pair IS-IS metric changes from "
        "Nautobot, approve the candidate, then list the intended Nautobot writes"
    )
    workflow_input_class = CircuitTurnupInput
    workflow_api_endpoint = "/bb_sandbox/circuit_turnup"
    workflow_namespace = "bb_sandbox"
    workflow_lock = WorkflowLockSpec(key_fields=["circuit_id"])

    def __init__(self) -> None:
        """Define the drain-style circuit turn-up stages."""
        StageMixin.__init__(self)
        self.define_stage(
            name="resolve",
            description="Resolve circuit, ports, LAG names, and POP-pair fan-out.",
            requires_approval=False,
            depends_on=[],
        )
        self.define_stage(
            name="plan",
            description="Build in-memory Junos candidates. Nautobot is unchanged.",
            requires_approval=False,
            depends_on=["resolve"],
        )
        self.define_stage(
            name="record_plan",
            description="Post the multi-device plan to Jira.",
            requires_approval=False,
            depends_on=["plan"],
        )
        self.define_stage(
            name="review",
            description="Approve or reject the plan.",
            requires_approval=True,
            approval_threshold=1,
            depends_on=["record_plan"],
        )
        self.define_stage(
            name="preflight",
            description="Rebuild the plan and require an exact match.",
            requires_approval=False,
            depends_on=["review"],
        )
        self.define_stage(
            name="persist",
            description="List intended Nautobot writes.",
            requires_approval=False,
            depends_on=["preflight"],
        )
        self.define_stage(
            name="apply",
            description="Mock device commit of the approved plan.",
            requires_approval=False,
            depends_on=["persist"],
        )
        self.define_stage(
            name="validate",
            description="Mock LAG, MACsec, ISIS, MPLS, and RIB checks.",
            requires_approval=False,
            depends_on=["apply"],
        )
        self.define_stage(
            name="audit",
            description="Post the result to Jira.",
            requires_approval=False,
            depends_on=["review"],
        )

    class ResolveInput(StageInput):
        """Circuit turn-up resolution input."""

        request: CircuitTurnupInput

    class ResolveOutput(StageOutput):
        """Resolved turn-up intent."""

        intent: CircuitTurnupIntent

    @stage_executor("resolve")
    async def resolve(self, stage_input: ResolveInput) -> ResolveOutput:
        """Validate Jira and resolve Nautobot objects before proposing writes."""
        request = stage_input.request
        ticket = await workflow.execute_activity(
            validate_ticket,
            ValidateTicketInput(ticketing_platform="jira", issue_key=request.jira),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        intent = await workflow.execute_activity(
            resolve_circuit_turnup_intent,
            CircuitTurnupLookupInput(
                circuit_id=request.circuit_id,
                local_device=request.local_device,
                local_ports=request.local_ports,
                local_lag=request.local_lag,
                remote_device=request.remote_device,
                remote_ports=request.remote_ports,
                remote_lag=request.remote_lag,
                ipv4_prefix=request.ipv4_prefix,
                minimum_links=request.minimum_links,
                path_metric=request.path_metric,
                macsec=request.macsec,
                related_metrics=request.related_metrics,
            ),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        notes = "\n".join(f"- {note}" for note in intent.preflight_notes)
        note_block = f"\n\n### Preflight notes\n\n{notes}" if notes else ""
        related = (
            "\n".join(
                f"- `{change.local_pop}-{change.remote_pop}`: metric `{change.metric}`"
                for change in intent.related_metrics
            )
            or "- None"
        )
        impacted = "\n".join(f"- `{name}`" for name in intent.impacted_devices)
        return self.ResolveOutput(
            intent=intent,
            display=(
                f"Jira [{request.jira}]({ticket.url}): {ticket.summary}\n\n"
                "### Circuit\n\n"
                f"- **ID:** `{intent.circuit_id}`\n"
                f"- **Local endpoint:** `{intent.local.device_name}:{intent.local.lag_name}`\n"
                f"- **Remote endpoint:** `{intent.remote.device_name}:{intent.remote.lag_name}`\n"
                f"- **Path metric:** `{intent.path_metric}`\n"
                f"- **MACsec:** {'Enabled' if intent.macsec else 'Disabled'}\n\n"
                f"### Related IGP\n\n{related}\n\n"
                f"### Impacted devices\n\n{impacted}"
                f"{note_block}"
            ),
        )

    class PlanInput(StageInput):
        """In-memory plan input."""

        intent: CircuitTurnupIntent

    class PlanOutput(StageOutput):
        """Pinned combined candidate."""

        combined_diff: str
        mocked: bool

    @stage_executor("plan")
    async def plan_proposed_configuration(self, stage_input: PlanInput) -> PlanOutput:
        """Render proposed Junos without changing Nautobot or Config Store."""
        planned = await workflow.execute_activity(
            build_circuit_turnup_plan,
            stage_input.intent,
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        return self.PlanOutput(
            combined_diff=planned.combined_diff,
            mocked=planned.mocked,
            display=(
                "Built an in-memory candidate for every impacted device. "
                "Nautobot and Config Store are unchanged.\n\n"
                f"{_markdown_diff(planned.combined_diff)}"
            ),
        )

    class RecordPlanInput(StageInput):
        """Jira plan comment input."""

        jira: str
        intent: CircuitTurnupIntent
        diff: str
        workflow_url: str

    class RecordPlanOutput(StageOutput):
        """Jira plan comment result."""

        comment_id: str | None

    @stage_executor("record_plan")
    async def record_change_plan(self, stage_input: RecordPlanInput) -> RecordPlanOutput:
        """Publish the generated plan before waiting for approval."""
        devices = ", ".join(f"`{name}`" for name in stage_input.intent.impacted_devices)
        summary = (
            "### Proposed circuit turn-up\n\n"
            f"- **Workflow:** [Open workflow]({stage_input.workflow_url})\n"
            f"- **Circuit:** `{stage_input.intent.circuit_id}`\n"
            f"- **Devices:** {devices}\n"
            "- **Nautobot:** unchanged pending approval\n\n"
            f"#### Proposed candidate\n\n{_markdown_diff(stage_input.diff)}"
        )
        jira_body = (
            "h3. Proposed BB sandbox circuit turn-up\n\n"
            f"*Workflow:* [Open workflow|{stage_input.workflow_url}]\n"
            f"*Circuit:* {stage_input.intent.circuit_id}\n"
            f"*Devices:* {', '.join(stage_input.intent.impacted_devices)}\n"
            "*Nautobot:* unchanged pending approval\n\n"
            "h4. Proposed candidate\n"
            f"{_jira_diff(stage_input.diff)}"
        )
        result = await workflow.execute_activity(
            add_ticket_comment,
            AddCommentInput(
                ticketing_platform="jira",
                issue_key=stage_input.jira,
                body=jira_body,
            ),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        return self.RecordPlanOutput(
            comment_id=result.comment_id,
            display=f"Recorded change plan `{result.comment_id}` on {stage_input.jira}.\n\n{summary}",
        )

    class ReviewInput(StageInput):
        """Approval wait input."""

        diff: str
        mocked: bool

    class ReviewOutput(StageOutput):
        """Approval result."""

        approved: bool
        reviewers: list[str]
        diff: str

    @stage_executor("review")
    async def review_configuration_diff(self, stage_input: ReviewInput) -> ReviewOutput:
        """Wait for the drain-style plan gate."""
        approved, reviewers = await self.wait_for_review(
            "review", stage_input.diff, mocked=stage_input.mocked
        )
        decision = "Approved" if approved else "Rejected"
        reviewer_list = ", ".join(reviewers) or "none"
        return self.ReviewOutput(
            approved=approved,
            reviewers=reviewers,
            diff=stage_input.diff,
            display=(
                f"### {decision}\n\n"
                f"- **Reviewers:** {reviewer_list}\n\n"
                f"{_markdown_diff(stage_input.diff)}"
            ),
        )

    class PreflightInput(StageInput):
        """Stale-plan check input."""

        intent: CircuitTurnupIntent
        approved_diff: str

    class PreflightOutput(StageOutput):
        """Stale-plan check result."""

        matched: bool
        reason: str | None = None
        fresh_diff: str

    @stage_executor("preflight")
    async def preflight_approved_plan(self, stage_input: PreflightInput) -> PreflightOutput:
        """Reject execution when the in-memory plan drifted after approval."""
        planned = await workflow.execute_activity(
            build_circuit_turnup_plan,
            stage_input.intent,
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        matched = planned.combined_diff == stage_input.approved_diff
        reason = None if matched else "candidate changed since approval"
        display = (
            "Approved plan is still current."
            if matched
            else (
                "Plan drifted after approval; Nautobot was not written.\n\n"
                f"{_markdown_diff(planned.combined_diff)}"
            )
        )
        return self.PreflightOutput(
            matched=matched,
            reason=reason,
            fresh_diff=planned.combined_diff,
            display=display,
        )

    class PersistInput(StageInput):
        """Approved persist input."""

        intent: CircuitTurnupIntent

    class PersistOutput(StageOutput):
        """Listed Nautobot mutations."""

        written: bool

    @stage_executor("persist")
    async def persist_intent(self, stage_input: PersistInput) -> PersistOutput:
        """Show intended Nautobot writes without issuing them."""
        result = await workflow.execute_activity(
            persist_circuit_turnup_intent,
            stage_input.intent,
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        lines = "\n".join(
            f"- `{mutation.model}`: {mutation.summary}" for mutation in result.mutations
        )
        return self.PersistOutput(
            written=result.written,
            display=f"### Intended Nautobot mutations\n\n{lines}",
        )

    class ApplyInput(StageInput):
        """Mock apply input."""

        intent: CircuitTurnupIntent
        diff: str

    class ApplyOutput(StageOutput):
        """Mock apply result."""

        applied: bool

    @stage_executor("apply")
    async def apply_plan(self, stage_input: ApplyInput) -> ApplyOutput:
        """Simulate the device commit of the approved candidate."""
        await workflow.execute_activity(
            mock_apply_candidate,
            MockDiffInput(
                phase="routing",
                device=stage_input.intent.local.device_name,
                ports=stage_input.intent.local.ports,
                remote_device=stage_input.intent.remote.device_name,
                remote_ports=stage_input.intent.remote.ports,
                lag_name=stage_input.intent.local.lag_name,
                remote_lag=stage_input.intent.remote.lag_name,
                igp_metric=stage_input.intent.path_metric,
            ),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        return self.ApplyOutput(
            applied=True,
            display=f"Mock-applied the approved candidate.\n\n{MOCK_NOTICE}",
        )

    class ValidateInput(StageInput):
        """Mock validation input."""

        intent: CircuitTurnupIntent

    class ValidateOutput(StageOutput):
        """Mock validation result."""

        healthy: bool
        checks: list[str]
        mocked: bool

    @stage_executor("validate")
    async def validate_turnup(self, stage_input: ValidateInput) -> ValidateOutput:
        """Simulate GNI post-change operational checks."""
        result = await workflow.execute_activity(
            mock_validate_circuit_turnup,
            stage_input.intent,
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        checks = "\n".join(f"- {check}" for check in result.checks)
        return self.ValidateOutput(
            healthy=result.healthy,
            checks=result.checks,
            mocked=result.mocked,
            display=f"Mock validation passed.\n\n{checks}\n\n{MOCK_NOTICE}",
        )

    class AuditInput(StageInput):
        """Final Jira audit input."""

        jira: str
        intent: CircuitTurnupIntent
        decision: str
        reviewers: list[str]
        diff: str
        workflow_url: str
        checks: list[str] = Field(default_factory=list)
        checks_mocked: bool = True

    class AuditOutput(StageOutput):
        """Jira audit result."""

        comment_id: str | None

    @stage_executor("audit")
    async def record_audit(self, stage_input: AuditInput) -> AuditOutput:
        """Write the review result to Jira."""
        reviewers = ", ".join(stage_input.reviewers) or "none"
        validation = _jira_validation_checks(
            stage_input.checks,
            mocked=stage_input.checks_mocked,
        )
        summary = (
            "### Circuit turn-up\n\n"
            f"- **Workflow:** [Open workflow]({stage_input.workflow_url})\n"
            f"- **Circuit:** `{stage_input.intent.circuit_id}`\n"
            f"- **Reviewers:** {reviewers}\n"
            f"- **Result:** {stage_input.decision}\n\n"
            f"#### Candidate\n\n{_markdown_diff(stage_input.diff)}"
        )
        jira_body = (
            "h3. BB sandbox circuit turn-up\n\n"
            f"*Workflow:* [Open workflow|{stage_input.workflow_url}]\n"
            f"*Circuit:* {stage_input.intent.circuit_id}\n"
            f"*Reviewers:* {reviewers}\n"
            f"*Result:* {stage_input.decision}\n\n"
            "h4. Candidate\n"
            f"{_jira_diff(stage_input.diff)}"
            f"{validation}"
        )
        result = await workflow.execute_activity(
            add_ticket_comment,
            AddCommentInput(
                ticketing_platform="jira",
                issue_key=stage_input.jira,
                body=jira_body,
            ),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        return self.AuditOutput(
            comment_id=result.comment_id,
            display=(
                f"Recorded audit comment `{result.comment_id}` on {stage_input.jira}.\n\n{summary}"
            ),
        )

    def _skip_after_review(self) -> None:
        for name in ("preflight", "persist", "apply", "validate"):
            self.set_stage_state(name, StateEnum.UNREACHABLE)

    @run_nv_config_manager_workflow
    async def run(self, workflow_input: CircuitTurnupInput) -> CircuitTurnupOutput:  # type: ignore[override, ty:invalid-method-override]
        """Execute the circuit turn-up workflow."""
        self.set_input(workflow_input)
        workflow.upsert_search_attributes({ISSUE_KEY_SEARCH_ATTRIBUTE: [workflow_input.jira]})
        resolved = await self.resolve(self.ResolveInput(request=workflow_input))
        planned = await self.plan_proposed_configuration(self.PlanInput(intent=resolved.intent))
        workflow_url = await self.workflow_url()
        await self.record_change_plan(
            self.RecordPlanInput(
                jira=workflow_input.jira,
                intent=resolved.intent,
                diff=planned.combined_diff,
                workflow_url=workflow_url,
            )
        )
        reviewed = await self.review_configuration_diff(
            self.ReviewInput(diff=planned.combined_diff, mocked=planned.mocked)
        )
        persisted = False
        applied = False
        checks: list[str] = []
        checks_mocked = True
        if reviewed.approved:
            preflight = await self.preflight_approved_plan(
                self.PreflightInput(
                    intent=resolved.intent,
                    approved_diff=reviewed.diff,
                )
            )
            if preflight.matched:
                persist = await self.persist_intent(self.PersistInput(intent=resolved.intent))
                persisted = persist.written
                await self.apply_plan(self.ApplyInput(intent=resolved.intent, diff=reviewed.diff))
                validated = await self.validate_turnup(self.ValidateInput(intent=resolved.intent))
                checks = validated.checks
                checks_mocked = validated.mocked
                applied = True
                decision = "approved; Nautobot writes listed but not issued; mock apply completed"
            else:
                self.set_stage_state("persist", StateEnum.UNREACHABLE)
                self.set_stage_state("apply", StateEnum.UNREACHABLE)
                self.set_stage_state("validate", StateEnum.UNREACHABLE)
                decision = f"blocked: {preflight.reason}"
        else:
            self._skip_after_review()
            decision = "rejected; nothing changed"
        await self.record_audit(
            self.AuditInput(
                jira=workflow_input.jira,
                intent=resolved.intent,
                decision=decision,
                reviewers=reviewed.reviewers,
                diff=reviewed.diff,
                workflow_url=workflow_url,
                checks=checks,
                checks_mocked=checks_mocked,
            )
        )
        return CircuitTurnupOutput(
            applied=applied,
            persisted=persisted,
            circuit_id=resolved.intent.circuit_id,
            local_endpoint=f"{resolved.intent.local.device_name}:{resolved.intent.local.lag_name}",
            remote_endpoint=f"{resolved.intent.remote.device_name}:{resolved.intent.remote.lag_name}",
            impacted_devices=resolved.intent.impacted_devices,
            approvers=reviewed.reviewers,
            jira=workflow_input.jira,
        )


class CircuitReserveInput(BaseModel):
    """Allocate unused WAN resources and write them as Planned."""

    local_device: str = Field(min_length=1)
    remote_device: str = Field(min_length=1)
    jira: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9]+-\d+$")
    circuit_id: str | None = Field(default=None)
    path_metric: int = Field(default=750, ge=1, le=16_777_214)
    macsec: bool = True
    minimum_links: int = Field(default=1, ge=1)
    user: str | None = None

    @field_validator("circuit_id")
    @classmethod
    def empty_circuit_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def distinct_devices(self) -> CircuitReserveInput:
        if self.local_device == self.remote_device:
            raise ValueError("local and remote devices must be distinct")
        return self


class CircuitReserveOutput(BaseModel):
    """Planned objects written to Nautobot."""

    written: bool
    circuit_id: str
    local_endpoint: str
    remote_endpoint: str
    ipv4_prefix: str
    approvers: list[str]
    jira: str


@workflow.defn
class BBCircuitReserveWorkflow(WorkflowMetadataMixin, _ApprovalMixin):
    """Allocate ports, LAG, and prefix, then write them as Planned after approval."""

    workflow_name = "BB Sandbox: Circuit Reserve"
    workflow_description = (
        "Pick the next unused member ports, LAG names, and /31, then write them "
        "to Nautobot as Planned"
    )
    workflow_input_class = CircuitReserveInput
    workflow_api_endpoint = "/bb_sandbox/circuit_reserve"
    workflow_namespace = "bb_sandbox"
    workflow_lock = WorkflowLockSpec(key_fields=["local_device", "remote_device"])

    def __init__(self) -> None:
        StageMixin.__init__(self)
        self.define_stage(
            name="resolve",
            description="Allocate unused ports, LAG names, and a /31.",
            requires_approval=False,
            depends_on=[],
        )
        self.define_stage(
            name="review",
            description="Approve the reservation before writing Nautobot.",
            requires_approval=True,
            approval_threshold=1,
            depends_on=["resolve"],
        )
        self.define_stage(
            name="persist",
            description="Write Planned LAG, IP, circuit, and member bindings.",
            requires_approval=False,
            depends_on=["review"],
        )
        self.define_stage(
            name="audit",
            description="Post the result to Jira.",
            requires_approval=False,
            depends_on=["review"],
        )

    class ResolveInput(StageInput):
        request: CircuitReserveInput

    class ResolveOutput(StageOutput):
        plan: CircuitReservationPlan

    @stage_executor("resolve")
    async def resolve(self, stage_input: ResolveInput) -> ResolveOutput:
        request = stage_input.request
        ticket = await workflow.execute_activity(
            validate_ticket,
            ValidateTicketInput(ticketing_platform="jira", issue_key=request.jira),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        plan = await workflow.execute_activity(
            plan_circuit_reservation,
            CircuitReservationLookupInput(
                local_device=request.local_device,
                remote_device=request.remote_device,
                jira=request.jira,
                circuit_id=request.circuit_id,
                path_metric=request.path_metric,
                macsec=request.macsec,
                minimum_links=request.minimum_links,
            ),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        intent = plan.intent
        create = "Create" if plan.create_circuit else "Attach"
        return self.ResolveOutput(
            plan=plan,
            display=(
                f"Jira [{request.jira}]({ticket.url}): {ticket.summary}\n\n"
                "### Reservation\n\n"
                f"- **Circuit:** `{intent.circuit_id}` ({create.lower()})\n"
                f"- **Local:** `{intent.local.device_name}` "
                f"`{intent.local.ports[0]}` → `{intent.local.lag_name}` "
                f"`{intent.local.ipv4_address}`\n"
                f"- **Remote:** `{intent.remote.device_name}` "
                f"`{intent.remote.ports[0]}` → `{intent.remote.lag_name}` "
                f"`{intent.remote.ipv4_address}`\n"
                f"- **Prefix:** `{intent.ipv4_prefix}` from `{plan.parent_prefix}`\n"
                f"- **Path metric:** `{intent.path_metric}`\n"
                f"- **MACsec:** {'Enabled' if intent.macsec else 'Disabled'}\n\n"
                "Nautobot is unchanged until this reservation is approved."
            ),
        )

    class ReviewInput(StageInput):
        plan: CircuitReservationPlan

    class ReviewOutput(StageOutput):
        approved: bool
        reviewers: list[str]
        plan: CircuitReservationPlan

    @stage_executor("review")
    async def review_reservation(self, stage_input: ReviewInput) -> ReviewOutput:
        summary = (
            f"{stage_input.plan.intent.local.device_name}:"
            f"{stage_input.plan.intent.local.lag_name} ↔ "
            f"{stage_input.plan.intent.remote.device_name}:"
            f"{stage_input.plan.intent.remote.lag_name}"
        )
        approved, reviewers = await self.wait_for_review("review", summary, mocked=False)
        decision = "Approved" if approved else "Rejected"
        reviewer_list = ", ".join(reviewers) or "none"
        return self.ReviewOutput(
            approved=approved,
            reviewers=reviewers,
            plan=stage_input.plan,
            display=f"### {decision}\n\n- **Reviewers:** {reviewer_list}\n- **Reservation:** `{summary}`",
        )

    class PersistInput(StageInput):
        plan: CircuitReservationPlan

    class PersistOutput(StageOutput):
        written: bool
        circuit_uuid: str
        mutations: list[ProposedNautobotMutation]

    @stage_executor("persist")
    async def persist_reservation(self, stage_input: PersistInput) -> PersistOutput:
        result = await workflow.execute_activity(
            persist_circuit_reservation,
            stage_input.plan,
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        lines = "\n".join(
            f"- `{mutation.model}`: {mutation.summary}" for mutation in result.mutations
        )
        return self.PersistOutput(
            written=result.written,
            circuit_uuid=result.circuit_uuid,
            mutations=result.mutations,
            display=f"### Planned objects written\n\n{lines}",
        )

    class AuditInput(StageInput):
        jira: str
        intent: CircuitTurnupIntent
        decision: str
        reviewers: list[str]
        workflow_url: str
        mutations: list[ProposedNautobotMutation] = Field(default_factory=list)

    class AuditOutput(StageOutput):
        comment_id: str | None

    @stage_executor("audit")
    async def record_audit(self, stage_input: AuditInput) -> AuditOutput:
        reviewers = ", ".join(stage_input.reviewers) or "none"
        objects = _jira_written_objects(stage_input.mutations)
        jira_body = (
            "h3. BB sandbox circuit reserve\n\n"
            f"*Workflow:* [Open workflow|{stage_input.workflow_url}]\n"
            f"*Circuit:* {stage_input.intent.circuit_id}\n"
            f"*Reviewers:* {reviewers}\n"
            f"*Result:* {stage_input.decision}"
            f"{objects}"
        )
        result = await workflow.execute_activity(
            add_ticket_comment,
            AddCommentInput(
                ticketing_platform="jira",
                issue_key=stage_input.jira,
                body=jira_body,
            ),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        return self.AuditOutput(
            comment_id=result.comment_id,
            display=f"Recorded audit comment `{result.comment_id}` on {stage_input.jira}.",
        )

    @run_nv_config_manager_workflow
    async def run(self, workflow_input: CircuitReserveInput) -> CircuitReserveOutput:  # type: ignore[override, ty:invalid-method-override]
        self.set_input(workflow_input)
        workflow.upsert_search_attributes({ISSUE_KEY_SEARCH_ATTRIBUTE: [workflow_input.jira]})
        resolved = await self.resolve(self.ResolveInput(request=workflow_input))
        reviewed = await self.review_reservation(self.ReviewInput(plan=resolved.plan))
        written = False
        mutations: list[ProposedNautobotMutation] = []
        if reviewed.approved:
            persist = await self.persist_reservation(self.PersistInput(plan=reviewed.plan))
            written = persist.written
            mutations = persist.mutations
            decision = "approved; Planned objects written"
        else:
            self.set_stage_state("persist", StateEnum.UNREACHABLE)
            decision = "rejected; nothing written"
        workflow_url = await self.workflow_url()
        await self.record_audit(
            self.AuditInput(
                jira=workflow_input.jira,
                intent=resolved.plan.intent,
                decision=decision,
                reviewers=reviewed.reviewers,
                workflow_url=workflow_url,
                mutations=mutations,
            )
        )
        intent = resolved.plan.intent
        return CircuitReserveOutput(
            written=written,
            circuit_id=intent.circuit_id,
            local_endpoint=f"{intent.local.device_name}:{intent.local.lag_name}",
            remote_endpoint=f"{intent.remote.device_name}:{intent.remote.lag_name}",
            ipv4_prefix=intent.ipv4_prefix,
            approvers=reviewed.reviewers,
            jira=workflow_input.jira,
        )


class CircuitActivateInput(BaseModel):
    """Activate a Planned circuit reservation during a maintenance window."""

    circuit_id: str = Field(min_length=1)
    jira: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9]+-\d+$")
    user: str | None = None


class CircuitActivateOutput(BaseModel):
    """Activation result."""

    activated: bool
    applied: bool
    circuit_id: str
    local_endpoint: str
    remote_endpoint: str
    approvers: list[str]
    jira: str


@workflow.defn
class BBCircuitActivateWorkflow(WorkflowMetadataMixin, _ApprovalMixin):
    """Flip a Planned reservation to Active, then mock apply and validate."""

    workflow_name = "BB Sandbox: Circuit Activate"
    workflow_description = (
        "Load a Planned circuit reservation, approve the candidate, then set those "
        "objects Active and mock the device push"
    )
    workflow_input_class = CircuitActivateInput
    workflow_api_endpoint = "/bb_sandbox/circuit_activate"
    workflow_namespace = "bb_sandbox"
    workflow_lock = WorkflowLockSpec(key_fields=["circuit_id"])

    def __init__(self) -> None:
        StageMixin.__init__(self)
        self.define_stage(
            name="resolve",
            description="Load the Planned circuit reservation from Nautobot.",
            requires_approval=False,
            depends_on=[],
        )
        self.define_stage(
            name="plan",
            description="Build the Junos candidate that Active status would render.",
            requires_approval=False,
            depends_on=["resolve"],
        )
        self.define_stage(
            name="record_plan",
            description="Post the candidate to Jira.",
            requires_approval=False,
            depends_on=["plan"],
        )
        self.define_stage(
            name="review",
            description="Approve or reject the activation.",
            requires_approval=True,
            approval_threshold=1,
            depends_on=["record_plan"],
        )
        self.define_stage(
            name="preflight",
            description="Confirm the reservation is still Planned and the devices are clear.",
            requires_approval=False,
            depends_on=["review"],
        )
        self.define_stage(
            name="persist",
            description="Set reserved objects to Active.",
            requires_approval=False,
            depends_on=["preflight"],
        )
        self.define_stage(
            name="apply",
            description="Mock device commit of the approved plan.",
            requires_approval=False,
            depends_on=["persist"],
        )
        self.define_stage(
            name="validate",
            description="Mock LAG, MACsec, ISIS, MPLS, and RIB checks.",
            requires_approval=False,
            depends_on=["apply"],
        )
        self.define_stage(
            name="audit",
            description="Post the result to Jira.",
            requires_approval=False,
            depends_on=["review"],
        )

    class ResolveInput(StageInput):
        request: CircuitActivateInput

    class ResolveOutput(StageOutput):
        objects: PlannedCircuitObjects

    @stage_executor("resolve")
    async def resolve(self, stage_input: ResolveInput) -> ResolveOutput:
        request = stage_input.request
        ticket = await workflow.execute_activity(
            validate_ticket,
            ValidateTicketInput(ticketing_platform="jira", issue_key=request.jira),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        objects = await workflow.execute_activity(
            resolve_planned_circuit,
            request.circuit_id,
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        intent = objects.intent
        return self.ResolveOutput(
            objects=objects,
            display=(
                f"Jira [{request.jira}]({ticket.url}): {ticket.summary}\n\n"
                "### Planned circuit\n\n"
                f"- **ID:** `{intent.circuit_id}`\n"
                f"- **Local:** `{intent.local.device_name}:{intent.local.lag_name}` "
                f"({', '.join(intent.local.ports)})\n"
                f"- **Remote:** `{intent.remote.device_name}:{intent.remote.lag_name}` "
                f"({', '.join(intent.remote.ports)})\n"
                f"- **Prefix:** `{intent.ipv4_prefix}`\n"
                f"- **Path metric:** `{intent.path_metric}`"
            ),
        )

    class PlanInput(StageInput):
        intent: CircuitTurnupIntent

    class PlanOutput(StageOutput):
        combined_diff: str
        mocked: bool

    @stage_executor("plan")
    async def plan_activation(self, stage_input: PlanInput) -> PlanOutput:
        planned = await workflow.execute_activity(
            build_circuit_turnup_plan,
            stage_input.intent,
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        return self.PlanOutput(
            combined_diff=planned.combined_diff,
            mocked=planned.mocked,
            display=(
                "Candidate if these Planned objects become Active:\n\n"
                f"{_markdown_diff(planned.combined_diff)}"
            ),
        )

    class RecordPlanInput(StageInput):
        jira: str
        intent: CircuitTurnupIntent
        diff: str
        workflow_url: str

    class RecordPlanOutput(StageOutput):
        comment_id: str | None

    @stage_executor("record_plan")
    async def record_change_plan(self, stage_input: RecordPlanInput) -> RecordPlanOutput:
        jira_body = (
            "h3. Proposed BB sandbox circuit activate\n\n"
            f"*Workflow:* [Open workflow|{stage_input.workflow_url}]\n"
            f"*Circuit:* {stage_input.intent.circuit_id}\n"
            "*Nautobot:* Planned objects unchanged pending approval\n\n"
            "h4. Proposed candidate\n"
            f"{_jira_diff(stage_input.diff)}"
        )
        result = await workflow.execute_activity(
            add_ticket_comment,
            AddCommentInput(
                ticketing_platform="jira",
                issue_key=stage_input.jira,
                body=jira_body,
            ),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        return self.RecordPlanOutput(
            comment_id=result.comment_id,
            display=(
                f"Recorded change plan `{result.comment_id}` on {stage_input.jira}.\n\n"
                f"{_markdown_diff(stage_input.diff)}"
            ),
        )

    class ReviewInput(StageInput):
        diff: str
        mocked: bool

    class ReviewOutput(StageOutput):
        approved: bool
        reviewers: list[str]
        diff: str

    @stage_executor("review")
    async def review_activation(self, stage_input: ReviewInput) -> ReviewOutput:
        approved, reviewers = await self.wait_for_review(
            "review", stage_input.diff, mocked=stage_input.mocked
        )
        decision = "Approved" if approved else "Rejected"
        reviewer_list = ", ".join(reviewers) or "none"
        return self.ReviewOutput(
            approved=approved,
            reviewers=reviewers,
            diff=stage_input.diff,
            display=(
                f"### {decision}\n\n- **Reviewers:** {reviewer_list}\n\n"
                f"{_markdown_diff(stage_input.diff)}"
            ),
        )

    class PreflightInput(StageInput):
        circuit_id: str
        approved_diff: str

    class PreflightOutput(StageOutput):
        matched: bool
        objects: PlannedCircuitObjects | None = None
        reason: str | None = None

    @stage_executor("preflight")
    async def preflight_activation(self, stage_input: PreflightInput) -> PreflightOutput:
        objects = await workflow.execute_activity(
            resolve_planned_circuit,
            stage_input.circuit_id,
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        planned = await workflow.execute_activity(
            build_circuit_turnup_plan,
            objects.intent,
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        local_check = await self._check_activation_endpoint(objects.intent.local)
        remote_check = await self._check_activation_endpoint(objects.intent.remote)
        comparison = ActivationPreflightComparison.from_checks(
            candidate_matched=planned.combined_diff == stage_input.approved_diff,
            conflicts=[*local_check.conflicts, *remote_check.conflicts],
        )
        if comparison.matched:
            mocked = local_check.mocked and remote_check.mocked
            device_line = (
                "Mock devices have no reserved LAG, member, or address configuration."
                if mocked
                else "Both devices still lack the reserved LAG, members, and addresses."
            )
            display = f"Reservation is still Planned and the candidate matches. {device_line}"
        else:
            occupancy = ""
            if comparison.conflicts:
                lines = "\n".join(f"- {item}" for item in comparison.conflicts)
                occupancy = f"\n\n#### Device occupancy\n\n{lines}"
            display = f"Blocked: {comparison.reason}.{occupancy}"
        return self.PreflightOutput(
            matched=comparison.matched,
            objects=objects if comparison.matched else None,
            reason=comparison.reason,
            display=display,
        )

    async def _check_activation_endpoint(
        self, endpoint: CircuitTurnupEndpoint
    ) -> ActivationDeviceCheckOutput:
        device_result = await workflow.execute_activity(
            get_network_device,
            GetNetworkDeviceInput(device_id=endpoint.device_id),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        DeviceMixin.attach_device_search_attributes(device_result.device)
        return await workflow.execute_activity(
            check_activation_device_state,
            ActivationDeviceCheckInput(
                device_data=device_result.device,
                lag_name=endpoint.lag_name,
                member_port=endpoint.ports[0],
                ipv4_address=endpoint.ipv4_address,
            ),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )

    class PersistInput(StageInput):
        objects: PlannedCircuitObjects

    class PersistOutput(StageOutput):
        activated: bool

    @stage_executor("persist")
    async def persist_activation(self, stage_input: PersistInput) -> PersistOutput:
        result = await workflow.execute_activity(
            activate_planned_circuit,
            stage_input.objects,
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        return self.PersistOutput(
            activated=True,
            display=f"Set {len(result.updated_ids)} objects to Active.",
        )

    class ApplyInput(StageInput):
        intent: CircuitTurnupIntent

    class ApplyOutput(StageOutput):
        applied: bool

    @stage_executor("apply")
    async def apply_activation(self, stage_input: ApplyInput) -> ApplyOutput:
        await workflow.execute_activity(
            mock_apply_candidate,
            MockDiffInput(
                phase="routing",
                device=stage_input.intent.local.device_name,
                ports=stage_input.intent.local.ports,
                remote_device=stage_input.intent.remote.device_name,
                remote_ports=stage_input.intent.remote.ports,
                lag_name=stage_input.intent.local.lag_name,
                remote_lag=stage_input.intent.remote.lag_name,
                igp_metric=stage_input.intent.path_metric,
            ),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        return self.ApplyOutput(
            applied=True,
            display=f"Mock-applied the Active candidate.\n\n{MOCK_NOTICE}",
        )

    class ValidateInput(StageInput):
        intent: CircuitTurnupIntent

    class ValidateOutput(StageOutput):
        healthy: bool
        checks: list[str]
        mocked: bool

    @stage_executor("validate")
    async def validate_activation(self, stage_input: ValidateInput) -> ValidateOutput:
        result = await workflow.execute_activity(
            mock_validate_circuit_turnup,
            stage_input.intent,
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        checks = "\n".join(f"- {check}" for check in result.checks)
        return self.ValidateOutput(
            healthy=result.healthy,
            checks=result.checks,
            mocked=result.mocked,
            display=f"Mock validation passed.\n\n{checks}\n\n{MOCK_NOTICE}",
        )

    class AuditInput(StageInput):
        jira: str
        intent: CircuitTurnupIntent
        decision: str
        reviewers: list[str]
        diff: str
        workflow_url: str
        checks: list[str] = Field(default_factory=list)
        checks_mocked: bool = True

    class AuditOutput(StageOutput):
        comment_id: str | None

    @stage_executor("audit")
    async def record_audit(self, stage_input: AuditInput) -> AuditOutput:
        reviewers = ", ".join(stage_input.reviewers) or "none"
        validation = _jira_validation_checks(
            stage_input.checks,
            mocked=stage_input.checks_mocked,
        )
        jira_body = (
            "h3. BB sandbox circuit activate\n\n"
            f"*Workflow:* [Open workflow|{stage_input.workflow_url}]\n"
            f"*Circuit:* {stage_input.intent.circuit_id}\n"
            f"*Reviewers:* {reviewers}\n"
            f"*Result:* {stage_input.decision}\n\n"
            "h4. Candidate\n"
            f"{_jira_diff(stage_input.diff)}"
            f"{validation}"
        )
        result = await workflow.execute_activity(
            add_ticket_comment,
            AddCommentInput(
                ticketing_platform="jira",
                issue_key=stage_input.jira,
                body=jira_body,
            ),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        return self.AuditOutput(
            comment_id=result.comment_id,
            display=f"Recorded audit comment `{result.comment_id}` on {stage_input.jira}.",
        )

    def _skip_after_review(self) -> None:
        for name in ("preflight", "persist", "apply", "validate"):
            self.set_stage_state(name, StateEnum.UNREACHABLE)

    @run_nv_config_manager_workflow
    async def run(self, workflow_input: CircuitActivateInput) -> CircuitActivateOutput:  # type: ignore[override, ty:invalid-method-override]
        self.set_input(workflow_input)
        workflow.upsert_search_attributes({ISSUE_KEY_SEARCH_ATTRIBUTE: [workflow_input.jira]})
        resolved = await self.resolve(self.ResolveInput(request=workflow_input))
        planned = await self.plan_activation(self.PlanInput(intent=resolved.objects.intent))
        workflow_url = await self.workflow_url()
        await self.record_change_plan(
            self.RecordPlanInput(
                jira=workflow_input.jira,
                intent=resolved.objects.intent,
                diff=planned.combined_diff,
                workflow_url=workflow_url,
            )
        )
        reviewed = await self.review_activation(
            self.ReviewInput(diff=planned.combined_diff, mocked=planned.mocked)
        )
        activated = False
        applied = False
        checks: list[str] = []
        checks_mocked = True
        if reviewed.approved:
            preflight = await self.preflight_activation(
                self.PreflightInput(
                    circuit_id=workflow_input.circuit_id,
                    approved_diff=reviewed.diff,
                )
            )
            if preflight.matched and preflight.objects is not None:
                await self.persist_activation(self.PersistInput(objects=preflight.objects))
                activated = True
                await self.apply_activation(self.ApplyInput(intent=preflight.objects.intent))
                validated = await self.validate_activation(
                    self.ValidateInput(intent=preflight.objects.intent)
                )
                checks = validated.checks
                checks_mocked = validated.mocked
                applied = True
                decision = "approved; objects set Active; mock apply completed"
            else:
                self.set_stage_state("persist", StateEnum.UNREACHABLE)
                self.set_stage_state("apply", StateEnum.UNREACHABLE)
                self.set_stage_state("validate", StateEnum.UNREACHABLE)
                decision = f"blocked: {preflight.reason}"
        else:
            self._skip_after_review()
            decision = "rejected; objects remain Planned"
        await self.record_audit(
            self.AuditInput(
                jira=workflow_input.jira,
                intent=resolved.objects.intent,
                decision=decision,
                reviewers=reviewed.reviewers,
                diff=reviewed.diff,
                workflow_url=workflow_url,
                checks=checks,
                checks_mocked=checks_mocked,
            )
        )
        intent = resolved.objects.intent
        return CircuitActivateOutput(
            activated=activated,
            applied=applied,
            circuit_id=intent.circuit_id,
            local_endpoint=f"{intent.local.device_name}:{intent.local.lag_name}",
            remote_endpoint=f"{intent.remote.device_name}:{intent.remote.lag_name}",
            approvers=reviewed.reviewers,
            jira=workflow_input.jira,
        )


REGISTERED_WORKFLOWS = [
    BBDrainInterfaceWorkflow,
    BBInternalBackboneBringupWorkflow,
    BBCircuitTurnupWorkflow,
    BBCircuitReserveWorkflow,
    BBCircuitActivateWorkflow,
]
