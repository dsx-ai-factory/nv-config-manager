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
"""Diagnostics Workflow Definition."""

import asyncio
from datetime import timedelta

from pydantic import BaseModel, Field
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

from nv_config_manager.temporal.common.decorators.workflow import run_nv_config_manager_workflow
from nv_config_manager.temporal.common.mixins.metadata import WorkflowMetadataMixin
from nv_config_manager.temporal.common.mixins.stage import (
    StageInput,
    StageMixin,
    StageOutput,
    StateEnum,
    stage_executor,
)
from nv_config_manager.temporal.common.search_attributes import ISSUE_KEY_SEARCH_ATTRIBUTE
from nv_config_manager.temporal.common.workflow_references import DeviceReferences

with workflow.unsafe.imports_passed_through():
    from nv_config_manager.temporal.common.mixins.archive import ArchiveMixin
    from nv_config_manager.temporal.common.mixins.device import DeviceMixin, NetworkDeviceData
    from nv_config_manager.temporal.ngc.activities.dcim import (
        GetNetworkDeviceInput,
        get_network_device,
    )
    from nv_config_manager.temporal.ngc.activities.diagnostics import (
        RunDiagnosticsInput,
        RunDiagnosticsOutput,
        TechSupportInput,
        TechSupportOutput,
        collect_tech_support_bundle,
        get_available_commands,
        run_diagnostic_commands,
    )
    from nv_config_manager.temporal.ngc.activities.ticketing import (
        AddCommentInput,
        UploadAttachmentInput,
        UploadTechSupportFromRedisInput,
        ValidateTicketInput,
        add_ticket_comment,
        upload_attachment,
        upload_tech_support_from_redis,
        validate_ticket,
    )


# Per-activity timeouts and retry policies
_TICKET_TIMEOUT = timedelta(seconds=30)
_DEVICE_LOOKUP_TIMEOUT = timedelta(seconds=30)
_DIAGNOSTIC_CMD_TIMEOUT = timedelta(seconds=120)
_TECH_SUPPORT_TIMEOUT = timedelta(
    seconds=600
)  # cl-support -M (~300s) + base64 transfer (~120s) + margin
_UPLOAD_TIMEOUT = timedelta(seconds=120)
_UPLOAD_LARGE_TIMEOUT = timedelta(seconds=300)
_COMMENT_TIMEOUT = timedelta(seconds=30)

_DEFAULT_RETRY = RetryPolicy(maximum_attempts=3)
_DIAGNOSTIC_RETRY = RetryPolicy(
    maximum_attempts=1
)  # per-command errors are captured inline; retrying the whole activity wastes time
_EXPENSIVE_RETRY = RetryPolicy(
    maximum_attempts=1
)  # don't retry a timed-out collection — it already waited 600s
_PREFLIGHT_RETRY = RetryPolicy(maximum_attempts=1)  # fail fast before touching devices


# =============================================================================
# Workflow Input / Result
# =============================================================================


class DiagnosticsWorkflowInput(BaseModel):
    device_ids: DeviceReferences = Field(description="DCIM identifiers of the devices to diagnose.")
    commands: list[str] = Field(
        description="Diagnostic command catalog names to run on each device."
    )
    ticketing_platform: str = Field(
        default="", description="Ticketing platform to update; empty enables ticketless mode."
    )
    issue_key: str = Field(
        default="", description="Issue key to update; empty enables ticketless mode."
    )
    include_tech_support: bool = Field(
        default=False, description="Whether to collect a technical-support bundle from each device."
    )
    user: str = Field(
        default="",
        description="Engineer username or email, populated from request authentication when omitted.",
    )


class DiagnosticsWorkflowResult(BaseModel):
    issue_key: str
    devices_count: int
    commands_run: list[str]
    attachment_url: str  # URL / ID of the uploaded diagnostics file (empty in ticketless mode)
    tech_support_urls: list[str]  # one entry per device when include_tech_support=True
    comment_id: str
    diagnostics_content: str = (
        ""  # assembled diagnostics text (populated in ticketless mode instead of uploading)
    )
    warning: str = (
        ""  # human-readable warning, e.g. when Jira access failed and ticketless fallback was used
    )


# =============================================================================
# Stage Input / Output Models
# =============================================================================


