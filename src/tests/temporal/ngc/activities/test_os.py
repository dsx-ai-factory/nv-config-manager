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
"""Test OS Activities."""

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
import responses
from nv_config_manager_dcim.workflow_models import OSImageVersions
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from nv_config_manager.common.client import ConfigStoreFileNotFound
from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData, Platform
from nv_config_manager.temporal.ngc.activities.os import (
    ExecuteZTPInput,
    ExecuteZTPOutput,
    GetCurrentOSInput,
    GetCurrentOSOutput,
    GetOSImageVersionsInput,
    PollImageInput,
    PollZTPStatusInput,
    PollZTPStatusOutput,
    UpdateIntendedOSImageInput,
    WaitRebootInput,
    WaitRebootOutput,
    _check_ztp_success,
    _verify_device_rebooted,
    execute_ztp,
    get_current_os,
    get_os_image_versions,
    poll_image,
    poll_ztp_status,
    update_intended_os_image,
    wait_reboot,
)
from nv_config_manager.temporal.ngc.activities.render import (
    ValidateRenderedImageChangeInput,
    validate_rendered_image_change,
)
from nv_config_manager.ztp.storage import ObjectStorageNotFoundException


@pytest.fixture
def device_data():
    """Create a test device data object."""
    return NetworkDeviceData(
        id="test-device",
        name="test-device",
        role="test-role",
        platform="cumulus-linux",
        site="SITEA",
        device_type="sn4200",
        primary_ip4="10.0.0.1",
        primary_ip6=None,
    )


@responses.activate
def test_execute_ztp(device_data):
    """Test execute_ztp activity."""
    # Mock the factory reset endpoint
    responses.add(
        responses.POST,
        f"https://{device_data.primary_ip4}:8765/nvue_v1/system/factory-default",
        json={"status": "success"},
        status=200,
    )

    # Execute activity
    result = execute_ztp(ExecuteZTPInput(device_data=device_data))

    # Verify result
    assert isinstance(result, ExecuteZTPOutput)
    assert isinstance(result.start_time, str)
    # Verify timestamp is valid ISO format
    datetime.fromisoformat(result.start_time)

    # Verify HTTP call was made
    assert len(responses.calls) == 1
    assert (
        responses.calls[0].request.url
        == f"https://{device_data.primary_ip4}:8765/nvue_v1/system/factory-default"
    )
    assert responses.calls[0].request.method == "POST"
    assert json.loads(responses.calls[0].request.body) == {
        "@reset": {"parameters": {"force": True}, "state": "start"}
    }


@pytest.mark.asyncio
@responses.activate
@patch("nv_config_manager.temporal.ngc.activities.os.time.sleep")
async def test_poll_ztp_status_success(mock_sleep, device_data):
    """Test poll_ztp_status activity with successful ZTP."""
    # Mock the ZTP status endpoint with success after 3 polls
    responses.add(
        responses.GET,
        f"https://{device_data.primary_ip4}:8765/nvue_v1/system/ztp",
        json={"status": "in-progress"},
        status=200,
    )
    responses.add(
        responses.GET,
        f"https://{device_data.primary_ip4}:8765/nvue_v1/system/ztp",
        json={"status": "in-progress"},
        status=200,
    )
    responses.add(
        responses.GET,
        f"https://{device_data.primary_ip4}:8765/nvue_v1/system/ztp",
        json={"status": "success"},
        status=200,
    )

    # Execute activity within test environment
    env = ActivityEnvironment()
    result = env.run(poll_ztp_status, PollZTPStatusInput(device_data=device_data))

    # Verify result
    assert isinstance(result, PollZTPStatusOutput)
    assert result.success is True

    # Verify HTTP calls were made
    assert len(responses.calls) == 3
    for call in responses.calls:
        assert call.request.url == f"https://{device_data.primary_ip4}:8765/nvue_v1/system/ztp"
        assert call.request.method == "GET"

    # Verify we slept twice (before the second and third polls)
    assert mock_sleep.call_count == 2
    mock_sleep.assert_called_with(30)


