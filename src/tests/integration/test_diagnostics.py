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
"""Integration tests for DiagnosticsWorkflow.

Requires:
  - Running Kind cluster with nv-config-manager deployed
  - Worker deployed with mockDevices=true (values-local-diagnostics.yaml)
  - Mock DCIM topology loaded: make topology
  - Jira server accessible from developer machine (corporate network)
  - A valid Jira issue key: --jira-issue-key GNI-1234 or JIRA_ISSUE_KEY env var

Run example:
  pytest src/tests/integration/test_diagnostics.py \\
      --override-ini="addopts=" \\
      --jira-issue-key GNI-1234

Polling:
  Workflows run against the mock topology so device commands complete quickly.
  Each poll waits up to POLL_TIMEOUT_S seconds (default 60) with POLL_INTERVAL_S
  (default 5) between calls.
"""

from __future__ import annotations

import os
import time
from typing import Any

import pytest
import requests

pytestmark = [
    pytest.mark.integration,
    # Jira is unreachable from CI — skip the entire module unless a Jira issue
    # key is explicitly provided via --jira-issue-key or JIRA_ISSUE_KEY env var.
    pytest.mark.skipif(
        not os.getenv("JIRA_ISSUE_KEY"),
        reason="JIRA_ISSUE_KEY not set — Jira unreachable in CI",
    ),
]

# Workflow API endpoint (matches workflow_api_endpoint = "/ngc/diagnostics")
DIAGNOSTICS_ENDPOINT = "/v1/workflow/ngc/diagnostics"
WORKFLOW_DETAIL_ENDPOINT = "/v1/workflow/{workflow_id}"

POLL_INTERVAL_S = 5
POLL_TIMEOUT_S = 180

# Terminal states returned by the API (WorkflowExecutionStatus.name)
_TERMINAL_STATES = {"COMPLETED", "FAILED", "TERMINATED", "CANCELED", "TIMED_OUT"}


# =============================================================================
# Helpers
# =============================================================================


def _poll_to_terminal(
    temporal_api_url: str,
    client: requests.Session,
    workflow_id: str,
    timeout: int = POLL_TIMEOUT_S,
    interval: int = POLL_INTERVAL_S,
) -> dict[str, Any]:
    """Poll GET /v1/workflow/{id} until status is a terminal state.

    Returns the final workflow detail dict.
    Raises pytest.fail if the timeout is exceeded.
    """
    url = f"{temporal_api_url}{WORKFLOW_DETAIL_ENDPOINT.format(workflow_id=workflow_id)}"
    deadline = time.monotonic() + timeout
    status = "UNKNOWN"

    while time.monotonic() < deadline:
        try:
            resp = client.get(url, timeout=10)
            resp.raise_for_status()
            detail = resp.json()
            status = detail.get("status", "UNKNOWN")
            print(f"  [poll] workflow {workflow_id[:8]}... status={status}")
            if status in _TERMINAL_STATES:
                return detail
        except requests.exceptions.ConnectionError as exc:
            print(f"  [poll] connection dropped (retrying): {exc}")

        time.sleep(interval)

    pytest.fail(
        f"Workflow {workflow_id} did not reach a terminal state within {timeout}s. "
        f"Last status: {status}"
    )


def _start_workflow(
    temporal_api_url: str,
    client: requests.Session,
    payload: dict[str, Any],
) -> str:
    """POST to the diagnostics endpoint and return the workflow_id."""
    resp = client.post(
        f"{temporal_api_url}{DIAGNOSTICS_ENDPOINT}",
        json=payload,
        timeout=15,
    )
    if resp.status_code == 403:
        pytest.fail(
            "DiagnosticsWorkflow returned 403. "
            "Verify RBAC config allows this role to execute DiagnosticsWorkflow."
        )
    if resp.status_code == 404:
        pytest.fail(
            "DiagnosticsWorkflow endpoint not found. "
            "Verify the worker is deployed and workflow is registered."
        )
    resp.raise_for_status()
    return resp.json()["id"]


# =============================================================================
# Session-scoped fixtures
# =============================================================================


@pytest.fixture(scope="session")
def jira_issue_key(request: pytest.FixtureRequest) -> str:
    """Valid Jira issue key for the happy-path diagnostics tests.

    Resolved from --jira-issue-key CLI option or JIRA_ISSUE_KEY env var.
    Tests that depend on this fixture are skipped when neither is set.
    """
    key = request.config.getoption("--jira-issue-key") or os.environ.get("JIRA_ISSUE_KEY")
    if not key:
        pytest.skip(
            "No Jira issue key provided. "
            "Pass --jira-issue-key GNI-1234 or set JIRA_ISSUE_KEY env var."
        )
    return str(key)