class ValidateTicketStageInput(StageInput):
    ticketing_platform: str
    issue_key: str


class ValidateTicketStageOutput(StageOutput):
    summary: str
    status: str
    url: str


class ResolveDevicesStageInput(StageInput):
    device_ids: list[str]


class ResolveDevicesStageOutput(StageOutput):
    devices: list[NetworkDeviceData]


class RunDiagnosticsStageInput(StageInput):
    devices: list[NetworkDeviceData]
    commands: list[str]


class RunDiagnosticsStageOutput(StageOutput):
    results: list[RunDiagnosticsOutput]


class CollectTechSupportStageInput(StageInput):
    devices: list[NetworkDeviceData]


class CollectTechSupportStageOutput(StageOutput):
    bundles: list[TechSupportOutput]


class AssembleOutputStageInput(StageInput):
    results: list[RunDiagnosticsOutput]
    issue_key: str
    triggered_by: str
    commands: list[str]
    timestamp: str


class AssembleOutputStageOutput(StageOutput):
    content: bytes
    filename: str


class UploadAttachmentStageInput(StageInput):
    ticketing_platform: str
    issue_key: str
    filename: str
    content: bytes
    content_type: str


class UploadAttachmentStageOutput(StageOutput):
    attachment_id: str
    attachment_url: str


class UploadTechSupportStageInput(StageInput):
    ticketing_platform: str
    issue_key: str
    bundles: list[TechSupportOutput]


class UploadTechSupportStageOutput(StageOutput):
    attachment_urls: list[str]
    tech_support_lines: list[str] = []  # formatted lines for Jira comment (includes TTL annotation)
    download_urls: list[str] = []  # clean download URLs for DiagnosticsWorkflowResult


class PostCommentStageInput(StageInput):
    ticketing_platform: str
    issue_key: str
    body: str


class PostCommentStageOutput(StageOutput):
    comment_id: str


# =============================================================================
# Helpers
# =============================================================================


def _assemble_diagnostics_text(
    results: list[RunDiagnosticsOutput],
    issue_key: str,
    triggered_by: str,
    commands: list[str],
    timestamp: str,
) -> bytes:
    """Merge per-device diagnostic outputs into a single structured text file."""
    sep = "=" * 72
    thin = "-" * 72
    lines: list[str] = [
        sep,
        "DIAGNOSTICS REPORT",
        f"Ticket:       {issue_key or 'N/A (ticketless)'}",
        f"Triggered by: {triggered_by}",
        f"Timestamp:    {timestamp}",
        f"Commands:     {', '.join(commands)}",
        sep,
        "",
    ]
    for result in results:
        lines += [f"Device: {result.device_name}", thin]
        for cmd_name, output in result.outputs.items():
            lines += [f">>> {cmd_name}", output, ""]
        lines.append("")
    return "\n".join(lines).encode()


def _format_comment(
    issue_key: str,
    triggered_by: str,
    devices: list[NetworkDeviceData],
    commands: list[str],
    diag_url: str,
    tech_support_urls: list[str],
    timestamp: str,
) -> str:
    """Build the plain-text comment body posted to the ticket at workflow end."""
    lines: list[str] = [
        "Diagnostics workflow completed",
        "",
        f"Ticket:       {issue_key}",
        f"Triggered by: {triggered_by}",
        f"Timestamp:    {timestamp}",
        "",
        f"Devices ({len(devices)}):",
    ]
    for device in devices:
        lines.append(f"  - {device.name}  [{device.platform.value}]")
    lines += ["", f"Commands ({len(commands)}):"]
    for cmd in commands:
        lines.append(f"  - {cmd}")
    lines += ["", f"Diagnostics output: {diag_url}"]
    if tech_support_urls:
        lines += ["", f"Tech-support bundles ({len(tech_support_urls)}):"]
        for url in tech_support_urls:
            lines.append(f"  - {url}")
    return "\n".join(lines)


# =============================================================================
# Workflow
# =============================================================================