@pytest.mark.asyncio
@responses.activate
@patch("nv_config_manager.temporal.ngc.activities.os.time.sleep")
async def test_wait_reboot_success(mock_sleep, device_data):
    """Test wait_reboot activity with successful reboot."""
    # Create a timestamp from 2 minutes ago
    ztp_time = datetime.now() - timedelta(minutes=2)

    # Mock the system info endpoint with uptime less than elapsed time after 3 polls
    responses.add(
        responses.GET,
        f"https://{device_data.primary_ip4}:8765/nvue_v1/system",
        json={"uptime": 180},  # 3 minutes uptime > 2 minutes elapsed
        status=200,
    )
    responses.add(
        responses.GET,
        f"https://{device_data.primary_ip4}:8765/nvue_v1/system",
        json={"uptime": 180},  # 3 minutes uptime > 2 minutes elapsed
        status=200,
    )
    responses.add(
        responses.GET,
        f"https://{device_data.primary_ip4}:8765/nvue_v1/system",
        json={"uptime": 60},  # 1 minute uptime < 2 minutes elapsed
        status=200,
    )

    # Execute activity within test environment
    env = ActivityEnvironment()
    result = env.run(
        wait_reboot,
        WaitRebootInput(
            device_data=device_data,
            ztp_execution_timestamp=ztp_time.isoformat(),
            timeout=10,
        ),
    )

    # Verify result
    assert isinstance(result, WaitRebootOutput)
    assert result.success is True

    # Verify HTTP calls were made
    assert len(responses.calls) == 3
    for call in responses.calls:
        assert call.request.url == f"https://{device_data.primary_ip4}:8765/nvue_v1/system"
        assert call.request.method == "GET"

    # Verify we slept twice (before the third poll)
    assert mock_sleep.call_count == 2
    mock_sleep.assert_called_with(30)


@patch("nv_config_manager.temporal.ngc.activities.os.NetworkConnection")
def test_get_current_os_success(mock_network_connection, device_data):
    """Test get_current_os activity with successful retrieval."""
    # Mock device connection and get_running_image method
    mock_device = Mock()
    mock_device.get_running_image.return_value = "5.2.0"
    mock_network_connection.from_device_data.return_value = mock_device

    # Execute activity
    result = get_current_os(GetCurrentOSInput(device_data=device_data))

    # Verify result
    assert isinstance(result, GetCurrentOSOutput)
    assert result.running_os == "5.2.0"

    # Verify device connection was created and method called
    mock_network_connection.from_device_data.assert_called_once_with(device_data)
    mock_device.get_running_image.assert_called_once()


@patch("nv_config_manager.temporal.ngc.activities.os.NetworkConnection")
def test_get_current_os_failure(mock_network_connection, device_data):
    """Test get_current_os activity with failure."""
    # Mock device connection to raise an exception
    mock_device = Mock()
    mock_device.get_running_image.side_effect = Exception("Connection failed")
    mock_network_connection.from_device_data.return_value = mock_device

    # Execute activity and expect ApplicationError
    with pytest.raises(ApplicationError, match="Failed to get current OS"):
        get_current_os(GetCurrentOSInput(device_data=device_data))


def test_verify_device_rebooted_success():
    """Test _verify_device_rebooted helper function with successful reboot detection."""
    # Create mock device with uptime less than elapsed time
    mock_device = Mock()
    mock_device.get_uptime.return_value = 60  # 1 minute

    # Create timestamp from 2 minutes ago
    ztp_time = datetime.now() - timedelta(minutes=2)

    # Execute function
    result = _verify_device_rebooted(mock_device, ztp_time)

    # Verify result indicates reboot occurred
    assert result is True
    mock_device.get_uptime.assert_called_once()


def test_verify_device_rebooted_no_reboot():
    """Test _verify_device_rebooted when device has not rebooted."""
    # Create mock device with uptime greater than elapsed time
    mock_device = Mock()
    mock_device.get_uptime.return_value = 180  # 3 minutes

    # Create timestamp from 2 minutes ago
    ztp_time = datetime.now() - timedelta(minutes=2)

    # Execute function
    result = _verify_device_rebooted(mock_device, ztp_time)

    # Verify result indicates no reboot
    assert result is False


def test_verify_device_rebooted_exception():
    """Test _verify_device_rebooted when get_uptime raises exception."""
    # Create mock device that raises exception
    mock_device = Mock()
    mock_device.get_uptime.side_effect = Exception("Connection failed")

    # Create timestamp
    ztp_time = datetime.now() - timedelta(minutes=2)

    # Execute function
    result = _verify_device_rebooted(mock_device, ztp_time)

    # Verify result indicates failure (no reboot detected)
    assert result is False


