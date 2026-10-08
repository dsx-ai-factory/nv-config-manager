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
"""Integration tests for ZTP (Zero Touch Provisioning) API.

These tests verify that the ZTP API correctly serves configuration files
for ZTP-enabled devices and properly updates device status to Provisioned.
"""

from hashlib import sha256
from typing import Any
from uuid import uuid4

import paramiko
import pytest
import requests
from paramiko import Transport

from tests.integration.dcim_adapter import DCIMIntegrationAdapter

# Mark all tests in this module as integration tests
pytestmark = pytest.mark.integration


class TestZTPAPI:
    """Tests for the ZTP API endpoints."""

    @staticmethod
    def _get_config_filename(managed_device: dict[str, Any]) -> str:
        """Extract config filename from device's intended_config.path."""
        intended_config = managed_device.get("intended_config")
        if intended_config and intended_config.get("path"):
            config_path = intended_config["path"]
            # Extract filename from path (e.g., "startup.yaml" from full path)
            return config_path.split("/")[-1] if "/" in config_path else config_path
        # Default fallback
        return "startup.yaml"

    @pytest.mark.timeout(120)  # 2 minutes for fetching all configs
    def test_ztp_devices_can_fetch_config(
        self,
        ztp_api_url: str,
        dcim_adapter: DCIMIntegrationAdapter,
        ztp_client: requests.Session,
    ) -> None:
        """Test that all ZTP-enabled devices can fetch their config file.

        For each device with ztp_enabled=true, this test attempts to fetch
        the config file (using the path from intended_config) from the ZTP API.
        """
        print("\n=== Testing ZTP config fetch for all ZTP-enabled devices ===")

        devices = dcim_adapter.list_devices(ztp_enabled=True)
        total_count = len(devices)

        print(f"Total ZTP-enabled devices: {total_count}")

        if total_count == 0:
            pytest.skip("No ZTP-enabled devices found in the deployment")

        # Track results
        successful = []
        failed = []

        for managed_device in devices:
            device_id = managed_device["id"]
            device_name = managed_device["name"]
            config_filename = self._get_config_filename(managed_device)

            try:
                # Fetch config file from ZTP API
                response = ztp_client.get(
                    f"{ztp_api_url}/v1/device/{device_id}/config/{config_filename}",
                    timeout=30,
                )

                if response.status_code == 200:
                    content = response.text
                    # Basic validation - should have some content
                    if content and len(content) > 10:
                        successful.append(device_name)
                        print(
                            f"  ✅ {device_name}: {config_filename} fetched ({len(content)} bytes)"
                        )
                    else:
                        failed.append(
                            {
                                "name": device_name,
                                "error": f"Empty or too short content ({len(content)} bytes)",
                            }
                        )
                        print(f"  ❌ {device_name}: Empty or invalid content")
                else:
                    failed.append(
                        {
                            "name": device_name,
                            "error": f"HTTP {response.status_code}: {response.text[:100]}",
                        }
                    )
                    print(f"  ❌ {device_name}: HTTP {response.status_code}")

            except requests.RequestException as e:
                failed.append({"name": device_name, "error": str(e)})
                print(f"  ❌ {device_name}: {e}")

        # Summary
        print("\n=== ZTP Config Fetch Summary ===")
        print(f"Successful: {len(successful)}/{total_count}")
        print(f"Failed: {len(failed)}/{total_count}")

        if failed:
            error_details = "\n".join(f"  - {f['name']}: {f['error']}" for f in failed[:10])
            remaining = len(failed) - 10
            error_msg = f"Failed to fetch config for {len(failed)} devices:\n{error_details}"
            if remaining > 0:
                error_msg += f"\n  ... and {remaining} more"
            pytest.fail(error_msg)

        print(f"✅ All {total_count} ZTP-enabled devices can fetch their config")

    @pytest.mark.timeout(120)  # 2 minutes
    def test_provisioned_endpoint_updates_status(
        self,
        ztp_api_url: str,
        dcim_adapter: DCIMIntegrationAdapter,
        ztp_client: requests.Session,
    ) -> None:
        """Test that the provisioned endpoint updates device status in the DCIM.

        This test:
        1. Finds a ZTP-enabled device
        2. Calls the provisioned endpoint
        3. Verifies the device status changed to 'Provisioned' in the selected DCIM
        """
        print("\n=== Testing ZTP provisioned endpoint ===")

        devices = dcim_adapter.list_devices(ztp_enabled=True)

        if not devices:
            pytest.skip("No ZTP-enabled devices found in the deployment")

        # Find a device that is NOT already Provisioned (to test the status change)
        test_device = None
        for device in devices:
            status = device["status"]
            if status != "Provisioned":
                test_device = device
                break

        if not test_device:
            # All devices are already provisioned - just verify the endpoint works
            test_device = devices[0]
            print(f"All devices already Provisioned, testing endpoint on {test_device['name']}")

        device_id = test_device["id"]
        device_name = test_device["name"]
        initial_status = test_device["status"]

        print(f"Testing device: {device_name}")
        print(f"Initial status: {initial_status}")

        # Call the provisioned endpoint
        try:
            response = ztp_client.post(
                f"{ztp_api_url}/v1/device/{device_id}/provisioned",
                timeout=30,
            )
            response.raise_for_status()
            print(f"Provisioned endpoint response: {response.text}")
        except requests.RequestException as e:
            pytest.fail(f"Failed to call provisioned endpoint: {e}")

        # Verify the status changed in the selected DCIM.
        new_status = dcim_adapter.get_device_status(device_id)
        print(f"New status: {new_status}")

        if new_status != "Provisioned":
            pytest.fail(
                f"Device status did not change to 'Provisioned'. "
                f"Expected: 'Provisioned', Got: '{new_status}'"
            )

        print(f"✅ Device {device_name} successfully marked as Provisioned")

    @pytest.mark.timeout(60)
    def test_ztp_devices_exist(
        self,
        dcim_adapter: DCIMIntegrationAdapter,
    ) -> None:
        """Test that ZTP-enabled devices exist in the deployment.

        This is a basic sanity check to ensure the mock topology
        includes devices with ZTP enabled.
        """
        print("\n=== Checking for ZTP-enabled devices ===")

        devices = dcim_adapter.list_devices(ztp_enabled=True)
        total_count = len(devices)

        print(f"Found {total_count} ZTP-enabled devices:")
        for device in devices[:10]:
            name = device["name"]
            status = device["status"]
            print(f"  - {name} (status: {status})")

        if total_count > 10:
            print(f"  ... and {total_count - 10} more")

        if total_count == 0:
            pytest.fail(
                "No ZTP-enabled devices found. "
                "Ensure the mock topology includes devices with ztp_enabled=true."
            )

        print(f"✅ Found {total_count} ZTP-enabled devices")


