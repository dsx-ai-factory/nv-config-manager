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
"""Integration tests for device configuration rendering.

These tests verify that the NVIDIA Config Manager render pipeline successfully generates
configurations for all render-enabled devices in the deployment.
"""

import time

import pytest
import requests

from tests.integration.dcim_adapter import DCIMIntegrationAdapter

# Mark all tests in this module as integration tests
pytestmark = pytest.mark.integration


class TestRenderPipeline:
    """Tests for the complete render pipeline."""

    def _get_queue_status(
        self,
        render_api_url: str,
        render_client: requests.Session,
    ) -> dict[str, int]:
        """Get the current render queue status."""
        try:
            response = render_client.get(
                f"{render_api_url}/v1/admin/consumers",
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()

            device_consumers = [
                consumer
                for consumer in data.get("consumers", [])
                if str(consumer.get("name", "")).endswith("-device")
            ]
            if not device_consumers or any(
                consumer.get("num_pending", -1) < 0 or consumer.get("num_ack_pending", -1) < 0
                for consumer in device_consumers
            ):
                return {"pending": -1, "ack_pending": -1}
            total_pending = sum(consumer["num_pending"] for consumer in device_consumers)
            total_ack_pending = sum(consumer["num_ack_pending"] for consumer in device_consumers)

            return {
                "pending": total_pending,
                "ack_pending": total_ack_pending,
            }
        except requests.RequestException:
            # If we can't reach the API, assume queues are not ready
            return {"pending": -1, "ack_pending": -1}

    @pytest.mark.timeout(60)
    def test_render_all_queues_enabled_devices(
        self,
        render_api_url: str,
        render_client: requests.Session,
    ) -> None:
        """Force render work into the queue before asserting that it drains."""
        response = render_client.post(
            f"{render_api_url}/v1/render/all",
            json={"commit_message": "Integration test render-all"},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        assert response.status_code == 202
        assert payload["queued_count"] == payload["total_devices"]
        assert payload["queued_count"] > 0

    @pytest.mark.timeout(660)  # 11 minutes - allow time for queue drain
    def test_render_queues_drain(
        self,
        render_api_url: str,
        render_client: requests.Session,
    ) -> None:
        """Test that render queues eventually drain to empty.

        This test waits for all pending render jobs to complete,
        with a maximum timeout of 10 minutes.
        """
        max_wait_seconds = 600
        poll_interval = 15
        elapsed = 0

        print("\n=== Waiting for render queues to drain ===")

        while elapsed < max_wait_seconds:
            status = self._get_queue_status(render_api_url, render_client)

            if status["pending"] == -1:
                print(f"  [{elapsed}s] Could not reach render API, retrying...")
                time.sleep(poll_interval)
                elapsed += poll_interval
                continue

            print(
                f"  [{elapsed}s] Pending: {status['pending']}, Ack Pending: {status['ack_pending']}"
            )

            if status["pending"] == 0 and status["ack_pending"] == 0:
                print("✅ Render queues are empty")
                # Give a bit more time for final database writes
                time.sleep(5)
                return

            time.sleep(poll_interval)
            elapsed += poll_interval

        pytest.fail(
            f"Render queues did not drain within {max_wait_seconds} seconds. "
            f"Final status: pending={status['pending']}, "
            f"ack_pending={status['ack_pending']}"
        )

    @pytest.mark.timeout(60)  # 1 minute for GraphQL query
    def test_all_devices_have_rendered_config(
        self,
        dcim_adapter: DCIMIntegrationAdapter,
    ) -> None:
        """Test that all render-enabled devices have a rendered configuration.

        This test uses the provider adapter to verify that every device
        with render_enabled=true has a non-null intended_config with a commit_id.
        """
        print("\n=== Verifying device render status ===")

        devices = dcim_adapter.list_devices(render_enabled=True)
        total_count = len(devices)

        print(f"Total render-enabled devices: {total_count}")

        if total_count == 0:
            pytest.fail(
                "No render-enabled devices found. Ensure the mock topology was loaded correctly."
            )

        # Find devices missing rendered config
        missing_render = [
            {
                "id": device["id"],
                "name": device.get("name", "unknown"),
            }
            for device in devices
            if device.get("intended_config") is None
        ]

        rendered_count = total_count - len(missing_render)

        print(f"Successfully rendered: {rendered_count}")
        print(f"Missing renders: {len(missing_render)}")

        if missing_render:
            missing_names = [d["name"] for d in missing_render[:10]]
            remaining = len(missing_render) - 10
            error_msg = (
                f"Found {len(missing_render)} devices without rendered config.\n"
                f"First 10: {missing_names}"
            )
            if remaining > 0:
                error_msg += f"\n... and {remaining} more"
            pytest.fail(error_msg)

        print(f"✅ All {total_count} render-enabled devices have rendered configs")

    @pytest.mark.timeout(60)  # 1 minute for GraphQL query
    def test_rendered_configs_have_commit_ids(
        self,
        dcim_adapter: DCIMIntegrationAdapter,
    ) -> None:
        """Test that all rendered configs have valid commit IDs.

        This ensures the config store integration is working correctly.
        """
        print("\n=== Verifying commit IDs in rendered configs ===")

        devices = dcim_adapter.list_devices(render_enabled=True)

        # Filter to devices that have intended_config but missing commit_id
        missing_commit_id = [
            {
                "id": device["id"],
                "name": device.get("name", "unknown"),
            }
            for device in devices
            if device.get("intended_config") is not None
            and not device["intended_config"].get("commit_id")
        ]

        if missing_commit_id:
            names = [d["name"] for d in missing_commit_id[:10]]
            pytest.fail(
                f"Found {len(missing_commit_id)} devices with intended_config "
                f"but missing commit_id: {names}"
            )

        rendered_count = sum(
            1 for d in devices if d.get("intended_config") and d["intended_config"].get("commit_id")
        )
        print(f"✅ All {rendered_count} rendered configs have valid commit IDs")