@pytest.fixture(scope="session")
def diagnostics_input(
    dcim_device_ids: list[str],
    jira_issue_key: str,
) -> dict[str, Any]:
    """Standard single-device diagnostics payload used by most happy-path tests."""
    return {
        "device_ids": [dcim_device_ids[0]],
        "commands": ["show_version"],
        "ticketing_platform": "jira",
        "issue_key": jira_issue_key,
        "include_tech_support": False,
        "triggered_by": "integration-test@nvidia.com",
    }


@pytest.fixture(scope="session")
def completed_workflow(
    temporal_api_url: str,
    temporal_client: requests.Session,
    diagnostics_input: dict[str, Any],
) -> dict[str, Any]:
    """Start a single-device diagnostics workflow and wait for COMPLETE.

    Shared across tests that only need to inspect a finished workflow,
    so the poll cost is paid once per session.
    """
    print("\n[fixtures] Starting diagnostics workflow for shared completed_workflow fixture...")
    workflow_id = _start_workflow(temporal_api_url, temporal_client, diagnostics_input)
    print(f"[fixtures] workflow_id={workflow_id}")

    detail = _poll_to_terminal(temporal_api_url, temporal_client, workflow_id)
    if detail["status"] != "COMPLETED":
        pytest.fail(
            f"Shared diagnostics workflow did not COMPLETE (status={detail['status']}). "
            f"Check worker logs and Jira connectivity."
        )
    print("[fixtures] Shared workflow COMPLETED")
    return detail


# =============================================================================
# Tests
# =============================================================================


@pytest.mark.timeout(30)
def test_diagnostics_workflow_starts(
    temporal_api_url: str,
    temporal_client: requests.Session,
    diagnostics_input: dict[str, Any],
) -> None:
    """POST /v1/workflow/ngc/diagnostics -> 200 and workflow_id in response."""
    print("\n=== test_diagnostics_workflow_starts ===")

    resp = temporal_client.post(
        f"{temporal_api_url}{DIAGNOSTICS_ENDPOINT}",
        json=diagnostics_input,
        timeout=15,
    )

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    body = resp.json()
    assert "id" in body, f"Response missing 'id' field: {body}"
    assert body["id"], "workflow_id is empty"
    print(f"[PASS] Workflow started: id={body['id']}")


@pytest.mark.timeout(POLL_TIMEOUT_S + 10)
def test_diagnostics_workflow_reaches_complete(
    completed_workflow: dict[str, Any],
) -> None:
    """Workflow status reaches COMPLETE within the timeout."""
    print("\n=== test_diagnostics_workflow_reaches_complete ===")
    assert completed_workflow["status"] == "COMPLETED", (
        f"Expected COMPLETED, got {completed_workflow['status']}"
    )
    print(f"[PASS] Workflow COMPLETED (id={completed_workflow['id'][:8]}...)")


@pytest.mark.timeout(30)
def test_diagnostics_workflow_stages_all_complete(
    completed_workflow: dict[str, Any],
) -> None:
    """After COMPLETE: 6 mandatory stages are COMPLETE; collect_tech_support is
    UNREACHABLE when include_tech_support=False."""
    print("\n=== test_diagnostics_workflow_stages_all_complete ===")

    stages = completed_workflow.get("stages", [])
    assert stages, "Response has no stages"

    state_by_name = {s["name"]: s["state"] for s in stages}
    print(f"  Stage states: {state_by_name}")

    mandatory = [
        "validate_ticket",
        "resolve_devices",
        "run_diagnostics",
        "assemble_output",
        "upload_attachment",
        "post_comment",
    ]
    for name in mandatory:
        assert name in state_by_name, f"Stage '{name}' not found in response"
        assert state_by_name[name] == "COMPLETE", (
            f"Stage '{name}' expected COMPLETE, got {state_by_name[name]}"
        )

    # collect_tech_support must be UNREACHABLE (set at top of run() before any activities)
    assert state_by_name.get("collect_tech_support") == "UNREACHABLE", (
        f"collect_tech_support expected UNREACHABLE, "
        f"got {state_by_name.get('collect_tech_support')}"
    )

    print("[PASS] All mandatory stages COMPLETE; collect_tech_support UNREACHABLE")