class TestZTPFileStore:
    """CI-only tests that mutate the ephemeral Kind FileStore."""

    @pytest.mark.ci_only
    @pytest.mark.timeout(60)
    def test_upload_and_download_round_trip(
        self,
        kind_filestore_deployment: None,
        ztp_api_url: str,
        ztp_client: requests.Session,
    ) -> None:
        """Upload a dummy file, then verify full and ranged downloads."""
        transfer_id = uuid4().hex
        platform = "ci-filestore"
        version = "round-trip"
        filename = f"payload-{transfer_id}.bin"
        seed = f"nv-config-manager FileStore round-trip {transfer_id}\n".encode()
        payload = (seed * ((256 * 1024 // len(seed)) + 1))[: 256 * 1024]
        checksum = sha256(payload).hexdigest()
        object_url = f"{ztp_api_url}/v1/files/{platform}/{version}/{filename}"

        upload_response = ztp_client.post(
            object_url,
            params={"checksum": checksum},
            files={"file": (filename, payload, "application/octet-stream")},
            headers={"Content-Type": None},
            timeout=60,
        )
        upload_response.raise_for_status()
        assert upload_response.json() == "OK"

        download_response = ztp_client.get(object_url, timeout=60)
        download_response.raise_for_status()
        assert download_response.headers["Accept-Ranges"] == "bytes"
        assert int(download_response.headers["Content-Length"]) == len(payload)
        assert download_response.content == payload
        assert sha256(download_response.content).hexdigest() == checksum

        range_start = 17
        range_end = len(payload) - 23
        range_response = ztp_client.get(
            object_url,
            headers={"Range": f"bytes={range_start}-{range_end}"},
            timeout=60,
        )
        assert range_response.status_code == 206
        assert range_response.headers["Content-Range"] == (
            f"bytes {range_start}-{range_end}/{len(payload)}"
        )
        assert int(range_response.headers["Content-Length"]) == range_end - range_start + 1
        assert range_response.content == payload[range_start : range_end + 1]

        checksum_response = ztp_client.get(f"{object_url}/checksum", timeout=30)
        checksum_response.raise_for_status()
        assert checksum_response.json() == {"checksum": checksum}


class TestZTPSFTP:
    """Tests for the ZTP SFTP server."""

    def _get_ztp_device(
        self,
        dcim_adapter: DCIMIntegrationAdapter,
    ) -> tuple[str, str, str] | None:
        """Get a single ZTP-enabled device through the selected provider adapter.

        Returns:
            Tuple of (device_id, device_name, config_filename) or None if not found.
            The config_filename is extracted from the intended_config.path field.
        """
        devices = dcim_adapter.list_devices(ztp_enabled=True)
        if not devices:
            return None

        # Find a device that has intended_config with a path
        for managed_device in devices:
            intended_config = managed_device.get("intended_config")
            if intended_config and intended_config.get("path"):
                # Extract filename from path (e.g., "startup.yaml" from full path)
                config_path = intended_config["path"]
                # The path may be just a filename or a full path
                config_filename = config_path.split("/")[-1] if "/" in config_path else config_path
                return (managed_device["id"], managed_device["name"], config_filename)

        # Fallback: return first device with a default filename
        device = devices[0]
        return (device["id"], device["name"], "startup.yaml")

    @pytest.mark.timeout(60)
    def test_sftp_fetch_config(
        self,
        sftp_host_port: tuple[str, int],
        dcim_adapter: DCIMIntegrationAdapter,
    ) -> None:
        """Test that a device's config file can be fetched via SFTP.

        This test:
        1. Gets a ZTP-enabled device from the DCIM (with its config filename)
        2. Connects to the SFTP server
        3. Downloads the device's config file
        4. Verifies the content is valid

        Note: The SFTP server validates client IP against device addresses.
        This test may fail if the port-forward IP (localhost) doesn't match
        the device's registered addresses. In such cases, configure the
        SFTP server with a test mode or add localhost to allowed addresses.
        """
        host, port = sftp_host_port
        print(f"\n=== Testing SFTP config fetch at {host}:{port} ===")

        # Get a ZTP-enabled device with its config filename
        device = self._get_ztp_device(dcim_adapter)
        if not device:
            pytest.skip("No ZTP-enabled devices found in the deployment")

        device_id, device_name, config_filename = device
        print(f"Testing device: {device_name} (ID: {device_id})")
        print(f"Config filename: {config_filename}")

        transport: Transport | None = None
        sftp: paramiko.SFTPClient | None = None

        try:
            # Connect to the SFTP server
            # Username can be any value - authentication always succeeds
            transport = Transport((host, port))
            transport.connect(username=device_name, password="")

            # Open SFTP session
            sftp = paramiko.SFTPClient.from_transport(transport)
            if sftp is None:
                pytest.fail("Could not open SFTP session")

            # Download the config file
            # Path format: /device/{device_id}/{config_file}
            config_path = f"/device/{device_id}/{config_filename}"
            print(f"Fetching: {config_path}")

            with sftp.open(config_path, "r") as f:
                content = f.read()

            # Validate content
            if isinstance(content, bytes):
                content_str = content.decode("utf-8")
            else:
                content_str = content

            content_len = len(content_str)
            if content_len > 10:
                print(f"✅ {config_filename} fetched successfully ({content_len} bytes)")
                # Print first few lines for debugging
                lines = content_str.split("\n")[:5]
                print("Content preview:")
                for line in lines:
                    print(f"  {line}")
            else:
                pytest.fail(f"{config_filename} content too short ({content_len} bytes)")

        except paramiko.SSHException as e:
            pytest.fail(f"SSH/SFTP connection failed: {e}")
        except OSError as e:
            # Covers both network errors and SFTP file errors (IOError is alias for OSError)
            pytest.fail(f"Connection or file error: {e}")
        finally:
            if sftp:
                sftp.close()
            if transport:
                transport.close()

    @pytest.mark.timeout(30)
    def test_sftp_healthcheck(self, sftp_host_port: tuple[str, int]) -> None:
        """Test that the SFTP server healthcheck endpoint works.

        This test connects to the SFTP server using paramiko, downloads
        the /healthcheck file, and verifies it contains 'OK'.

        This ensures proper SSH/SFTP protocol negotiation (not just TCP connectivity).
        """
        host, port = sftp_host_port
        print(f"\n=== Testing SFTP healthcheck at {host}:{port} ===")

        transport: Transport | None = None
        sftp: paramiko.SFTPClient | None = None

        try:
            # Connect to the SFTP server
            transport = Transport((host, port))
            transport.connect(username="healthcheck", password="")

            # Open SFTP session
            sftp = paramiko.SFTPClient.from_transport(transport)
            if sftp is None:
                pytest.fail("Could not open SFTP session")

            # Download the healthcheck file
            with sftp.open("/healthcheck", "r") as f:
                content = f.read()

            if content == b"OK":
                print("✅ SFTP healthcheck passed")
            else:
                pytest.fail(f"Unexpected healthcheck content: {content!r}")

        except paramiko.SSHException as e:
            pytest.fail(f"SSH/SFTP connection failed: {e}")
        except OSError as e:
            pytest.fail(f"Network connection failed to {host}:{port}: {e}")
        finally:
            if sftp:
                sftp.close()
            if transport:
                transport.close()

    @pytest.mark.timeout(30)
    def test_sftp_connection(self, sftp_host_port: tuple[str, int]) -> None:
        """Test that the SFTP server accepts connections and authenticates.

        This is a basic connectivity test that verifies the SSH transport
        can be established and authentication succeeds.
        """
        host, port = sftp_host_port
        print(f"\n=== Testing SFTP connection at {host}:{port} ===")

        transport: Transport | None = None

        try:
            transport = Transport((host, port))
            transport.connect(username="test-device", password="")

            if transport.is_authenticated():
                print("✅ SFTP connection and authentication successful")
            else:
                pytest.fail("Transport connected but not authenticated")

        except paramiko.SSHException as e:
            pytest.fail(f"SSH connection failed: {e}")
        except OSError as e:
            pytest.fail(f"Network connection failed to {host}:{port}: {e}")
        finally:
            if transport:
                transport.close()