def test_check_ztp_success_with_reboot():
    """Test _check_ztp_success with successful ZTP and reboot verification."""
    # Create mock device
    mock_device = Mock()
    mock_device.get_ztp_status.return_value = "success"
    mock_device.get_uptime.return_value = 60  # 1 minute

    # Create timestamp from 2 minutes ago
    ztp_time = datetime.now() - timedelta(minutes=2)

    # Execute function
    result = _check_ztp_success(mock_device, ztp_time)

    # Verify result
    assert result is True
    mock_device.get_ztp_status.assert_called_once()
    mock_device.get_uptime.assert_called_once()


def test_check_ztp_success_without_timestamp():
    """Test _check_ztp_success without timestamp (backward compatibility)."""
    # Create mock device
    mock_device = Mock()
    mock_device.get_ztp_status.return_value = "success"

    # Execute function without timestamp
    result = _check_ztp_success(mock_device, None)

    # Verify result
    assert result is True
    mock_device.get_ztp_status.assert_called_once()
    # get_uptime should not be called when no timestamp
    mock_device.get_uptime.assert_not_called()


def test_check_ztp_success_not_complete():
    """Test _check_ztp_success when ZTP is not complete."""
    # Create mock device
    mock_device = Mock()
    mock_device.get_ztp_status.return_value = "in-progress"

    # Execute function
    result = _check_ztp_success(mock_device, None)

    # Verify result
    assert result is False
    mock_device.get_ztp_status.assert_called_once()


@pytest.mark.asyncio
async def test_get_os_image_versions_integration(monkeypatch):
    """Test get_os_image_versions with monkeypatched client."""
    from nv_config_manager.temporal.ngc.activities import os as os_module

    class MockClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get_os_image_versions(self, device_id):
            return OSImageVersions("5.1.0", "5.2.0", "192.168.1.1")

    monkeypatch.setattr(os_module, "create_dcim_client", MockClient)

    result = await get_os_image_versions(GetOSImageVersionsInput(device_id="test-id"))
    assert result.intended_firmware == "5.1.0"
    assert result.desired_firmware == "5.2.0"
    assert result.ztp_ipv4_address == "192.168.1.1"


@pytest.mark.asyncio
async def test_update_intended_os_image_integration(monkeypatch):
    """Test update_intended_os_image with monkeypatched client."""
    from nv_config_manager.temporal.ngc.activities import os as os_module

    merge_called = []

    class MockClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def set_intended_os_image(self, device_id, desired_firmware):
            merge_called.append((device_id, desired_firmware))

    monkeypatch.setattr(os_module, "create_dcim_client", MockClient)

    await update_intended_os_image(
        UpdateIntendedOSImageInput(device_id="test-id", desired_firmware="5.2.0")
    )
    assert len(merge_called) == 1
    assert merge_called == [("test-id", "5.2.0")]


@pytest.mark.asyncio
async def test_poll_image_integration(monkeypatch):
    """Test poll_image with monkeypatched NetworkConnection."""
    from nv_config_manager.temporal.ngc.activities import os as os_module

    call_count = [0]

    class MockNetworkConnection:
        def __init__(self, *args, **kwargs):
            pass

        @classmethod
        def from_device_data(cls, device_data):
            return cls()

        def get_running_image(self):
            call_count[0] += 1
            if call_count[0] < 3:
                return "5.1.0"  # Wrong version first two times
            return "5.2.0"  # Correct version on third try

    # Mock time.sleep to avoid actual delays
    def mock_sleep(seconds):
        pass

    monkeypatch.setattr(os_module, "NetworkConnection", MockNetworkConnection)
    monkeypatch.setattr(os_module.time, "sleep", mock_sleep)

    device_data = NetworkDeviceData(
        id="test-device",
        name="test-device",
        role="test-role",
        platform=Platform.CUMULUS_LINUX,
        site="SITEA",
        device_type="sn4200",
        primary_ip4="10.0.0.1",
        primary_ip6=None,
    )

    # Execute activity in environment
    env = ActivityEnvironment()
    result = env.run(
        poll_image,
        PollImageInput(device_data=device_data, expected_image="5.2.0"),
    )

    assert result.running_image == "5.2.0"
    assert call_count[0] == 3  # Should have called get_running_image 3 times


