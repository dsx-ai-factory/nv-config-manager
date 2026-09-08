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
"""Integration tests for DiagnosticsWorkflow — ticketless mode.

These tests run the DiagnosticsWorkflow with issue_key="" (ticketless mode) so
they work in environments that have no Jira access (e.g. the OOB management
server pointing at a Kind/local cluster with live devices).

Requirements:
  - Running nv-config-manager deployment (Kind or real cluster)
  - The selected DCIM loaded with at least one Cumulus Linux device
  - Live device reachable from the worker pod (for commands + tech-support)
  - config-manager.local service hostnames resolve to the Envoy Gateway address

Run example (from OOB management server):
  uv run pytest src/tests/integration/test_diagnostics_ticketless.py \\
      -v

  # With tech-support collection (slow — ~5 min per device):
  uv run pytest src/tests/integration/test_diagnostics_ticketless.py \\
      -v -k tech_support
"""

from __future__ import annotations

import time
from typing import Any

import pytest
import requests

pytestmark = pytest.mark.integration

DIAGNOSTICS_ENDPOINT = "/v1/workflow/ngc/diagnostics"
WORKFLOW_DETAIL_ENDPOINT = "/v1/workflow/{workflow_id}"

POLL_INTERVAL_S = 5
POLL_TIMEOUT_S = 180
TECH_SUPPORT_POLL_TIMEOUT_S = 600  # cl-support generation can take ~5 min

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
    """Poll until the workflow reaches a terminal state.

    kubectl port-forward and the local gateway can drop keep-alive connections
    between polls. Connection errors and gateway 503s are transient failures.
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
            # Port-forward dropped the connection between polls — retry on next tick.
            print(f"  [poll] connection dropped (retrying): {exc}")
        except requests.exceptions.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 503:
                raise
            print("  [poll] gateway upstream connection reset (retrying)")
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
# Fixtures
# =============================================================================


@pytest.fixture(scope="session")
def ticketless_input(dcim_device_ids: list[str]) -> dict[str, Any]:
    """Standard ticketless payload — no Jira fields, single device, fast commands."""
    return {
        "device_ids": [dcim_device_ids[0]],
        "commands": ["show_version"],
        "ticketing_platform": "",
        "issue_key": "",
        "include_tech_support": False,
        "user": "integration-test@nvidia.com",
    }


@pytest.fixture(scope="session")
def completed_ticketless_workflow(
    temporal_api_url: str,
    temporal_client: requests.Session,
    ticketless_input: dict[str, Any],
) -> dict[str, Any]:
    """Start a ticketless diagnostics workflow and wait for COMPLETE.

    Shared across tests so the device commands are only run once.
    """
    print("\n[fixtures] Starting ticketless diagnostics workflow...")
    workflow_id = _start_workflow(temporal_api_url, temporal_client, ticketless_input)
    print(f"[fixtures] workflow_id={workflow_id}")
    detail = _poll_to_terminal(temporal_api_url, temporal_client, workflow_id)
    if detail["status"] != "COMPLETED":
        pytest.fail(
            f"Ticketless workflow did not COMPLETE (status={detail['status']}). Check worker logs."
        )
    print("[fixtures] Ticketless workflow COMPLETED")
    return detail


# =============================================================================
# Tests — basic ticketless flow
# =============================================================================


@pytest.mark.timeout(30)
def test_ticketless_workflow_starts(
    temporal_api_url: str,
    temporal_client: requests.Session,
    ticketless_input: dict[str, Any],
) -> None:
    """POST with issue_key='' returns 200 with a workflow_id."""
    print("\n=== test_ticketless_workflow_starts ===")
    resp = temporal_client.post(
        f"{temporal_api_url}{DIAGNOSTICS_ENDPOINT}",
        json=ticketless_input,
        timeout=15,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "id" in body and body["id"], f"Response missing workflow id: {body}"
    print(f"[PASS] Workflow started: id={body['id']}")


@pytest.mark.timeout(POLL_TIMEOUT_S + 10)
def test_ticketless_workflow_completes(
    completed_ticketless_workflow: dict[str, Any],
) -> None:
    """Ticketless workflow reaches COMPLETED within the timeout."""
    print("\n=== test_ticketless_workflow_completes ===")
    assert completed_ticketless_workflow["status"] == "COMPLETED"
    print(f"[PASS] Workflow COMPLETED (id={completed_ticketless_workflow['id'][:8]}...)")


@pytest.mark.timeout(30)
def test_ticketless_jira_stages_unreachable(
    completed_ticketless_workflow: dict[str, Any],
) -> None:
    """Jira stages are UNREACHABLE; core stages are COMPLETE."""
    print("\n=== test_ticketless_jira_stages_unreachable ===")
    stages = completed_ticketless_workflow.get("stages", [])
    assert stages, "Response has no stages"
    state_by_name = {s["name"]: s["state"] for s in stages}
    print(f"  Stage states: {state_by_name}")

    for name in ["validate_ticket", "upload_attachment", "upload_tech_support", "post_comment"]:
        assert state_by_name.get(name) == "UNREACHABLE", (
            f"Stage '{name}' expected UNREACHABLE, got {state_by_name.get(name)!r}"
        )

    for name in ["resolve_devices", "run_diagnostics", "assemble_output"]:
        assert state_by_name.get(name) == "COMPLETE", (
            f"Stage '{name}' expected COMPLETE, got {state_by_name.get(name)!r}"
        )

    # collect_tech_support UNREACHABLE because include_tech_support=False
    assert state_by_name.get("collect_tech_support") == "UNREACHABLE", (
        f"collect_tech_support expected UNREACHABLE, got {state_by_name.get('collect_tech_support')!r}"
    )
    print("[PASS] Jira stages UNREACHABLE; core stages COMPLETE")


@pytest.mark.timeout(30)
def test_ticketless_result_has_diagnostics_content(
    completed_ticketless_workflow: dict[str, Any],
) -> None:
    """result.diagnostics_content contains the assembled diagnostics text."""
    print("\n=== test_ticketless_result_has_diagnostics_content ===")
    result = completed_ticketless_workflow.get("result")
    assert result is not None, "Workflow result is None after COMPLETED"

    content = result.get("diagnostics_content", "")
    assert content, f"diagnostics_content is empty: {result}"
    assert "DIAGNOSTICS REPORT" in content, f"diagnostics_content missing header:\n{content[:500]}"
    assert "ticketless" in content.lower(), (
        f"diagnostics_content should mention 'ticketless':\n{content[:500]}"
    )
    print(f"[PASS] diagnostics_content present ({len(content)} chars)")


@pytest.mark.timeout(30)
def test_ticketless_result_no_jira_fields(
    completed_ticketless_workflow: dict[str, Any],
) -> None:
    """result.attachment_url and result.comment_id are empty in ticketless mode."""
    print("\n=== test_ticketless_result_no_jira_fields ===")
    result = completed_ticketless_workflow.get("result", {})
    assert result.get("attachment_url", "") == "", (
        f"attachment_url should be empty in ticketless mode, got: {result.get('attachment_url')}"
    )
    assert result.get("comment_id", "") == "", (
        f"comment_id should be empty in ticketless mode, got: {result.get('comment_id')}"
    )
    print("[PASS] attachment_url and comment_id are empty")


@pytest.mark.timeout(30)
def test_ticketless_diagnostics_content_has_device_output(
    completed_ticketless_workflow: dict[str, Any],
    dcim_device_ids: list[str],
) -> None:
    """diagnostics_content includes show_version output from the target device."""
    print("\n=== test_ticketless_diagnostics_content_has_device_output ===")
    result = completed_ticketless_workflow.get("result", {})
    content = result.get("diagnostics_content", "")
    assert "show_version" in content, (
        f"'show_version' not found in diagnostics_content:\n{content[:500]}"
    )
    print("[PASS] show_version output present in diagnostics_content")


# =============================================================================
# Tests — multi-device ticketless
# =============================================================================


@pytest.mark.timeout(POLL_TIMEOUT_S + 10)
def test_ticketless_multiple_devices(
    temporal_api_url: str,
    temporal_client: requests.Session,
    dcim_device_ids: list[str],
) -> None:
    """Ticketless with 3 devices: result.devices_count == 3, all core stages COMPLETE."""
    print("\n=== test_ticketless_multiple_devices ===")
    if len(dcim_device_ids) < 3:
        pytest.skip(
            f"Need at least 3 Cumulus Linux devices in the DCIM, found {len(dcim_device_ids)}."
        )

    payload = {
        "device_ids": dcim_device_ids[:3],
        "commands": ["show_version"],
        "ticketing_platform": "",
        "issue_key": "",
        "include_tech_support": False,
        "user": "integration-test@nvidia.com",
    }
    workflow_id = _start_workflow(temporal_api_url, temporal_client, payload)
    detail = _poll_to_terminal(temporal_api_url, temporal_client, workflow_id)

    assert detail["status"] == "COMPLETED", (
        f"Multi-device ticketless workflow did not COMPLETE: {detail['status']}"
    )
    result = detail.get("result", {})
    assert result.get("devices_count") == 3, (
        f"Expected devices_count=3, got {result.get('devices_count')}"
    )
    print("[PASS] Ticketless workflow COMPLETED with devices_count=3")


# =============================================================================
# Tests — tech-support (slow, ~5 min per device)
# =============================================================================


@pytest.mark.timeout(TECH_SUPPORT_POLL_TIMEOUT_S + 30)
def test_ticketless_with_tech_support(
    temporal_api_url: str,
    temporal_client: requests.Session,
    dcim_device_ids: list[str],
) -> None:
    """include_tech_support=True in ticketless mode:
    - collect_tech_support stage reaches COMPLETE
    - result.tech_support_urls has one download URL per device
    - the download URL returns 200 with gzip content-type
    """
    print("\n=== test_ticketless_with_tech_support ===")
    payload = {
        "device_ids": [dcim_device_ids[0]],
        "commands": ["show_version"],
        "ticketing_platform": "",
        "issue_key": "",
        "include_tech_support": True,
        "user": "integration-test@nvidia.com",
    }
    workflow_id = _start_workflow(temporal_api_url, temporal_client, payload)
    print(
        f"  workflow_id={workflow_id} — waiting up to {TECH_SUPPORT_POLL_TIMEOUT_S}s for tech-support..."
    )
    detail = _poll_to_terminal(
        temporal_api_url, temporal_client, workflow_id, timeout=TECH_SUPPORT_POLL_TIMEOUT_S
    )

    assert detail["status"] == "COMPLETED", (
        f"Tech-support ticketless workflow did not COMPLETE: {detail['status']}"
    )

    stages = {s["name"]: s["state"] for s in detail.get("stages", [])}
    assert stages.get("collect_tech_support") == "COMPLETE", (
        f"collect_tech_support should be COMPLETE, got {stages.get('collect_tech_support')!r}"
    )
    assert stages.get("upload_tech_support") == "UNREACHABLE", (
        f"upload_tech_support should be UNREACHABLE in ticketless mode, "
        f"got {stages.get('upload_tech_support')!r}"
    )

    result = detail.get("result", {})
    urls = result.get("tech_support_urls", [])
    assert urls, f"tech_support_urls is empty: {result}"
    print(f"  tech_support_urls: {urls}")

    # Verify the download endpoint actually serves the bundle.
    download_url = urls[0]
    # Use the port-forwarded base URL if the stored URL has a different host.
    # The activity stores the URL using TEMPORAL_API_HOST from the worker env;
    # in a port-forward setup that URL has the real hostname, not localhost.
    # Re-construct the path using our known temporal_api_url to ensure reachability.
    from urllib.parse import urlparse

    parsed = urlparse(download_url)
    local_download_url = f"{temporal_api_url}{parsed.path}"
    print(f"  Fetching download URL: {local_download_url}")

    dl_resp = temporal_client.get(local_download_url, timeout=30, stream=True)
    assert dl_resp.status_code == 200, (
        f"Download endpoint returned {dl_resp.status_code}: {dl_resp.text[:200]}"
    )
    content_type = dl_resp.headers.get("Content-Type", "")
    assert "gzip" in content_type or "octet-stream" in content_type, (
        f"Unexpected Content-Type for tech-support bundle: {content_type}"
    )
    size = len(dl_resp.content)
    assert size > 0, "Downloaded tech-support bundle is empty"
    print(
        f"[PASS] Tech-support bundle downloaded successfully ({size:,} bytes, Content-Type: {content_type})"
    )
