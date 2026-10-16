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
"""End-to-end tests for DiagnosticsWorkflow.

Setup:
  - All tests use the session-scoped `env` fixture (conftest.py), which starts
    a real local Temporal dev server with IssueKey pre-registered as a search
    attribute.  start_time_skipping() cannot be used here because the workflow
    calls workflow.upsert_search_attributes({"IssueKey": ...}) and that command
    fails with 'search attribute IssueKey is not defined' in the embedded
    time-skipping server, causing every workflow task to be retried until timeout.
  - All activities are mocked so no real devices, Jira, NATS, or Nautobot are needed.

Code under test:
  nv_config_manager_workflows.workflows.diagnostics
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any, cast

import pytest
from nv_config_manager_dcim.workflow_models import NetworkDeviceData, Platform
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from nv_config_manager_workflows.activities.dcim import (
    GetNetworkDeviceInput,
    GetNetworkDeviceOutput,
)
from nv_config_manager_workflows.activities.diagnostics import (
    RunDiagnosticsInput,
    RunDiagnosticsOutput,
    TechSupportInput,
    TechSupportOutput,
    UploadTechSupportFromRedisInput,
)
from nv_config_manager_workflows.activities.nats import PublishNatsInput
from nv_config_manager_workflows.activities.ticketing import (
    AddCommentInput,
    AddCommentOutput,
    UploadAttachmentInput,
    UploadAttachmentOutput,
    ValidateTicketInput,
    ValidateTicketOutput,
)
from nv_config_manager_workflows.workflows.diagnostics import (
    DiagnosticsWorkflow,
    DiagnosticsWorkflowInput,
    DiagnosticsWorkflowResult,
)

_TEST_TIMEOUT = timedelta(seconds=30)


# =============================================================================
# Shared test data
# =============================================================================

ISSUE_KEY = "GNI-1234"
PLATFORM = "jira"
DEVICE_ID_1 = "aaaa0001-0000-0000-0000-000000000001"
DEVICE_ID_2 = "bbbb0002-0000-0000-0000-000000000002"
DEVICE_ID_3 = "cccc0003-0000-0000-0000-000000000003"

ATTACHMENT_URL = "https://jira.example.com/attachment/diagnostics-1"
COMMENT_ID = "77001"


def _make_device(device_id: str) -> NetworkDeviceData:
    return NetworkDeviceData(
        id=device_id,
        name=f"switch-{device_id[:4]}",
        role="tor-switch",
        platform=Platform.CUMULUS_LINUX,
        site="SITEA",
        device_type="sn5600",
        primary_ip4="192.0.2.1",
        primary_ip6=None,
    )


def _make_workflow_input(
    device_ids: list[str] | None = None,
    include_tech_support: bool = False,
    user: str = "eng@example.com",
    issue_key: str = ISSUE_KEY,
) -> DiagnosticsWorkflowInput:
    return DiagnosticsWorkflowInput(
        device_ids=device_ids or [DEVICE_ID_1],
        commands=["show_version"],
        ticketing_platform=PLATFORM,
        issue_key=issue_key,
        include_tech_support=include_tech_support,
        user=user,
    )


# =============================================================================
# Default mock activities
# =============================================================================


@activity.defn(name="validate_ticket")
async def mock_validate_ticket(inp: ValidateTicketInput) -> ValidateTicketOutput:
    return ValidateTicketOutput(
        summary="Switch link flapping",
        status="In Progress",
        url=f"https://jira.example.com/browse/{inp.issue_key}",
    )


@activity.defn(name="get_network_device")
async def mock_get_network_device(inp: GetNetworkDeviceInput) -> GetNetworkDeviceOutput:
    return GetNetworkDeviceOutput(device=_make_device(inp.device_id))


@activity.defn(name="run_diagnostic_commands")
async def mock_run_diagnostic_commands(inp: RunDiagnosticsInput) -> RunDiagnosticsOutput:
    return RunDiagnosticsOutput(
        device_name=inp.device_data.name,
        outputs={"show_version": "Cumulus Linux 5.9.0"},
    )


@activity.defn(name="collect_tech_support_bundle")
async def mock_collect_tech_support_bundle(inp: TechSupportInput) -> TechSupportOutput:
    return TechSupportOutput(
        device_name=inp.device_data.name,
        redis_key=f"tech_support:mock-workflow:{inp.device_data.name}",
        download_url=f"http://localhost:8000/v1/workflow/mock-workflow/tech-support/{inp.device_data.name}",
    )


@activity.defn(name="upload_attachment")
async def mock_upload_attachment(inp: UploadAttachmentInput) -> UploadAttachmentOutput:
    return UploadAttachmentOutput(
        attachment_id=ATTACHMENT_URL,
        attachment_url=ATTACHMENT_URL,
    )


@activity.defn(name="add_ticket_comment")
async def mock_add_ticket_comment(inp: AddCommentInput) -> AddCommentOutput:
    return AddCommentOutput(comment_id=COMMENT_ID)


@activity.defn(name="upload_tech_support_from_redis")
async def mock_upload_tech_support_from_redis(
    inp: UploadTechSupportFromRedisInput,
) -> UploadAttachmentOutput:
    url = f"{ATTACHMENT_URL}/tech-support-{inp.device_name}"
    return UploadAttachmentOutput(attachment_id=url, attachment_url=url)


@activity.defn(name="publish_nats")
async def mock_publish_nats(inp: PublishNatsInput) -> None:
    pass


_DEFAULT_ACTIVITIES = [
    mock_validate_ticket,
    mock_get_network_device,
    mock_run_diagnostic_commands,
    mock_collect_tech_support_bundle,
    mock_upload_attachment,
    mock_upload_tech_support_from_redis,
    mock_add_ticket_comment,
    mock_publish_nats,
]


def _worker(client: Any, task_queue: str, activities: Any = None) -> Worker:
    return Worker(
        client,
        task_queue=task_queue,
        workflows=[DiagnosticsWorkflow],
        activities=cast(Sequence[Callable[..., Any]], activities or _DEFAULT_ACTIVITIES),
        activity_executor=ThreadPoolExecutor(max_workers=10),
    )


# =============================================================================
# test_full_workflow_no_tech_support
# =============================================================================


@pytest.mark.asyncio
async def test_full_workflow_no_tech_support(env: WorkflowEnvironment) -> None:
    """include_tech_support=False: 6 mandatory stages reach COMPLETE;
    collect_tech_support is UNREACHABLE; result has attachment_url and comment_id."""
    task_queue = str(uuid.uuid4())
    async with _worker(env.client, task_queue):
        handle = await env.client.start_workflow(
            DiagnosticsWorkflow.run,
            _make_workflow_input(include_tech_support=False),
            id=str(uuid.uuid4()),
            task_queue=task_queue,
            execution_timeout=_TEST_TIMEOUT,
        )
        result: DiagnosticsWorkflowResult = await handle.result()
        stages = await handle.query("stages")

    assert isinstance(result, DiagnosticsWorkflowResult)
    assert result.attachment_url == ATTACHMENT_URL
    assert result.comment_id == COMMENT_ID
    assert result.tech_support_urls == []

    state_by_name = {s["name"]: s["state"] for s in stages}
    mandatory = [
        "validate_ticket",
        "resolve_devices",
        "run_diagnostics",
        "assemble_output",
        "upload_attachment",
        "post_comment",
    ]
    for name in mandatory:
        assert state_by_name[name] == "COMPLETE", f"{name} should be COMPLETE"

    assert state_by_name["collect_tech_support"] == "UNREACHABLE"
    assert state_by_name["upload_tech_support"] == "UNREACHABLE"


# =============================================================================
# test_full_workflow_with_tech_support
# =============================================================================


@pytest.mark.asyncio
async def test_full_workflow_with_tech_support(env: WorkflowEnvironment) -> None:
    """include_tech_support=True: all 8 stages reach COMPLETE;
    result.tech_support_urls has one entry per device."""
    task_queue = str(uuid.uuid4())
    async with _worker(env.client, task_queue):
        handle = await env.client.start_workflow(
            DiagnosticsWorkflow.run,
            _make_workflow_input(include_tech_support=True),
            id=str(uuid.uuid4()),
            task_queue=task_queue,
            execution_timeout=_TEST_TIMEOUT,
        )
        result: DiagnosticsWorkflowResult = await handle.result()
        stages = await handle.query("stages")

    state_by_name = {s["name"]: s["state"] for s in stages}
    for name in state_by_name:
        assert state_by_name[name] == "COMPLETE", f"{name} should be COMPLETE"

    assert len(result.tech_support_urls) == 1  # one device → one bundle


# =============================================================================
# test_workflow_falls_back_to_ticketless_on_invalid_ticket
# =============================================================================


@activity.defn(name="validate_ticket")
async def failing_validate_ticket(inp: ValidateTicketInput) -> ValidateTicketOutput:
    raise ApplicationError(
        f"Jira issue '{inp.issue_key}' not found",
        non_retryable=True,
    )


@pytest.mark.asyncio
async def test_workflow_falls_back_to_ticketless_on_invalid_ticket(
    env: WorkflowEnvironment,
) -> None:
    """validate_ticket raises ApplicationError; workflow falls back to ticketless mode;
    validate_ticket stage is COMPLETE with a warning, Jira stages are UNREACHABLE,
    result.warning carries the reason and diagnostics_content is populated."""
    task_queue = str(uuid.uuid4())
    activities = [
        failing_validate_ticket,
        mock_get_network_device,
        mock_run_diagnostic_commands,
        mock_collect_tech_support_bundle,
        mock_upload_attachment,
        mock_add_ticket_comment,
        mock_publish_nats,
    ]
    async with _worker(env.client, task_queue, activities):
        handle = await env.client.start_workflow(
            DiagnosticsWorkflow.run,
            _make_workflow_input(),
            id=str(uuid.uuid4()),
            task_queue=task_queue,
            execution_timeout=_TEST_TIMEOUT,
        )
        result: DiagnosticsWorkflowResult = await handle.result()
        stages = await handle.query("stages")

    assert isinstance(result, DiagnosticsWorkflowResult)
    assert result.warning != ""
    assert ISSUE_KEY in result.warning
    assert result.diagnostics_content != ""

    state_by_name = {s["name"]: s["state"] for s in stages}
    assert state_by_name["validate_ticket"] == "COMPLETE"
    for name in ("upload_attachment", "upload_tech_support", "post_comment"):
        assert state_by_name[name] == "UNREACHABLE", f"{name} should be UNREACHABLE"


# =============================================================================
# test_collect_tech_support_unreachable_at_start
# =============================================================================


@pytest.mark.asyncio
async def test_collect_tech_support_unreachable_at_start(env: WorkflowEnvironment) -> None:
    """Regression: UNREACHABLE must be set at TOP of run(), not in an else branch
    that only executes when validate_ticket succeeds.

    Even when validate_ticket fails and the workflow falls back to ticketless mode,
    collect_tech_support must be UNREACHABLE (not NOT_STARTED) when
    include_tech_support=False.
    """
    task_queue = str(uuid.uuid4())
    activities = [
        failing_validate_ticket,
        mock_get_network_device,
        mock_run_diagnostic_commands,
        mock_collect_tech_support_bundle,
        mock_upload_attachment,
        mock_add_ticket_comment,
        mock_publish_nats,
    ]
    async with _worker(env.client, task_queue, activities):
        handle = await env.client.start_workflow(
            DiagnosticsWorkflow.run,
            _make_workflow_input(),
            id=str(uuid.uuid4()),
            task_queue=task_queue,
            execution_timeout=_TEST_TIMEOUT,
        )
        await handle.result()
        stages = await handle.query("stages")

    state_by_name = {s["name"]: s["state"] for s in stages}
    assert state_by_name["validate_ticket"] == "COMPLETE"
    # UNREACHABLE must be set before validate_ticket is awaited in run()
    assert state_by_name["collect_tech_support"] == "UNREACHABLE"


# =============================================================================
# test_user_field_in_comment_body
# =============================================================================


@pytest.mark.asyncio
async def test_user_field_in_comment_body(env: WorkflowEnvironment) -> None:
    """DiagnosticsWorkflowInput(user='eng@example.com'); the body passed to
    add_ticket_comment contains 'eng@example.com'."""
    captured_bodies: list[str] = []

    @activity.defn(name="add_ticket_comment")
    async def capturing_comment(inp: AddCommentInput) -> AddCommentOutput:
        captured_bodies.append(inp.body)
        return AddCommentOutput(comment_id=COMMENT_ID)

    activities = [
        mock_validate_ticket,
        mock_get_network_device,
        mock_run_diagnostic_commands,
        mock_collect_tech_support_bundle,
        mock_upload_attachment,
        capturing_comment,
        mock_publish_nats,
    ]
    task_queue = str(uuid.uuid4())
    async with _worker(env.client, task_queue, activities):
        await env.client.execute_workflow(
            DiagnosticsWorkflow.run,
            _make_workflow_input(user="eng@example.com"),
            id=str(uuid.uuid4()),
            task_queue=task_queue,
            execution_timeout=_TEST_TIMEOUT,
        )

    assert len(captured_bodies) == 1
    assert "eng@example.com" in captured_bodies[0]


# =============================================================================
# test_parallel_device_execution
# =============================================================================


@pytest.mark.asyncio
async def test_parallel_device_execution(env: WorkflowEnvironment) -> None:
    """3 device_ids provided; run_diagnostic_commands is called once per device (3 total)."""
    call_count: list[str] = []

    @activity.defn(name="run_diagnostic_commands")
    async def counting_run_diagnostics(inp: RunDiagnosticsInput) -> RunDiagnosticsOutput:
        call_count.append(inp.device_data.name)
        return RunDiagnosticsOutput(
            device_name=inp.device_data.name,
            outputs={"show_version": "ok"},
        )

    activities = [
        mock_validate_ticket,
        mock_get_network_device,
        counting_run_diagnostics,
        mock_collect_tech_support_bundle,
        mock_upload_attachment,
        mock_add_ticket_comment,
        mock_publish_nats,
    ]
    task_queue = str(uuid.uuid4())
    async with _worker(env.client, task_queue, activities):
        result: DiagnosticsWorkflowResult = await env.client.execute_workflow(
            DiagnosticsWorkflow.run,
            _make_workflow_input(device_ids=[DEVICE_ID_1, DEVICE_ID_2, DEVICE_ID_3]),
            id=str(uuid.uuid4()),
            task_queue=task_queue,
            execution_timeout=_TEST_TIMEOUT,
        )

    assert result.devices_count == 3
    assert len(call_count) == 3


# =============================================================================
# test_issue_key_search_attribute_set
# =============================================================================


@pytest.mark.asyncio
async def test_issue_key_search_attribute_set(env: WorkflowEnvironment) -> None:
    """After run(), workflow search attribute IssueKey equals the submitted issue_key."""
    task_queue = str(uuid.uuid4())
    async with _worker(env.client, task_queue):
        handle = await env.client.start_workflow(
            DiagnosticsWorkflow.run,
            _make_workflow_input(issue_key="GNI-9999"),
            id=str(uuid.uuid4()),
            task_queue=task_queue,
            execution_timeout=_TEST_TIMEOUT,
        )
        await handle.result()
        desc = await handle.describe()

    issue_key_attr = desc.search_attributes.get("IssueKey")
    assert issue_key_attr == ["GNI-9999"]


# =============================================================================
# test_ticketless_mode
# =============================================================================


@pytest.mark.asyncio
async def test_ticketless_mode(env: WorkflowEnvironment) -> None:
    """issue_key='': Jira stages (validate_ticket, upload_attachment,
    upload_tech_support, post_comment) are UNREACHABLE; diagnostics_content
    has the assembled text; attachment_url and comment_id are empty."""
    task_queue = str(uuid.uuid4())
    async with _worker(env.client, task_queue):
        handle = await env.client.start_workflow(
            DiagnosticsWorkflow.run,
            _make_workflow_input(issue_key=""),
            id=str(uuid.uuid4()),
            task_queue=task_queue,
            execution_timeout=_TEST_TIMEOUT,
        )
        result: DiagnosticsWorkflowResult = await handle.result()
        stages = await handle.query("stages")

    state_by_name = {s["name"]: s["state"] for s in stages}

    # Jira stages must be UNREACHABLE
    for name in ["validate_ticket", "upload_attachment", "upload_tech_support", "post_comment"]:
        assert state_by_name[name] == "UNREACHABLE", f"{name} should be UNREACHABLE"

    # Core stages must complete
    for name in ["resolve_devices", "run_diagnostics", "assemble_output"]:
        assert state_by_name[name] == "COMPLETE", f"{name} should be COMPLETE"

    # collect_tech_support UNREACHABLE because include_tech_support=False
    assert state_by_name["collect_tech_support"] == "UNREACHABLE"

    # Result fields
    assert result.attachment_url == ""
    assert result.comment_id == ""
    assert "DIAGNOSTICS REPORT" in result.diagnostics_content
    assert "ticketless" in result.diagnostics_content.lower()


# =============================================================================
# test_ticketless_mode_with_tech_support
# =============================================================================


@pytest.mark.asyncio
async def test_ticketless_mode_with_tech_support(env: WorkflowEnvironment) -> None:
    """issue_key='' with include_tech_support=True: collect_tech_support runs,
    tech_support_urls contains device paths from cl_support_log."""
    task_queue = str(uuid.uuid4())
    async with _worker(env.client, task_queue):
        result: DiagnosticsWorkflowResult = await env.client.execute_workflow(
            DiagnosticsWorkflow.run,
            _make_workflow_input(issue_key="", include_tech_support=True),
            id=str(uuid.uuid4()),
            task_queue=task_queue,
            execution_timeout=_TEST_TIMEOUT,
        )

    assert result.attachment_url == ""
    assert result.comment_id == ""
    # mock returns download_url → tech_support_urls has one entry per device
    assert len(result.tech_support_urls) == 1
    assert "tech-support" in result.tech_support_urls[0]