@pytest.mark.asyncio
async def test_poll_image_exception_path(monkeypatch):
    """Test poll_image exception handling."""
    from nv_config_manager.temporal.ngc.activities import os as os_module

    call_count = [0]

    class MockNetworkConnection:
        @classmethod
        def from_device_data(cls, device_data):
            return cls()

        def get_running_image(self):
            call_count[0] += 1
            if call_count[0] < 2:
                raise Exception("Connection error")
            return "5.2.0"

    def mock_sleep(seconds):
        pass

    monkeypatch.setattr(os_module, "NetworkConnection", MockNetworkConnection)
    monkeypatch.setattr(os_module.time, "sleep", mock_sleep)

    device_data = NetworkDeviceData(
        id="test",
        name="test",
        role="test",
        platform=Platform.CUMULUS_LINUX,
        site="test",
        device_type="sn4200",
        primary_ip4="10.0.0.1",
        primary_ip6=None,
    )

    env = ActivityEnvironment()
    result = env.run(
        poll_image,
        PollImageInput(device_data=device_data, expected_image="5.2.0"),
    )

    assert result.running_image == "5.2.0"


@pytest.mark.asyncio
async def test_poll_ztp_status_exception_handling(monkeypatch):
    """Test poll_ztp_status exception handling path."""
    from nv_config_manager.temporal.ngc.activities import os as os_module

    call_count = [0]

    class MockNetworkConnection:
        @classmethod
        def from_device_data(cls, device_data):
            return cls()

        def get_ztp_status(self):
            call_count[0] += 1
            if call_count[0] < 2:
                raise Exception("Connection error")
            return "success"

    def mock_sleep(seconds):
        pass

    monkeypatch.setattr(os_module, "NetworkConnection", MockNetworkConnection)
    monkeypatch.setattr(os_module.time, "sleep", mock_sleep)

    device_data = NetworkDeviceData(
        id="test",
        name="test",
        role="test",
        platform=Platform.CUMULUS_LINUX,
        site="test",
        device_type="sn4200",
        primary_ip4="10.0.0.1",
        primary_ip6=None,
    )

    env = ActivityEnvironment()
    result = env.run(
        poll_ztp_status,
        PollZTPStatusInput(device_data=device_data),
    )

    assert result.success is True


@pytest.mark.asyncio
async def test_wait_reboot_exception_handling(monkeypatch):
    """Test wait_reboot exception handling path."""
    from nv_config_manager.temporal.ngc.activities import os as os_module

    call_count = [0]

    class MockNetworkConnection:
        @classmethod
        def from_device_data(cls, device_data):
            return cls()

        def get_uptime(self):
            call_count[0] += 1
            if call_count[0] < 2:
                raise Exception("Connection error")
            return 60  # Device rebooted

    def mock_sleep(seconds):
        pass

    monkeypatch.setattr(os_module, "NetworkConnection", MockNetworkConnection)
    monkeypatch.setattr(os_module.time, "sleep", mock_sleep)

    device_data = NetworkDeviceData(
        id="test",
        name="test",
        role="test",
        platform=Platform.CUMULUS_LINUX,
        site="test",
        device_type="sn4200",
        primary_ip4="10.0.0.1",
        primary_ip6=None,
    )

    ztp_time = datetime.now() - timedelta(minutes=2)

    env = ActivityEnvironment()
    result = env.run(
        wait_reboot,
        WaitRebootInput(
            device_data=device_data,
            ztp_execution_timestamp=ztp_time.isoformat(),
            timeout=10,
        ),
    )

    assert result.success is True