@pytest.mark.timeout(30)
def test_diagnostics_workflow_result_has_attachment_url(
    completed_workflow: dict[str, Any],
) -> None:
    """After COMPLETE, result.attachment_url is a non-empty string."""
    print("\n=== test_diagnostics_workflow_result_has_attachment_url ===")

    result = completed_workflow.get("result")
    assert result is not None, "Workflow result is None after COMPLETED"
    attachment_url = result.get("attachment_url", "")
    assert attachment_url, f"result.attachment_url is empty: {result}"
    print(f"[PASS] attachment_url={attachment_url}")


@pytest.mark.timeout(30)
def test_diagnostics_workflow_comment_posted(
    completed_workflow: dict[str, Any],
) -> None:
    """After COMPLETE, result.comment_id is a non-empty string."""
    print("\n=== test_diagnostics_workflow_comment_posted ===")

    result = completed_workflow.get("result")
    assert result is not None, "Workflow result is None"
    comment_id = result.get("comment_id", "")
    assert comment_id, f"result.comment_id is empty: {result}"
    print(f"[PASS] comment_id={comment_id}")


@pytest.mark.timeout(POLL_TIMEOUT_S + 10)
def test_diagnostics_workflow_with_tech_support(
    temporal_api_url: str,
    temporal_client: requests.Session,
    dcim_device_ids: list[str],
    jira_issue_key: str,
) -> None:
    """include_tech_support=True; result.tech_support_urls is non-empty and
    collect_tech_support + upload_tech_support stages are COMPLETE."""
    print("\n=== test_diagnostics_workflow_with_tech_support ===")

    payload = {
        "device_ids": [dcim_device_ids[0]],
        "commands": ["show_version"],
        "ticketing_platform": "jira",
        "issue_key": jira_issue_key,
        "include_tech_support": True,
        "triggered_by": "integration-test@nvidia.com",
    }
    workflow_id = _start_workflow(temporal_api_url, temporal_client, payload)
    detail = _poll_to_terminal(temporal_api_url, temporal_client, workflow_id)

    assert detail["status"] == "COMPLETED", (
        f"Workflow with tech support did not COMPLETE: {detail['status']}"
    )

    result = detail.get("result", {})
    tech_support_urls = result.get("tech_support_urls", [])
    assert tech_support_urls, (
        f"tech_support_urls is empty after include_tech_support=True: {result}"
    )

    stages = {s["name"]: s["state"] for s in detail.get("stages", [])}
    assert stages.get("collect_tech_support") == "COMPLETE", (
        f"collect_tech_support should be COMPLETE, got {stages.get('collect_tech_support')}"
    )
    assert stages.get("upload_tech_support") == "COMPLETE", (
        f"upload_tech_support should be COMPLETE, got {stages.get('upload_tech_support')}"
    )
    print(f"[PASS] tech_support_urls={tech_support_urls}")


@pytest.mark.timeout(60)
def test_diagnostics_workflow_invalid_ticket_fails(
    temporal_api_url: str,
    temporal_client: requests.Session,
    dcim_device_ids: list[str],
) -> None:
    """issue_key='NOTREAL-9999'; validate_ticket stage reaches FAILED quickly.

    The workflow may keep retrying (non_retryable enforcement depends on the
    deployed worker version), so we only assert the stage state, not the
    overall workflow terminal status.
    """
    print("\n=== test_diagnostics_workflow_invalid_ticket_fails ===")

    payload = {
        "device_ids": [dcim_device_ids[0]],
        "commands": ["show_version"],
        "ticketing_platform": "jira",
        "issue_key": "NOTREAL-9999",
        "include_tech_support": False,
        "triggered_by": "integration-test@nvidia.com",
    }
    workflow_id = _start_workflow(temporal_api_url, temporal_client, payload)

    # Poll until validate_ticket stage is FAILED (happens within seconds of start).
    url = f"{temporal_api_url}{WORKFLOW_DETAIL_ENDPOINT.format(workflow_id=workflow_id)}"
    deadline = time.monotonic() + 30
    validate_ticket_state = "NOT_STARTED"
    while time.monotonic() < deadline:
        resp = temporal_client.get(url, timeout=10)
        resp.raise_for_status()
        detail = resp.json()
        stages = {s["name"]: s["state"] for s in detail.get("stages", [])}
        validate_ticket_state = stages.get("validate_ticket", "NOT_STARTED")
        print(f"  [poll] validate_ticket={validate_ticket_state}")
        if validate_ticket_state == "FAILED":
            break
        time.sleep(POLL_INTERVAL_S)

    assert validate_ticket_state == "FAILED", (
        f"validate_ticket should reach FAILED for a non-existent ticket, "
        f"got {validate_ticket_state!r}"
    )
    print("[PASS] validate_ticket stage FAILED for non-existent ticket (as expected)")


