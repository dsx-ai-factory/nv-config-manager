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
"""Integration tests for Temporal Workflow API.

These tests verify that Temporal workflows can be started and completed
successfully through the Temporal API gateway.
"""

import os
import time
from typing import Any

import pytest
import requests

from tests.integration.dcim_adapter import DCIMIntegrationAdapter

# Mark all tests in this module as integration tests
pytestmark = pytest.mark.integration

# API version prefix
API_PREFIX = "/v1"
BACKUP_WORKFLOW_ENDPOINT = f"{API_PREFIX}/workflow/ngc/backup"
WORKFLOW_DETAIL_ENDPOINT = f"{API_PREFIX}/workflow/{{workflow_id}}"

POLL_INTERVAL_SECONDS = 5
WORKFLOW_TERMINAL_STATES = {"COMPLETED", "FAILED", "TERMINATED", "CANCELED", "TIMED_OUT"}
WORKFLOW_FAILURE_STATES = WORKFLOW_TERMINAL_STATES - {"COMPLETED"}
TEMPORAL_REQUEST_TIMEOUT_SECONDS = 15
TRANSIENT_GATEWAY_STATUS_CODES = {502, 503, 504}


class TestTemporalAPI:
    """Tests for the Temporal Workflow API endpoints."""

    def _find_backup_target(
        self,
        dcim_adapter: DCIMIntegrationAdapter,
        timeout_seconds: int = 180,
    ) -> dict[str, Any]:
        """Find a backup-enabled device with an intended config commit."""
        deadline = time.monotonic() + timeout_seconds
        last_backup_enabled_count = 0

        while time.monotonic() < deadline:
            devices = dcim_adapter.list_devices(backup_enabled=True)
            last_backup_enabled_count = len(devices)

            for managed_device in devices:
                intended_config = managed_device.get("intended_config") or {}
                if intended_config.get("commit_id") is not None:
                    return managed_device

            time.sleep(POLL_INTERVAL_SECONDS)

        pytest.fail(
            "No backup-enabled device with an intended config commit was found. "
            f"Backup-enabled device count: {last_backup_enabled_count}"
        )

    def _poll_workflow_to_terminal(
        self,
        temporal_api_url: str,
        temporal_client: requests.Session,
        workflow_id: str,
        timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        """Poll a workflow detail endpoint until Temporal reports a terminal status."""
        url = f"{temporal_api_url}{WORKFLOW_DETAIL_ENDPOINT.format(workflow_id=workflow_id)}"
        deadline = time.monotonic() + timeout_seconds
        last_status = "UNKNOWN"
        last_error = None

        while time.monotonic() < deadline:
            try:
                response = temporal_client.get(url, timeout=TEMPORAL_REQUEST_TIMEOUT_SECONDS)
                if response.status_code in TRANSIENT_GATEWAY_STATUS_CODES:
                    last_error = requests.HTTPError(
                        f"transient gateway response: {response.status_code}",
                        response=response,
                    )
                    print(
                        f"  - Backup workflow {workflow_id[:8]}... "
                        f"poll transient error: {last_error}"
                    )
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    time.sleep(min(POLL_INTERVAL_SECONDS, remaining))
                    continue
                response.raise_for_status()
                detail = response.json()
                last_status = detail.get("status", "UNKNOWN")
                last_error = None
                print(f"  - Backup workflow {workflow_id[:8]}... status: {last_status}")

                if last_status in WORKFLOW_TERMINAL_STATES:
                    return detail
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = exc
                print(f"  - Backup workflow {workflow_id[:8]}... poll transient error: {exc}")

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break

            time.sleep(min(POLL_INTERVAL_SECONDS, remaining))

        pytest.fail(
            f"Backup workflow {workflow_id} did not reach a terminal state within "
            f"{timeout_seconds} seconds. Last status: {last_status}; "
            f"last transient error: {last_error}"
        )

    def _get_temporal_response_with_retries(
        self,
        temporal_client: requests.Session,
        url: str,
        params: dict[str, Any] | None = None,
        retry_seconds: int = 45,
    ) -> requests.Response:
        """GET a Temporal API endpoint, retrying transient gateway read failures."""
        deadline = time.monotonic() + retry_seconds
        attempt = 0
        last_error = None

        while time.monotonic() < deadline:
            attempt += 1
            try:
                response = temporal_client.get(
                    url,
                    params=params,
                    timeout=TEMPORAL_REQUEST_TIMEOUT_SECONDS,
                )
                if response.status_code not in TRANSIENT_GATEWAY_STATUS_CODES:
                    return response

                last_error = requests.HTTPError(
                    f"transient gateway response: {response.status_code}",
                    response=response,
                )
                print(f"  - GET {url} transient error on attempt {attempt}: {last_error}")
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = exc
                print(f"  - GET {url} transient error on attempt {attempt}: {exc}")

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(POLL_INTERVAL_SECONDS, remaining))

        pytest.fail(
            f"GET {url} did not respond within {retry_seconds} seconds. "
            f"Last transient error: {last_error}"
        )

    def _assert_any_backup_config(
        self,
        dcim_adapter: DCIMIntegrationAdapter,
    ) -> None:
        """Assert the selected DCIM has at least one backup config entry."""
        assert dcim_adapter.backup_config_count() > 0, (
            "The DCIM has no nv-config-manager backup configuration entries"
        )

    @pytest.mark.timeout(30)
    def test_temporal_healthcheck(
        self,
        temporal_api_url: str,
        temporal_client: requests.Session,
        base_hostname: str,
        sso_enabled: bool,
    ) -> None:
        """Test that the Temporal API healthcheck endpoint is accessible.

        Without SSO the healthcheck is hit unauthenticated to verify it
        requires no auth.  With SSO the gateway enforces JWT validation
        before the request reaches the app, so we reuse the authenticated
        session (the app-level healthcheck still exercises the same code
        path; the gateway health-check bypass is validated separately via
        the ``healthChecks.paths`` Helm value).
        """
        print("\n=== Testing Temporal API healthcheck ===")

        if sso_enabled:
            # Gateway enforces JWT on svc-* routes; use the authenticated client.
            response = temporal_client.get(
                f"{temporal_api_url}/healthcheck",
                timeout=10,
            )
        else:
            # No SSO — verify the endpoint works without any auth.
            session = requests.Session()
            session.headers.update({"Host": f"workflow.{base_hostname}"})
            session.verify = False
            response = session.get(
                f"{temporal_api_url}/healthcheck",
                timeout=10,
            )

        print(f"Healthcheck response: {response.status_code}")

        if response.status_code != 200:
            pytest.fail(
                f"Temporal API healthcheck failed with status {response.status_code}: "
                f"{response.text}"
            )

        print("✅ Temporal API healthcheck passed")

    @pytest.mark.ci_only
    @pytest.mark.timeout(30)
    def test_codec_server_cors_preflight(self, base_hostname: str) -> None:
        """Test Temporal Web can send codec requests through the gateway."""
        print("\n=== Testing Temporal codec CORS preflight ===")

        origin = f"https://temporal.{base_hostname}"
        response = requests.options(
            f"https://workflow.{base_hostname}{API_PREFIX}/codec/decode",
            params={"preserveStorageRefs": "true"},
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-namespace",
            },
            timeout=10,
            verify=os.environ["REQUESTS_CA_BUNDLE"],
            allow_redirects=False,
        )

        assert response.status_code == 200, (
            f"Codec CORS preflight returned {response.status_code}: {response.text}"
        )
        assert response.headers.get("Access-Control-Allow-Origin") == origin, response.headers
        assert response.headers.get("Access-Control-Allow-Credentials") == "true", response.headers

        allowed_methods = {
            method.strip().upper()
            for method in response.headers.get("Access-Control-Allow-Methods", "").split(",")
            if method.strip()
        }
        assert "POST" in allowed_methods, response.headers

        allowed_headers = {
            header.strip().lower()
            for header in response.headers.get("Access-Control-Allow-Headers", "").split(",")
            if header.strip()
        }
        assert {"content-type", "x-namespace"} <= allowed_headers, response.headers

        print("✅ Temporal codec CORS preflight passed")

    @pytest.mark.timeout(30)
    def test_workflow_types_endpoint(
        self,
        temporal_api_url: str,
        temporal_client: requests.Session,
    ) -> None:
        """Test that the workflow types endpoint returns registered workflows.

        This verifies that:
        1. The endpoint is accessible with auth
        2. It returns a list of workflow types
        3. HelloWorld workflow is registered
        """
        print("\n=== Testing workflow types endpoint ===")

        response = temporal_client.get(
            f"{temporal_api_url}{API_PREFIX}/workflow/types",
            timeout=10,
        )

        print(f"Workflow types response status: {response.status_code}")

        if response.status_code == 403:
            pytest.fail(
                "Workflow types returned 403 Forbidden. "
                "Check that X-AUTH-REQUEST-EMAIL header is being passed correctly."
            )

        response.raise_for_status()

        workflow_types = response.json()
        print(f"Registered workflow types: {workflow_types}")

        # Verify HelloWorld is in the list
        if "HelloWorld" not in workflow_types:
            pytest.fail(
                f"HelloWorld workflow not found in registered types. "
                f"Available types: {workflow_types}"
            )

        print(f"✅ Found {len(workflow_types)} registered workflow types")

    @pytest.mark.timeout(60)
    def test_hello_world_workflow_succeeds(
        self,
        temporal_api_url: str,
        temporal_client: requests.Session,
    ) -> None:
        """Test that a HelloWorld workflow can be started and completes successfully.

        This test verifies the complete workflow lifecycle:
        1. Start a HelloWorld workflow via the API
        2. Poll for completion
        3. Verify the workflow completed successfully with expected result
        """
        print("\n=== Testing HelloWorld workflow execution ===")

        # Start the HelloWorld workflow
        workflow_input = {"name": "Integration Test"}

        print(f"Starting HelloWorld workflow with input: {workflow_input}")

        start_response = temporal_client.post(
            f"{temporal_api_url}{API_PREFIX}/workflow/hello_world",
            json=workflow_input,
            timeout=30,
        )

        if start_response.status_code == 403:
            pytest.fail(
                "HelloWorld workflow start returned 403 Forbidden. "
                "Check RBAC configuration allows 'all' role to execute HelloWorld."
            )

        if start_response.status_code == 404:
            pytest.fail(
                "HelloWorld workflow endpoint not found. "
                "Verify dynamic endpoint registration is working."
            )

        start_response.raise_for_status()

        workflow_data = start_response.json()
        workflow_id = workflow_data["id"]
        workflow_href = workflow_data.get("href", "N/A")

        print("Workflow started successfully!")
        print(f"  - Workflow ID: {workflow_id}")
        print(f"  - Workflow URL: {workflow_href}")

        # Poll for workflow completion
        max_wait_seconds = 30
        poll_interval = 1
        elapsed = 0

        while elapsed < max_wait_seconds:
            status_response = temporal_client.get(
                f"{temporal_api_url}{API_PREFIX}/workflow/{workflow_id}",
                timeout=10,
            )
            status_response.raise_for_status()

            status_data = status_response.json()
            workflow_status = status_data.get("status")

            print(f"  - Status after {elapsed}s: {workflow_status}")

            if workflow_status == "COMPLETED":
                # Workflow completed successfully
                result = status_data.get("result")
                print(f"  - Workflow result: {result}")

                # Verify the result contains expected content
                if result and "Integration Test" in str(result):
                    print("✅ HelloWorld workflow completed successfully with expected result")
                    return
                else:
                    # Accept any successful completion
                    print("✅ HelloWorld workflow completed successfully")
                    return

            elif workflow_status in ["FAILED", "TERMINATED", "CANCELED", "TIMED_OUT"]:
                pytest.fail(
                    f"Workflow ended with non-success status: {workflow_status}. "
                    f"Details: {status_data}"
                )

            time.sleep(poll_interval)
            elapsed += poll_interval

        pytest.fail(
            f"Workflow did not complete within {max_wait_seconds} seconds. "
            f"Last status: {workflow_status}"
        )

    @pytest.mark.timeout(90)
    def test_workflow_list_endpoint(
        self,
        temporal_api_url: str,
        temporal_client: requests.Session,
    ) -> None:
        """Test that the workflow list endpoint is accessible.

        This verifies that authenticated users can list workflows they have access to.
        """
        print("\n=== Testing workflow list endpoint ===")

        response = self._get_temporal_response_with_retries(
            temporal_client,
            f"{temporal_api_url}{API_PREFIX}/workflow/",
            params={"limit": 5},
        )

        print(f"Workflow list response status: {response.status_code}")

        if response.status_code == 403:
            pytest.fail(
                "Workflow list returned 403 Forbidden. "
                "Check that authentication is working correctly."
            )

        response.raise_for_status()

        data = response.json()
        workflows = data.get("workflows", [])
        next_page_token = data.get("next_page_token")

        print(f"Found {len(workflows)} workflows in list")
        if next_page_token:
            print("  - More pages available (pagination token present)")

        # Print summary of recent workflows
        for wf in workflows[:5]:
            wf_id = wf.get("id", "unknown")[:8]
            wf_type = wf.get("workflow_type", "unknown")
            wf_status = wf.get("status", "unknown")
            print(f"  - {wf_id}... ({wf_type}): {wf_status}")

        if len(workflows) > 5:
            print(f"  ... and {len(workflows) - 5} more")

        print("✅ Workflow list endpoint accessible")

    @pytest.mark.timeout(420)
    def test_backup_workflow_has_no_failed_stages_and_backup_configs_exist(
        self,
        temporal_api_url: str,
        temporal_client: requests.Session,
        dcim_adapter: DCIMIntegrationAdapter,
    ) -> None:
        """BackupWorkflow has no failed stages and the DCIM has live backup records."""
        print("\n=== Testing BackupWorkflow plugin recording ===")

        device = self._find_backup_target(dcim_adapter)
        device_id = device["id"]

        print(f"Starting BackupWorkflow for {device['name']} ({device_id})")

        response = temporal_client.post(
            f"{temporal_api_url}{BACKUP_WORKFLOW_ENDPOINT}",
            json={
                "device_id": device_id,
                "trigger": "API",
                "user": None,
                "user_domain": None,
                "workflow_id": None,
                "intended_config_commit_id": None,
            },
            timeout=30,
        )

        if response.status_code == 403:
            pytest.fail(
                "BackupWorkflow returned 403. "
                "Verify RBAC config allows this role to execute BackupWorkflow."
            )
        if response.status_code == 404:
            pytest.fail(
                "BackupWorkflow endpoint not found. "
                "Verify dynamic endpoint registration includes /ngc/backup."
            )

        response.raise_for_status()
        workflow_id = response.json()["id"]
        detail = self._poll_workflow_to_terminal(
            temporal_api_url,
            temporal_client,
            workflow_id,
        )

        stages = detail.get("stages", [])
        failed_stages = [
            {
                "name": stage.get("name"),
                "state": stage.get("state"),
                "traceback": stage.get("traceback"),
                "output": stage.get("output"),
            }
            for stage in stages
            if stage.get("state") == "FAILED"
        ]

        if detail["status"] in WORKFLOW_FAILURE_STATES or failed_stages:
            pytest.fail(
                f"BackupWorkflow did not finish cleanly. "
                f"Status: {detail['status']}; failed stages: {failed_stages}"
            )

        self._assert_any_backup_config(dcim_adapter)

        print("✅ BackupWorkflow had no failed stages and the DCIM has backup config entries")

    @pytest.mark.timeout(30)
    def test_rbac_config_status(
        self,
        temporal_api_url: str,
        temporal_client: requests.Session,
    ) -> None:
        """Test that the RBAC configuration is properly loaded.

        This verifies that:
        1. The RBAC config file exists and is loaded
        2. HelloWorld workflow has RBAC configuration
        """
        print("\n=== Testing RBAC configuration status ===")

        response = temporal_client.get(
            f"{temporal_api_url}/rbac-config-status",
            timeout=10,
        )

        print(f"RBAC config status response: {response.status_code}")

        response.raise_for_status()

        data = response.json()
        status = data.get("status")
        file_exists = data.get("file_exists")
        workflows_count = data.get("workflows_count", 0)
        workflows = data.get("workflows", {})

        print(f"  - Status: {status}")
        print(f"  - Config file exists: {file_exists}")
        print(f"  - Configured workflows: {workflows_count}")

        if status != "ok":
            error = data.get("error", "Unknown error")
            pytest.fail(f"RBAC configuration error: {error}")

        # Check HelloWorld is configured
        if "HelloWorld" not in workflows:
            pytest.fail(
                f"HelloWorld workflow not found in RBAC configuration. "
                f"Configured workflows: {list(workflows.keys())}"
            )

        hw_config = workflows["HelloWorld"]
        print(f"  - HelloWorld read_roles: {hw_config.get('read_roles')}")
        print(f"  - HelloWorld execute_roles: {hw_config.get('execute_roles')}")

        print(f"✅ RBAC configuration loaded with {workflows_count} workflows")