@pytest.mark.asyncio
async def test_get_os_image_versions_with_parent_location(monkeypatch):
    """Test get_os_image_versions when device location has a parent."""
    from nv_config_manager.temporal.ngc.activities import os as os_module

    class MockClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get_os_image_versions(self, device_id):
            return OSImageVersions("5.1.0", "5.2.0", "192.168.1.1")

    monkeypatch.setattr(os_module, "create_dcim_client", MockClient)

    result = await get_os_image_versions(GetOSImageVersionsInput(device_id="test-id"))
    assert result.intended_firmware == "5.1.0"
    assert result.desired_firmware == "5.2.0"


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.ngc.activities.render.asyncio.sleep", new_callable=AsyncMock)
@patch("nv_config_manager.temporal.ngc.activities.render.activity.heartbeat")
async def test_validate_rendered_image_change_success(mock_heartbeat, mock_sleep, device_data):
    """Test validate_rendered_image_change when image is found."""
    from unittest.mock import MagicMock

    mock_file = MagicMock()
    mock_file.content = "VERSION_ID=5.0.0"

    mock_config_client = AsyncMock()
    mock_config_client.__aenter__ = AsyncMock(return_value=mock_config_client)
    mock_config_client.__aexit__ = AsyncMock(return_value=None)
    mock_config_client.load_file = AsyncMock(return_value=mock_file)

    with (
        patch(
            "nv_config_manager.temporal.ngc.activities.render.config_store_client",
            return_value=mock_config_client,
        ),
        patch("nv_config_manager.temporal.ngc.activities.render.datetime") as mock_datetime,
    ):
        # Mock datetime to return immediately (no timeout)
        mock_now = MagicMock()
        mock_now.__sub__ = MagicMock(return_value=timedelta(seconds=0))
        mock_datetime.now.return_value = mock_now

        result = await validate_rendered_image_change(
            ValidateRenderedImageChangeInput(device_data=device_data, desired_image="5.0.0")
        )

    assert result is True
    mock_config_client.load_file.assert_called_once_with(
        device_uuid="test-device", filename="boot-script"
    )


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.ngc.activities.render.asyncio.sleep", new_callable=AsyncMock)
@patch("nv_config_manager.temporal.ngc.activities.render.activity.heartbeat")
async def test_validate_rendered_image_change_timeout(mock_heartbeat, mock_sleep, device_data):
    """Test validate_rendered_image_change when timeout is reached."""
    from unittest.mock import MagicMock

    mock_file = MagicMock()
    mock_file.content = "VERSION_ID=4.0.0"  # Different version

    mock_config_client = AsyncMock()
    mock_config_client.__aenter__ = AsyncMock(return_value=mock_config_client)
    mock_config_client.__aexit__ = AsyncMock(return_value=None)
    mock_config_client.load_file = AsyncMock(return_value=mock_file)

    start_time = datetime.now()
    timeout = timedelta(minutes=5)

    with (
        patch(
            "nv_config_manager.temporal.ngc.activities.render.config_store_client",
            return_value=mock_config_client,
        ),
        patch("nv_config_manager.temporal.ngc.activities.render.datetime") as mock_datetime,
    ):
        # Mock datetime to simulate timeout
        call_count = 0

        def mock_now():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return start_time
            # After first call, return time that exceeds timeout
            return start_time + timeout + timedelta(seconds=1)

        mock_datetime.now.side_effect = mock_now

        with pytest.raises(ApplicationError) as exc_info:
            await validate_rendered_image_change(
                ValidateRenderedImageChangeInput(device_data=device_data, desired_image="5.0.0")
            )

        assert "Timeout waiting for image version 5.0.0" in str(exc_info.value)


@pytest.mark.asyncio
async def test_validate_rendered_image_change_unsupported_platform():
    """Test validate_rendered_image_change with unsupported platform."""
    unsupported_device = NetworkDeviceData(
        id="test-device-id",
        name="test-device",
        platform=Platform.ARISTA_EOS,  # Not CUMULUS_LINUX
        role="test_role",
        site="test_site",
        device_type="test_device_type",
        primary_ip4="192.0.2.1",
        primary_ip6=None,
    )

    with pytest.raises(NotImplementedError) as exc_info:
        await validate_rendered_image_change(
            ValidateRenderedImageChangeInput(device_data=unsupported_device, desired_image="5.0.0")
        )

    assert "Platform arista-eos not supported" in str(exc_info.value)


