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
import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from paramiko import SFTPAttributes
from paramiko.common import AUTH_SUCCESSFUL, OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED, OPEN_SUCCEEDED
from paramiko.sftp import SFTP_OK

from nv_config_manager.dcim import ZTPDevice
from nv_config_manager.ztp.sftp.main import (
    ObjectStorageRangeReader,
    ZTPServer,
    ZTPSFTPHandle,
    ZTPSFTPServer,
    ZTPSFTPSubsystemHandler,
    handle_connection,
    shutdown_event,
    start_server,
)
from nv_config_manager.ztp.storage import ObjectStorageDownload


@pytest.fixture
def ztp_server():
    return ZTPServer()


@pytest.fixture
def sftp_handle():
    handle = ZTPSFTPHandle(0)
    handle.readfile = io.BytesIO(b"test content")
    return handle


@pytest.fixture
def sftp_server():
    server = ZTPServer()
    return ZTPSFTPServer(server, client_addr="192.168.1.1")


def test_check_auth_password(ztp_server):
    """Test that all password authentication attempts are allowed."""
    result = ztp_server.check_auth_password("testuser", "testpass")
    assert result == AUTH_SUCCESSFUL


def test_get_allowed_auths(ztp_server):
    """Test that only password authentication is allowed."""
    result = ztp_server.get_allowed_auths("testuser")
    assert result == "password"


def test_check_channel_request(ztp_server):
    """Test channel request handling."""
    # Test session channel
    result = ztp_server.check_channel_request("session", 1)
    assert result == OPEN_SUCCEEDED

    # Test other channel types
    result = ztp_server.check_channel_request("other", 1)
    assert result == OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED


def test_close(sftp_handle):
    """Test file handle closing."""
    result = sftp_handle.close()
    assert result == SFTP_OK
    # The readfile should be closed but not None
    assert sftp_handle.readfile.closed


def test_read(sftp_handle):
    """Test reading from file handle."""
    # Test reading from start
    result = sftp_handle.read(0, 4)
    assert result == b"test"

    # Test reading from offset
    result = sftp_handle.read(5, 4)
    assert result == b"cont"


def test_load_ztp_file(sftp_server):
    """Test loading ZTP configuration file."""
    dcim_device = ZTPDevice(
        device_id="device1",
        name="device-1",
        addresses=["192.168.1.1"],
        platform_name="Cumulus Linux",
        firmware_version=None,
        config_store_instance=None,
    )
    with (
        patch(
            "nv_config_manager_dcim_nautobot_2x.provider.NautobotDCIMClient.get_ztp_device",
            new_callable=AsyncMock,
            return_value=dcim_device,
        ),
        patch(
            "nv_config_manager.ztp.device.DeviceData.load_file",
            new_callable=AsyncMock,
            return_value="test config",
        ),
    ):
        result = sftp_server._load_ztp_file("device1", "config.txt")

    assert isinstance(result, io.BytesIO)
    assert result.getvalue() == b"test config"


def test_object_storage_range_reader_fetches_bounded_ranges(sftp_server):
    """SFTP reads only fetch the configured range cache, not the full object."""
    storage_client = MagicMock()
    storage_client.close = AsyncMock()
    body_one = MagicMock()
    body_one.read = AsyncMock(side_effect=[b"ab", b"cd"])
    body_two = MagicMock()
    body_two.read = AsyncMock(side_effect=[b"ef", b"gh"])
    storage_client.get_object = AsyncMock(
        side_effect=[
            ObjectStorageDownload(
                filename="firmware.bin",
                file_handle=body_one,
                content_length=4,
                total_length=10,
                backend="s3",
                object_key="cisco/1.0/firmware.bin",
            ),
            ObjectStorageDownload(
                filename="firmware.bin",
                file_handle=body_two,
                content_length=4,
                total_length=10,
                backend="s3",
                object_key="cisco/1.0/firmware.bin",
            ),
        ]
    )
    reader = ObjectStorageRangeReader(
        storage_client=storage_client,
        event_loop=sftp_server._event_loop,
        platform="cisco",
        version="1.0",
        filename="firmware.bin",
        content_length=10,
        etag='"revision-1"',
        logger=MagicMock(),
        read_ahead_bytes=4,
    )

    assert reader.read(0, 2) == b"ab"
    assert reader.read(2, 2) == b"cd"
    assert reader.read(4, 2) == b"ef"
    assert reader.read(10, 2) == b""
    assert storage_client.get_object.await_count == 2
    assert storage_client.get_object.await_args_list[0].kwargs == {
        "range_header": "bytes=0-3",
        "known_total_length": 10,
        "if_match": '"revision-1"',
    }
    assert storage_client.get_object.await_args_list[1].kwargs == {
        "range_header": "bytes=4-7",
        "known_total_length": 10,
        "if_match": '"revision-1"',
    }
    body_one.close.assert_called_once()
    body_two.close.assert_called_once()
    assert body_one.read.await_args_list[0].args == (4,)
    assert body_one.read.await_args_list[1].args == (2,)

    reader.close()
    storage_client.close.assert_awaited_once()