@workflow.defn
class DiagnosticsWorkflow(WorkflowMetadataMixin, StageMixin, DeviceMixin, ArchiveMixin):
    """Run diagnostic commands against network devices and attach results to a ticket."""

    workflow_name = "Device Diagnostics"
    workflow_description = (
        "Run diagnostic commands against network devices and attach results to a ticketing issue"
    )
    workflow_input_class = DiagnosticsWorkflowInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/ngc/diagnostics"
    workflow_namespace = "ngc"

    def __init__(self) -> None:
        StageMixin.__init__(self)
        self._jira_inaccessible: bool = False
        self._jira_warning: str = ""
        self.define_stage(
            name="validate_ticket",
            description="Confirm the ticket exists before running any device work",
            requires_approval=False,
            depends_on=[],
        )
        self.define_stage(
            name="resolve_devices",
            description="Fetch NetworkDeviceData for each requested device ID from the DCIM",
            requires_approval=False,
            depends_on=["validate_ticket"],
        )
        self.define_stage(
            name="run_diagnostics",
            description="Run diagnostic commands across all devices in parallel",
            requires_approval=False,
            depends_on=["resolve_devices"],
        )
        self.define_stage(
            name="collect_tech_support",
            description="Collect tech-support bundles from all devices (optional)",
            requires_approval=False,
            depends_on=["resolve_devices"],
        )
        self.define_stage(
            name="assemble_output",
            description="Merge all device diagnostic outputs into a single text file",
            requires_approval=False,
            depends_on=["run_diagnostics"],
        )
        self.define_stage(
            name="upload_attachment",
            description="Upload the diagnostics text file as a direct ticket attachment",
            requires_approval=False,
            depends_on=["assemble_output"],
        )
        self.define_stage(
            name="upload_tech_support",
            description="Upload each tech-support bundle as a ticket attachment (optional)",
            requires_approval=False,
            depends_on=["collect_tech_support"],
        )
        self.define_stage(
            name="post_comment",
            description="Post a summary comment on the ticket",
            requires_approval=False,
            depends_on=["upload_attachment"],
        )

    # -------------------------------------------------------------------------
    # Stage methods
    # -------------------------------------------------------------------------

    @stage_executor("validate_ticket")
    async def validate_ticket_stage(
        self, stage_input: ValidateTicketStageInput
    ) -> ValidateTicketStageOutput:
        try:
            result = await workflow.execute_activity(
                validate_ticket,
                ValidateTicketInput(
                    ticketing_platform=stage_input.ticketing_platform,
                    issue_key=stage_input.issue_key,
                ),
                start_to_close_timeout=_TICKET_TIMEOUT,
                retry_policy=_PREFLIGHT_RETRY,
            )
        except ActivityError as exc:
            reason = str(exc.cause) if exc.cause else str(exc)
            warning = (
                f"Warning: Could not verify Jira ticket {stage_input.issue_key} — {reason}. "
                f"Proceeding in ticketless mode."
            )
            workflow.logger.warning(warning)
            self._jira_inaccessible = True
            self._jira_warning = warning
            return ValidateTicketStageOutput(
                summary="",
                status="",
                url="",
                display=warning,
            )
        return ValidateTicketStageOutput(
            summary=result.summary,
            status=result.status,
            url=result.url,
            display=f"Ticket {stage_input.issue_key}: {result.summary} [{result.status}]",
        )

    @stage_executor("resolve_devices")
    async def resolve_devices_stage(
        self, stage_input: ResolveDevicesStageInput
    ) -> ResolveDevicesStageOutput:
        tasks = [
            workflow.execute_activity(
                get_network_device,
                GetNetworkDeviceInput(device_id=device_id),
                start_to_close_timeout=_DEVICE_LOOKUP_TIMEOUT,
                retry_policy=_DEFAULT_RETRY,
            )
            for device_id in stage_input.device_ids
        ]
        outputs = await asyncio.gather(*tasks)
        devices = [out.device for out in outputs]
        lines = [f"Resolved {len(devices)} device(s):"]
        for d in devices:
            lines.append(f"- **{d.name}** — platform: `{d.platform}`")
        return ResolveDevicesStageOutput(
            devices=devices,
            display="\n".join(lines),
        )

    @stage_executor("run_diagnostics")
    async def run_diagnostics_stage(
        self, stage_input: RunDiagnosticsStageInput
    ) -> RunDiagnosticsStageOutput:
        tasks = [
            workflow.execute_activity(
                run_diagnostic_commands,
                RunDiagnosticsInput(device_data=device, commands=stage_input.commands),
                start_to_close_timeout=_DIAGNOSTIC_CMD_TIMEOUT,
                retry_policy=_DIAGNOSTIC_RETRY,
            )
            for device in stage_input.devices
        ]
        results = await asyncio.gather(*tasks)
        successful = sum(
            1
            for r in results
            if r.outputs and not any(v.startswith("ERROR:") for v in r.outputs.values())
        )
        lines = [f"Ran diagnostics on {len(results)} device(s), {successful} fully successful\n"]
        for device, result in zip(stage_input.devices, results, strict=True):
            lines.append(f"**{result.device_name}** (platform: `{device.platform}`)")
            if not result.outputs:
                available = list(get_available_commands(device.platform).keys())
                avail_str = ", ".join(f"`{c}`" for c in available) if available else "_none_"
                lines.append(
                    f"_No commands ran. Requested: {stage_input.commands}. Available for `{device.platform}`: {avail_str}_"
                )
            else:
                for cmd, output in result.outputs.items():
                    lines.append(f"```\n>>> {cmd}\n{output}\n```")
        return RunDiagnosticsStageOutput(
            results=list(results),
            display="\n\n".join(lines),
        )

    @stage_executor("collect_tech_support")
    async def collect_tech_support_stage(
        self, stage_input: CollectTechSupportStageInput
    ) -> CollectTechSupportStageOutput:
        tasks = [
            workflow.execute_activity(
                collect_tech_support_bundle,
                TechSupportInput(device_data=device),
                start_to_close_timeout=_TECH_SUPPORT_TIMEOUT,
                heartbeat_timeout=timedelta(seconds=60),
                retry_policy=_EXPENSIVE_RETRY,
            )
            for device in stage_input.devices
        ]
        bundles = await asyncio.gather(*tasks)
        for bundle in bundles:
            if not bundle.download_url:
                raise ApplicationError(
                    f"No download URL generated for {bundle.device_name} — cannot deliver bundle.",
                    non_retryable=False,
                )
        lines = [f"Collected tech-support bundles from {len(bundles)} device(s):\n"]
        for bundle in bundles:
            lines.append(f"**{bundle.device_name}** — {bundle.download_url} (valid for 24 hours)")
            if bundle.cl_support_log:
                lines.append(f"```\n{bundle.cl_support_log.strip()}\n```")
        return CollectTechSupportStageOutput(
            bundles=list(bundles),
            display="\n\n".join(lines),
        )

    @stage_executor("assemble_output")
    async def assemble_output_stage(
        self, stage_input: AssembleOutputStageInput
    ) -> AssembleOutputStageOutput:
        # Pure in-workflow computation — no activity needed.
        safe_ts = stage_input.timestamp.replace(":", "-")
        key_part = stage_input.issue_key or "ticketless"
        filename = f"diagnostics_{key_part}_{safe_ts}.txt"
        content = _assemble_diagnostics_text(
            results=stage_input.results,
            issue_key=stage_input.issue_key,
            triggered_by=stage_input.triggered_by,
            commands=stage_input.commands,
            timestamp=stage_input.timestamp,
        )
        preview = content.decode("utf-8", errors="replace")[:2000]
        return AssembleOutputStageOutput(
            content=content,
            filename=filename,
            display=f"Assembled **{filename}** ({len(content)} bytes)\n\n```\n{preview}\n```",
        )

    @stage_executor("upload_attachment")
    async def upload_attachment_stage(
        self, stage_input: UploadAttachmentStageInput
    ) -> UploadAttachmentStageOutput:
        result = await workflow.execute_activity(
            upload_attachment,
            UploadAttachmentInput(
                ticketing_platform=stage_input.ticketing_platform,
                issue_key=stage_input.issue_key,
                filename=stage_input.filename,
                content=stage_input.content,
                content_type=stage_input.content_type,
            ),
            start_to_close_timeout=_UPLOAD_TIMEOUT,
            retry_policy=_DEFAULT_RETRY,
        )
        return UploadAttachmentStageOutput(
            attachment_id=result.attachment_id,
            attachment_url=result.attachment_url,
            display=f"Uploaded {stage_input.filename} → {result.attachment_url}",
        )

    @stage_executor("upload_tech_support")
    async def upload_tech_support_stage(
        self, stage_input: UploadTechSupportStageInput
    ) -> UploadTechSupportStageOutput:
        attachment_urls: list[str] = []
        tech_support_lines: list[str] = []
        download_urls: list[str] = []
        for bundle in stage_input.bundles:
            download_urls.append(bundle.download_url)
            download_line = (
                f"Tech-support bundle for {bundle.device_name}: "
                f"{bundle.download_url} (valid for 24 hours)"
            )
            try:
                result = await workflow.execute_activity(
                    upload_tech_support_from_redis,
                    UploadTechSupportFromRedisInput(
                        ticketing_platform=stage_input.ticketing_platform,
                        issue_key=stage_input.issue_key,
                        device_name=bundle.device_name,
                        redis_key=bundle.redis_key,
                    ),
                    start_to_close_timeout=_UPLOAD_LARGE_TIMEOUT,
                    retry_policy=_DEFAULT_RETRY,
                )
                attachment_urls.append(result.attachment_url)
                tech_support_lines.append(download_line)
            except ActivityError as exc:
                cause = exc.cause
                if isinstance(cause, ApplicationError) and cause.type == "attachment_too_large":
                    workflow.logger.warning(
                        "Tech-support bundle for %s exceeds %s attachment size limit: %s",
                        bundle.device_name,
                        stage_input.ticketing_platform,
                        str(cause),
                    )
                    tech_support_lines.append(
                        f"{download_line}\n  Note: Bundle exceeds the "
                        f"{stage_input.ticketing_platform} attachment size limit. "
                        f"Use the download URL instead."
                    )
                else:
                    raise
        return UploadTechSupportStageOutput(
            attachment_urls=attachment_urls,
            tech_support_lines=tech_support_lines,
            download_urls=download_urls,
            display=f"Uploaded {len(attachment_urls)} tech-support bundle(s)",
        )

    @stage_executor("post_comment")
    async def post_comment_stage(
        self, stage_input: PostCommentStageInput
    ) -> PostCommentStageOutput:
        result = await workflow.execute_activity(
            add_ticket_comment,
            AddCommentInput(
                ticketing_platform=stage_input.ticketing_platform,
                issue_key=stage_input.issue_key,
                body=stage_input.body,
            ),
            start_to_close_timeout=_COMMENT_TIMEOUT,
            retry_policy=_DEFAULT_RETRY,
        )
        return PostCommentStageOutput(
            comment_id=result.comment_id,
            display=f"Posted comment `{result.comment_id}` on {stage_input.issue_key}\n\n```\n{stage_input.body}\n```",
        )

    # -------------------------------------------------------------------------
    # Entrypoint
    # -------------------------------------------------------------------------

    def _mark_unreachable_stages(
        self, workflow_input: DiagnosticsWorkflowInput, ticketless: bool
    ) -> None:
        """Mark optional/skipped stages UNREACHABLE so they never appear as NOT_STARTED."""

        def skip_stage(stage_name: str) -> None:
            self.set_stage_state(
                stage_name,
                StateEnum.UNREACHABLE,
                cascade_unreachable=False,
            )

        if not workflow_input.include_tech_support:
            skip_stage("collect_tech_support")
            skip_stage("upload_tech_support")
        if ticketless:
            for stage in (
                "validate_ticket",
                "upload_attachment",
                "upload_tech_support",
                "post_comment",
            ):
                skip_stage(stage)

    async def _ticketless_result(
        self,
        workflow_input: DiagnosticsWorkflowInput,
        devices: list,
        assemble_output: AssembleOutputStageOutput,
        tech_support_bundles: list[TechSupportOutput],
    ) -> DiagnosticsWorkflowResult:
        """Build the result for ticketless (or Jira-inaccessible) runs."""
        tech_support_urls: list[str] = [
            b.download_url for b in tech_support_bundles if b.download_url
        ]
        await self.archive_results()
        return DiagnosticsWorkflowResult(
            issue_key="",
            devices_count=len(devices),
            commands_run=workflow_input.commands,
            attachment_url="",
            tech_support_urls=tech_support_urls,
            comment_id="",
            diagnostics_content=assemble_output.content.decode("utf-8", errors="replace"),
            warning=self._jira_warning,
        )

    @run_nv_config_manager_workflow
    async def run(  # type: ignore[override, ty:invalid-method-override]
        self, workflow_input: DiagnosticsWorkflowInput
    ) -> DiagnosticsWorkflowResult:
        self.set_input(workflow_input)
        if workflow_input.issue_key:
            workflow.upsert_search_attributes(
                {ISSUE_KEY_SEARCH_ATTRIBUTE: [workflow_input.issue_key]}
            )

        ticketless = not workflow_input.issue_key
        self._mark_unreachable_stages(workflow_input, ticketless)

        timestamp = workflow.now().strftime("%Y-%m-%dT%H:%M:%SZ")

        # Stage 1 — fail early if the ticket doesn't exist (skipped in ticketless mode)
        if not ticketless:
            await self.validate_ticket_stage(
                ValidateTicketStageInput(
                    ticketing_platform=workflow_input.ticketing_platform,
                    issue_key=workflow_input.issue_key,
                )
            )
            if self._jira_inaccessible:
                ticketless = True
                for stage_name in ("upload_attachment", "upload_tech_support", "post_comment"):
                    if self.get_stage_state(stage_name) == StateEnum.NOT_STARTED:
                        self.set_stage_state(
                            stage_name,
                            StateEnum.UNREACHABLE,
                            cascade_unreachable=False,
                        )

        # Stage 2 — resolve DCIM device identifiers → NetworkDeviceData
        resolve_output = await self.resolve_devices_stage(
            ResolveDevicesStageInput(device_ids=workflow_input.device_ids)
        )
        devices = resolve_output.devices

        # Stage 3 — run diagnostic commands across all devices in parallel
        diag_output = await self.run_diagnostics_stage(
            RunDiagnosticsStageInput(devices=devices, commands=workflow_input.commands)
        )

        # Stage 4 — optional: collect tech-support bundles
        tech_support_bundles: list[TechSupportOutput] = []
        if workflow_input.include_tech_support:
            ts_output = await self.collect_tech_support_stage(
                CollectTechSupportStageInput(devices=devices)
            )
            tech_support_bundles = ts_output.bundles

        # Stage 5 — merge all device outputs into a single text file
        assemble_output = await self.assemble_output_stage(
            AssembleOutputStageInput(
                results=diag_output.results,
                issue_key=workflow_input.issue_key,
                triggered_by=workflow_input.user,
                commands=workflow_input.commands,
                timestamp=timestamp,
            )
        )

        if ticketless:
            return await self._ticketless_result(
                workflow_input, devices, assemble_output, tech_support_bundles
            )

        # Stage 6 — upload diagnostics file as a direct ticket attachment
        upload_output = await self.upload_attachment_stage(
            UploadAttachmentStageInput(
                ticketing_platform=workflow_input.ticketing_platform,
                issue_key=workflow_input.issue_key,
                filename=assemble_output.filename,
                content=assemble_output.content,
                content_type="text/plain",
            )
        )

        # Stage 7 — optional: upload each tech-support bundle as its own attachment
        ticketed_tech_support_urls: list[str] = []
        ticketed_tech_support_comment_lines: list[str] = []
        if workflow_input.include_tech_support and tech_support_bundles:
            ts_upload_output = await self.upload_tech_support_stage(
                UploadTechSupportStageInput(
                    ticketing_platform=workflow_input.ticketing_platform,
                    issue_key=workflow_input.issue_key,
                    bundles=tech_support_bundles,
                )
            )
            ticketed_tech_support_urls = ts_upload_output.download_urls
            ticketed_tech_support_comment_lines = ts_upload_output.tech_support_lines

        # Stage 8 — post summary comment
        comment_body = _format_comment(
            issue_key=workflow_input.issue_key,
            triggered_by=workflow_input.user,
            devices=devices,
            commands=workflow_input.commands,
            diag_url=upload_output.attachment_url,
            tech_support_urls=ticketed_tech_support_comment_lines,
            timestamp=timestamp,
        )
        post_output = await self.post_comment_stage(
            PostCommentStageInput(
                ticketing_platform=workflow_input.ticketing_platform,
                issue_key=workflow_input.issue_key,
                body=comment_body,
            )
        )

        await self.archive_results()
        return DiagnosticsWorkflowResult(
            issue_key=workflow_input.issue_key,
            devices_count=len(devices),
            commands_run=workflow_input.commands,
            attachment_url=upload_output.attachment_url,
            tech_support_urls=ticketed_tech_support_urls,
            comment_id=post_output.comment_id,
        )