@pytest.fixture
def juniper_device_data():
    """Juniper device used by image-render validation tests."""
    return NetworkDeviceData(
        id="juniper-device",
        name="juniper-device",
        platform=Platform.JUNIPER_JUNOS,
        role="wan",
        site="test_site",
        device_type="mx204",
        primary_ip4="192.0.2.10",
        primary_ip6=None,
    )


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.ngc.activities.render.asyncio.sleep", new_callable=AsyncMock)
@patch("nv_config_manager.temporal.ngc.activities.render.activity.heartbeat")
async def test_validate_rendered_image_change_juniper_success(
    mock_heartbeat, mock_sleep, juniper_device_data
):
    """Juniper validation succeeds when firmware and full-config are both present."""
    mock_file = MagicMock()
    mock_config_client = AsyncMock()
    mock_config_client.__aenter__ = AsyncMock(return_value=mock_config_client)
    mock_config_client.__aexit__ = AsyncMock(return_value=None)
    mock_config_client.load_file = AsyncMock(return_value=mock_file)

    mock_storage_client = AsyncMock()
    mock_storage_client.__aenter__ = AsyncMock(return_value=mock_storage_client)
    mock_storage_client.__aexit__ = AsyncMock(return_value=None)
    mock_storage_client.get_firmware_checksum = AsyncMock(return_value="abc123")

    with (
        patch(
            "nv_config_manager.temporal.ngc.activities.render.config_store_client",
            return_value=mock_config_client,
        ),
        patch(
            "nv_config_manager.temporal.ngc.activities.render.get_storage_client",
            return_value=mock_storage_client,
        ),
        patch("nv_config_manager.temporal.ngc.activities.render.datetime") as mock_datetime,
    ):
        mock_now = MagicMock()
        mock_now.__sub__ = MagicMock(return_value=timedelta(seconds=0))
        mock_datetime.now.return_value = mock_now

        result = await validate_rendered_image_change(
            ValidateRenderedImageChangeInput(
                device_data=juniper_device_data, desired_image="24.4R2"
            )
        )

    assert result is True
    mock_storage_client.get_firmware_checksum.assert_called_once_with("juniper-junos", "24.4R2")
    mock_config_client.load_file.assert_called_once_with(
        device_uuid="juniper-device", filename="full-config"
    )


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.ngc.activities.render.asyncio.sleep", new_callable=AsyncMock)
@patch("nv_config_manager.temporal.ngc.activities.render.activity.heartbeat")
async def test_validate_rendered_image_change_juniper_timeout(
    mock_heartbeat, mock_sleep, juniper_device_data
):
    """Juniper validation times out when firmware is missing from ZTP storage."""
    mock_file = MagicMock()
    mock_config_client = AsyncMock()
    mock_config_client.__aenter__ = AsyncMock(return_value=mock_config_client)
    mock_config_client.__aexit__ = AsyncMock(return_value=None)
    mock_config_client.load_file = AsyncMock(return_value=mock_file)

    mock_storage_client = AsyncMock()
    mock_storage_client.__aenter__ = AsyncMock(return_value=mock_storage_client)
    mock_storage_client.__aexit__ = AsyncMock(return_value=None)
    mock_storage_client.get_firmware_checksum = AsyncMock(
        side_effect=ObjectStorageNotFoundException("missing")
    )

    start_time = datetime.now()
    timeout = timedelta(minutes=5)

    with (
        patch(
            "nv_config_manager.temporal.ngc.activities.render.config_store_client",
            return_value=mock_config_client,
        ),
        patch(
            "nv_config_manager.temporal.ngc.activities.render.get_storage_client",
            return_value=mock_storage_client,
        ),
        patch("nv_config_manager.temporal.ngc.activities.render.datetime") as mock_datetime,
    ):
        call_count = 0

        def mock_now():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return start_time
            return start_time + timeout + timedelta(seconds=1)

        mock_datetime.now.side_effect = mock_now

        with pytest.raises(ApplicationError) as exc_info:
            await validate_rendered_image_change(
                ValidateRenderedImageChangeInput(
                    device_data=juniper_device_data, desired_image="24.4R2"
                )
            )

    assert "Timeout waiting for Juniper firmware 24.4R2" in str(exc_info.value)


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.ngc.activities.render.asyncio.sleep", new_callable=AsyncMock)
@patch("nv_config_manager.temporal.ngc.activities.render.activity.heartbeat")
async def test_validate_rendered_image_change_juniper_waits_for_full_config(
    mock_heartbeat, mock_sleep, juniper_device_data
):
    """Missing full-config is treated as not-ready, not as a hard error."""
    mock_config_client = AsyncMock()
    mock_config_client.__aenter__ = AsyncMock(return_value=mock_config_client)
    mock_config_client.__aexit__ = AsyncMock(return_value=None)
    mock_config_client.load_file = AsyncMock(side_effect=ConfigStoreFileNotFound("missing"))

    mock_storage_client = AsyncMock()
    mock_storage_client.__aenter__ = AsyncMock(return_value=mock_storage_client)
    mock_storage_client.__aexit__ = AsyncMock(return_value=None)
    mock_storage_client.get_firmware_checksum = AsyncMock(return_value="abc123")

    start_time = datetime.now()
    timeout = timedelta(minutes=5)

    with (
        patch(
            "nv_config_manager.temporal.ngc.activities.render.config_store_client",
            return_value=mock_config_client,
        ),
        patch(
            "nv_config_manager.temporal.ngc.activities.render.get_storage_client",
            return_value=mock_storage_client,
        ),
        patch("nv_config_manager.temporal.ngc.activities.render.datetime") as mock_datetime,
    ):
        call_count = 0

        def mock_now():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return start_time
            return start_time + timeout + timedelta(seconds=1)

        mock_datetime.now.side_effect = mock_now

        with pytest.raises(ApplicationError, match="full-config"):
            await validate_rendered_image_change(
                ValidateRenderedImageChangeInput(
                    device_data=juniper_device_data, desired_image="24.4R2"
                )
            )