def test_object_storage_range_reader_bounds_oversized_requests(sftp_server):
    """A client cannot make one SFTP read exceed the configured cache bound."""
    storage_client = MagicMock()
    storage_client.close = AsyncMock()
    body = MagicMock()
    body.read = AsyncMock(return_value=b"abcd")
    storage_client.get_object = AsyncMock(
        return_value=ObjectStorageDownload(
            filename="firmware.bin",
            file_handle=body,
            content_length=4,
            total_length=100,
            backend="s3",
            object_key="cisco/1.0/firmware.bin",
        )
    )
    reader = ObjectStorageRangeReader(
        storage_client=storage_client,
        event_loop=sftp_server._event_loop,
        platform="cisco",
        version="1.0",
        filename="firmware.bin",
        content_length=100,
        logger=MagicMock(),
        read_ahead_bytes=4,
    )

    assert reader.read(0, 100) == b"abcd"
    storage_client.get_object.assert_awaited_once_with(
        "cisco",
        "1.0",
        "firmware.bin",
        range_header="bytes=0-3",
        known_total_length=100,
        if_match=None,
    )
    body.read.assert_awaited_once_with(4)

    reader.close()


def test_object_storage_range_reader_releases_its_download_permit_once(sftp_server):
    """A download permit is held for the SFTP handle's full lifetime."""
    storage_client = MagicMock()
    storage_client.close = AsyncMock()
    release_download_permit = MagicMock()
    reader = ObjectStorageRangeReader(
        storage_client=storage_client,
        event_loop=sftp_server._event_loop,
        platform="cisco",
        version="1.0",
        filename="firmware.bin",
        content_length=10,
        logger=MagicMock(),
        release_download_permit=release_download_permit,
    )

    reader.close()
    reader.close()

    storage_client.close.assert_awaited_once()
    release_download_permit.assert_called_once()


def test_subsystem_closes_handles_before_session_event_loop(sftp_server):
    """Paramiko must close range readers while their session loop is still usable."""
    events: list[str] = []
    storage_client = MagicMock()
    storage_client.close = AsyncMock()
    release_download_permit = MagicMock()
    reader = ObjectStorageRangeReader(
        storage_client=storage_client,
        event_loop=sftp_server._event_loop,
        platform="cisco",
        version="1.0",
        filename="firmware.bin",
        content_length=10,
        logger=MagicMock(),
        release_download_permit=release_download_permit,
    )
    handle = ZTPSFTPHandle(0)
    handle.range_reader = reader
    handler = object.__new__(ZTPSFTPSubsystemHandler)
    handler.server = sftp_server

    def close_outstanding_handles(_handler: ZTPSFTPSubsystemHandler) -> None:
        events.append("handles")
        assert not sftp_server._event_loop.is_closed()
        handle.close()

    original_close_session = sftp_server.close_session

    def close_session() -> None:
        events.append("session")
        original_close_session()

    with (
        patch(
            "nv_config_manager.ztp.sftp.main.SFTPServer.finish_subsystem",
            autospec=True,
            side_effect=close_outstanding_handles,
        ),
        patch.object(sftp_server, "close_session", side_effect=close_session),
    ):
        handler.finish_subsystem()

    assert events == ["handles", "session"]
    assert sftp_server._event_loop.is_closed()
    storage_client.close.assert_awaited_once()
    release_download_permit.assert_called_once()


def test_object_storage_range_reader_supports_synchronous_file_handles(sftp_server):
    """PVC-backed synchronous range handles are bounded and closed after each read."""
    storage_client = MagicMock()
    storage_client.close = AsyncMock()
    body = io.BytesIO(b"abcd-and-unrequested-data")
    storage_client.get_object = AsyncMock(
        return_value=ObjectStorageDownload(
            filename="firmware.bin",
            file_handle=body,
            content_length=4,
            total_length=10,
            backend="filestore",
            object_key="cisco/1.0/firmware.bin",
            etag="file-revision",
        )
    )
    reader = ObjectStorageRangeReader(
        storage_client=storage_client,
        event_loop=sftp_server._event_loop,
        platform="cisco",
        version="1.0",
        filename="firmware.bin",
        content_length=10,
        etag="file-revision",
        logger=MagicMock(),
        read_ahead_bytes=4,
    )

    assert reader.read(0, 2) == b"ab"
    assert body.closed
    reader.close()