@pytest.mark.timeout(POLL_TIMEOUT_S + 10)
def test_diagnostics_workflow_command_normalization(
    temporal_api_url: str,
    temporal_client: requests.Session,
    dcim_device_ids: list[str],
    jira_issue_key: str,
) -> None:
    """commands=['show version'] (spaces) normalizes to 'show_version'; workflow completes.
    Regression: validate_commands must normalize spaces to underscores end-to-end."""
    print("\n=== test_diagnostics_workflow_command_normalization ===")

    payload = {
        "device_ids": [dcim_device_ids[0]],
        "commands": ["show version"],  # spaces — must normalize to "show_version"
        "ticketing_platform": "jira",
        "issue_key": jira_issue_key,
        "include_tech_support": False,
        "triggered_by": "integration-test@nvidia.com",
    }
    workflow_id = _start_workflow(temporal_api_url, temporal_client, payload)
    detail = _poll_to_terminal(temporal_api_url, temporal_client, workflow_id)

    assert detail["status"] == "COMPLETED", (
        f"Workflow with space-separated command did not COMPLETE: {detail['status']}"
    )
    print("[PASS] Workflow COMPLETED with space-normalized command")


@pytest.mark.timeout(POLL_TIMEOUT_S + 10)
def test_diagnostics_workflow_unknown_commands_ignored(
    temporal_api_url: str,
    temporal_client: requests.Session,
    dcim_device_ids: list[str],
    jira_issue_key: str,
) -> None:
    """commands=['nonexistent_command']; workflow completes with empty outputs (no crash).
    Regression: unknown commands must be silently filtered, not error the workflow."""
    print("\n=== test_diagnostics_workflow_unknown_commands_ignored ===")

    payload = {
        "device_ids": [dcim_device_ids[0]],
        "commands": ["nonexistent_command"],
        "ticketing_platform": "jira",
        "issue_key": jira_issue_key,
        "include_tech_support": False,
        "triggered_by": "integration-test@nvidia.com",
    }
    workflow_id = _start_workflow(temporal_api_url, temporal_client, payload)
    detail = _poll_to_terminal(temporal_api_url, temporal_client, workflow_id)

    assert detail["status"] == "COMPLETED", (
        f"Workflow with unknown command did not COMPLETE: {detail['status']}"
    )
    print("[PASS] Workflow COMPLETED with unknown command silently ignored")


@pytest.mark.timeout(POLL_TIMEOUT_S + 10)
def test_diagnostics_workflow_multiple_devices(
    temporal_api_url: str,
    temporal_client: requests.Session,
    dcim_device_ids: list[str],
    jira_issue_key: str,
) -> None:
    """3 device_ids; result.devices_count == 3."""
    print("\n=== test_diagnostics_workflow_multiple_devices ===")

    if len(dcim_device_ids) < 3:
        pytest.skip(
            f"Need at least 3 Cumulus Linux devices in the DCIM, "
            f"found {len(dcim_device_ids)}. Load more mock devices: make topology"
        )

    payload = {
        "device_ids": dcim_device_ids[:3],
        "commands": ["show_version"],
        "ticketing_platform": "jira",
        "issue_key": jira_issue_key,
        "include_tech_support": False,
        "triggered_by": "integration-test@nvidia.com",
    }
    workflow_id = _start_workflow(temporal_api_url, temporal_client, payload)
    detail = _poll_to_terminal(temporal_api_url, temporal_client, workflow_id)

    assert detail["status"] == "COMPLETED", (
        f"Multi-device workflow did not COMPLETE: {detail['status']}"
    )

    result = detail.get("result", {})
    assert result.get("devices_count") == 3, (
        f"Expected devices_count=3, got {result.get('devices_count')}"
    )
    print(f"[PASS] Workflow COMPLETED with devices_count={result['devices_count']}")


@pytest.mark.timeout(30)
def test_diagnostics_workflow_search_attribute(
    completed_workflow: dict[str, Any],
    diagnostics_input: dict[str, Any],
) -> None:
    """IssueKey search attribute on the workflow equals the submitted issue_key."""
    print("\n=== test_diagnostics_workflow_search_attribute ===")

    search_attrs = completed_workflow.get("search_attributes", {})
    issue_key_attr = search_attrs.get("IssueKey", [])
    submitted_key = diagnostics_input["issue_key"]

    assert issue_key_attr, f"IssueKey search attribute is missing or empty: {search_attrs}"
    assert submitted_key in issue_key_attr, (
        f"IssueKey attribute {issue_key_attr!r} does not contain submitted key {submitted_key!r}"
    )
    print(f"[PASS] IssueKey search attribute = {issue_key_attr}")