@patch("nv_config_manager.ztp.sftp.main.get_storage_client")
def test_open_s3_file_creates_range_backed_handle(mock_s3_client, sftp_server):
    """Opening an S3 file must fetch metadata only until the first SFTP read."""
    storage_client = MagicMock()
    storage_client.connect = AsyncMock()
    storage_client.close = AsyncMock()
    storage_client.get_object_metadata = AsyncMock(
        return_value={"size": 1024, "etag": '"revision-1"'}
    )
    storage_client.get_object = AsyncMock()
    mock_s3_client.return_value = storage_client

    result = sftp_server.open("/file/cisco/1.0/firmware.bin", 0, None)

    assert isinstance(result, ZTPSFTPHandle)
    assert result.range_reader is not None
    assert result.range_reader._etag == '"revision-1"'
    storage_client.get_object.assert_not_awaited()
    result.close()
    storage_client.close.assert_awaited_once()


def test_load_path(sftp_server):
    """Test path loading with different path types."""
    # Test healthcheck path
    result = sftp_server._load_path("/healthcheck")
    assert isinstance(result, io.BytesIO)
    assert result.getvalue() == b"OK"

    # Test unknown path type
    with pytest.raises(FileNotFoundError):
        sftp_server._load_path("/unknown/path")


def test_mock_stat(sftp_server):
    """Test file attribute generation."""
    with patch.object(sftp_server, "_load_path", return_value=io.StringIO("test")):
        result = sftp_server._mock_stat("/test/path")
        assert isinstance(result, SFTPAttributes)
        assert result.st_size == 4


def test_stat(sftp_server):
    """Test stat operation."""
    sftp_server.logger = MagicMock()
    with patch.object(sftp_server, "_mock_stat", return_value=SFTPAttributes()):
        result = sftp_server.stat("/test/path")
        assert isinstance(result, SFTPAttributes)
    sftp_server.logger.debug.assert_called_once_with("stat request: %s", "/test/path")


def test_lstat(sftp_server):
    """Test lstat operation."""
    with patch.object(sftp_server, "_mock_stat", return_value=SFTPAttributes()):
        result = sftp_server.lstat("/test/path")
        assert isinstance(result, SFTPAttributes)


def test_open(sftp_server):
    """Test file opening."""
    with patch.object(sftp_server, "_load_path", return_value=io.StringIO("test")):
        result = sftp_server.open("/test/path", 0, None)
        assert isinstance(result, ZTPSFTPHandle)


@patch("nv_config_manager.ztp.sftp.main.paramiko.Transport")
def test_handle_connection(mock_transport):
    """Test connection handling."""
    # Create mock socket and address
    mock_socket = MagicMock()
    mock_addr = ("192.168.1.1", 12345)
    mock_host_key = MagicMock()

    # Mock transport and channel
    mock_channel = MagicMock()
    mock_transport_instance = MagicMock()
    mock_transport.return_value = mock_transport_instance
    mock_transport_instance.accept.return_value = mock_channel

    # Set up transport lifecycle
    mock_transport_instance.is_active.side_effect = [
        True,
        True,
        False,
    ]  # Will return True twice, then False

    # Run the connection handler
    handle_connection(mock_socket, mock_addr, mock_host_key)

    # Verify transport was set up and started
    mock_transport_instance.add_server_key.assert_called_once_with(mock_host_key)
    mock_transport_instance.set_subsystem_handler.assert_called_once_with(
        "sftp",
        ZTPSFTPSubsystemHandler,
        ZTPSFTPServer,
        client_addr=mock_addr[0],
    )
    mock_transport_instance.start_server.assert_called_once()
    mock_transport_instance.close.assert_called_once()


@pytest.mark.timeout(0)  # override default timeout, sftp startup takes a bit
@patch("nv_config_manager.ztp.sftp.main.start_http_server")
@patch("nv_config_manager.ztp.sftp.main.socket.socket")
def test_start_server(mock_socket, mock_start_http_server):
    """Test server startup."""
    # Mock socket instance
    mock_socket_instance = MagicMock()
    mock_socket.return_value = mock_socket_instance

    # Start the server with shutdown_event set so it stops after bringup
    shutdown_event.set()
    start_server(host="127.0.0.1", port=2222, level="INFO")

    # Verify socket was set up correctly
    mock_socket_instance.bind.assert_called_once_with(("127.0.0.1", 2222))
    mock_socket_instance.listen.assert_called_once_with(10)
    mock_socket_instance.close.assert_called_once()
    mock_start_http_server.assert_called_once_with(9100, addr="0.0.0.0")
