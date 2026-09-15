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
import inspect
import io
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from nv_config_manager.common.auth import AuthConfig
from nv_config_manager.common.client import (
    ConfigStoreException,
    ConfigStoreFileNotFound,
)
from nv_config_manager.ztp.api.main import app, healthcheck
from nv_config_manager.ztp.s3 import (
    S3ExistsException,
    S3NotFoundException,
)
from nv_config_manager.ztp.storage import (
    ObjectStorageByteRange,
    ObjectStorageDownload,
    ObjectStorageRangeNotSatisfiableException,
)

SSO_HEADERS = {"X-Auth-Request-Email": "test@nvidia.com"}


@pytest.fixture
def client():
    return TestClient(app)


def test_healthcheck(client):
    """Verify healthcheck."""
    rsp = client.get("/healthcheck")
    assert rsp.status_code == 200
    assert rsp.json() == "OK"


def test_healthcheck_is_async():
    """Keep the probe on the event loop so it is not doubled by a threadpool hop."""
    assert inspect.iscoroutinefunction(healthcheck)


def test_docs(client):
    """Verify Swagger Doc endpoint."""
    rsp = client.get("/docs")
    assert rsp.status_code == 403

    rsp = client.get("/docs", headers=SSO_HEADERS)
    assert rsp.status_code == 200


@patch("nv_config_manager.ztp.api.device_v1.Request.client")
def test_device_v1_bootscript(
    mock_request_client,
    mock_device_data,
    mock_not_found_data,
    mock_no_render_data,
    client,
):
    """Test device bootscript v1 endpoint."""
    mock_request_client.host = "testclient"
    mock_device_data["data"]["config_manager_device"]["device"]["interfaces"] = [
        {"ip_addresses": [{"host": "testclient"}]}
    ]
    mock_no_render_data["data"]["config_manager_device"]["device"]["interfaces"] = [
        {"ip_addresses": [{"host": "testclient"}]}
    ]

    with patch(
        "nv_config_manager_dcim_nautobot_2x.provider.NautobotDCIMClient.graphql_query",
        return_value=mock_device_data,
    ):
        with patch(
            "nv_config_manager.ztp.device.DeviceData.load_file",
            new_callable=AsyncMock,
            return_value="boot-script content",
        ):
            rsp = client.get(f"/v1/device/{uuid4()}/boot-script")
            assert rsp.text == "boot-script content"
            assert rsp.status_code == 200

    with patch(
        "nv_config_manager_dcim_nautobot_2x.provider.NautobotDCIMClient.graphql_query",
        return_value=mock_not_found_data,
    ):
        rsp = client.get(f"/v1/device/{uuid4()}/boot-script")
        assert rsp.status_code == 404

    with patch(
        "nv_config_manager_dcim_nautobot_2x.provider.NautobotDCIMClient.graphql_query",
        return_value=mock_no_render_data,
    ):
        rsp = client.get(f"/v1/device/{uuid4()}/boot-script")
        assert rsp.status_code == 404


@patch("nv_config_manager.ztp.api.device_v1.Request.client")
def test_device_v1_config(mock_request_client, mock_device_data, mock_not_found_data, client):
    """Test device config v1 endpoint."""
    mock_request_client.host = "testclient"

    mock_device_data["data"]["config_manager_device"]["device"]["interfaces"] = [
        {"ip_addresses": [{"host": "testclient"}]}
    ]

    with patch(
        "nv_config_manager_dcim_nautobot_2x.provider.NautobotDCIMClient.graphql_query",
        return_value=mock_device_data,
    ):
        with patch(
            "nv_config_manager.ztp.device.DeviceData.load_file",
            new_callable=AsyncMock,
            return_value="RNO1-NVIDIA Config Manager-LAB/RNO1-M04-C10-SPINE1-HSS-TAN-LAB1/startup.yaml content",
        ):
            rsp = client.get(f"/v1/device/{uuid4()}/config/startup.yaml")
            assert (
                rsp.text
                == "RNO1-NVIDIA Config Manager-LAB/RNO1-M04-C10-SPINE1-HSS-TAN-LAB1/startup.yaml content"
            )
            assert rsp.status_code == 200

        with patch(
            "nv_config_manager_dcim_nautobot_2x.provider.NautobotDCIMClient.graphql_query",
            return_value=mock_not_found_data,
        ):
            rsp = client.get(f"/v1/device/{uuid4()}/config/startup.yaml")
            assert rsp.status_code == 404

    # Test that we throw a 403 Forbidden from unexpected client IP.
    mock_device_data["data"]["config_manager_device"]["device"]["interfaces"] = [
        {"ip_addresses": [{"host": "10.0.0.1"}]}
    ]

    with patch(
        "nv_config_manager_dcim_nautobot_2x.provider.NautobotDCIMClient.graphql_query",
        return_value=mock_device_data,
    ):
        rsp = client.get(f"/v1/device/{uuid4()}/config/startup.yaml")
        assert rsp.status_code == 403


@patch("nv_config_manager.ztp.api.device_v1.Request.client")
def test_device_v1_config_auth_disabled_bypasses_ip_check(
    mock_request_client, mock_device_data, client
):
    """No-auth deployments should not enforce device IP checks."""
    mock_request_client.host = "testclient2"
    mock_device_data["data"]["config_manager_device"]["device"]["interfaces"] = [
        {"ip_addresses": [{"host": "10.0.0.1"}]}
    ]

    with patch("nv_config_manager.common.auth._auth_config", AuthConfig(required=False)):
        with patch(
            "nv_config_manager_dcim_nautobot_2x.provider.NautobotDCIMClient.graphql_query",
            return_value=mock_device_data,
        ):
            with patch(
                "nv_config_manager.ztp.device.DeviceData.load_file",
                new_callable=AsyncMock,
                return_value="startup.yaml content",
            ):
                rsp = client.get(f"/v1/device/{uuid4()}/config/startup.yaml")
                assert rsp.text == "startup.yaml content"
                assert rsp.status_code == 200


def test_device_v1_firmware(mock_device_data, mock_not_found_data, client):
    """Test device firmware v1 endpoint."""

    mock_content = b"testcontent"

    # Create a proper mock for aiobotocore StreamingBody with iter_chunks
    mock_streaming_body = MagicMock()

    # Mock iter_chunks to return an async iterator
    async def async_iter_chunks(chunk_size):
        yield mock_content

    mock_streaming_body.iter_chunks = async_iter_chunks

    with patch(
        "nv_config_manager_dcim_nautobot_2x.provider.NautobotDCIMClient.graphql_query",
        return_value=mock_device_data,
    ):
        # Object found - mock S3Client with async context manager and streaming methods
        mock_s3_class = MagicMock()
        mock_s3_instance = MagicMock()
        mock_s3_instance.__aenter__ = AsyncMock(return_value=mock_s3_instance)
        mock_s3_instance.__aexit__ = AsyncMock(return_value=None)
        mock_s3_instance.connect = AsyncMock(return_value=mock_s3_instance)
        mock_s3_instance.close = AsyncMock(return_value=None)
        mock_s3_instance.get_firmware_object = AsyncMock(
            return_value=ObjectStorageDownload(
                filename="testfname",
                file_handle=mock_streaming_body,
                content_length=len(mock_content),
                total_length=len(mock_content),
                backend="s3",
                object_key="cumulus-linux/testfname",
            )
        )
        mock_s3_class.return_value = mock_s3_instance

        with patch(
            "nv_config_manager.ztp.api.device_v1.get_storage_client", return_value=mock_s3_instance
        ):
            rsp = client.get(f"/v1/device/{uuid4()}/firmware", headers=SSO_HEADERS)
            assert rsp.status_code == 200
            assert rsp.content == mock_content
            assert rsp.headers["content-disposition"] == (
                "attachment; filename=\"testfname\"; filename*=UTF-8''testfname"
            )

        # Test onie shortcut too
        # need to reset the mock streaming body
        mock_streaming_body2 = MagicMock()

        async def async_iter_chunks2(chunk_size):
            yield mock_content

        mock_streaming_body2.iter_chunks = async_iter_chunks2
        mock_streaming_body = mock_streaming_body2
        mock_s3_instance.get_firmware_object = AsyncMock(
            return_value=ObjectStorageDownload(
                filename="testfname",
                file_handle=mock_streaming_body,
                content_length=len(mock_content),
                total_length=len(mock_content),
                backend="s3",
                object_key="cumulus-linux/testfname",
            )
        )

        with patch(
            "nv_config_manager.ztp.api.device_v1.get_storage_client", return_value=mock_s3_instance
        ):
            rsp = client.get(f"/v1/device/{uuid4()}/onie", headers=SSO_HEADERS)
            assert rsp.status_code == 200
            assert rsp.content == mock_content
            assert rsp.headers["content-disposition"] == (
                "attachment; filename=\"testfname\"; filename*=UTF-8''testfname"
            )

        # Object not found
        mock_s3_instance.get_firmware_object = AsyncMock(side_effect=S3NotFoundException())

        with patch(
            "nv_config_manager.ztp.api.device_v1.get_storage_client", return_value=mock_s3_instance
        ):
            rsp = client.get(f"/v1/device/{uuid4()}/firmware", headers=SSO_HEADERS)
            assert rsp.status_code == 404

    with patch(
        "nv_config_manager_dcim_nautobot_2x.provider.NautobotDCIMClient.graphql_query",
        return_value=mock_not_found_data,
    ):
        rsp = client.get(f"/v1/device/{uuid4()}/firmware", headers=SSO_HEADERS)
        assert rsp.status_code == 404


def test_device_v1_firmware_checksum(mock_device_data, mock_not_found_data, client):
    """Test device firmware checksum v1 endpoint."""

    with patch(
        "nv_config_manager_dcim_nautobot_2x.provider.NautobotDCIMClient.graphql_query",
        return_value=mock_device_data,
    ):
        # Object found - mock S3Client with async context manager
        mock_s3_class = MagicMock()
        mock_s3_instance = MagicMock()
        mock_s3_instance.__aenter__ = AsyncMock(return_value=mock_s3_instance)
        mock_s3_instance.__aexit__ = AsyncMock(return_value=None)
        mock_s3_instance.get_firmware_checksum = AsyncMock(return_value="testchecksum")
        mock_s3_class.return_value = mock_s3_instance

        with patch(
            "nv_config_manager.ztp.api.device_v1.get_storage_client", return_value=mock_s3_instance
        ):
            rsp = client.get(f"/v1/device/{uuid4()}/firmware/checksum", headers=SSO_HEADERS)
            assert rsp.status_code == 200
            assert rsp.json() == {"checksum": "testchecksum"}

        # Object not found
        mock_s3_instance.get_firmware_checksum = AsyncMock(side_effect=S3NotFoundException())

        with patch(
            "nv_config_manager.ztp.api.device_v1.get_storage_client", return_value=mock_s3_instance
        ):
            rsp = client.get(f"/v1/device/{uuid4()}/firmware/checksum", headers=SSO_HEADERS)
            assert rsp.status_code == 404

    with patch(
        "nv_config_manager_dcim_nautobot_2x.provider.NautobotDCIMClient.graphql_query",
        return_value=mock_not_found_data,
    ):
        rsp = client.get(f"/v1/device/{uuid4()}/firmware/checksum", headers=SSO_HEADERS)
        assert rsp.status_code == 404


@patch("nv_config_manager.ztp.api.device_v1.Request.client")
def test_device_v1_config_store_exceptions(mock_request_client, mock_device_data, client):
    mock_request_client.host = "testclient"
    mock_device_data["data"]["config_manager_device"]["device"]["interfaces"] = [
        {"ip_addresses": [{"host": "testclient"}]}
    ]

    with patch(
        "nv_config_manager_dcim_nautobot_2x.provider.NautobotDCIMClient.graphql_query",
        return_value=mock_device_data,
    ):
        with patch(
            "nv_config_manager.ztp.device.DeviceData.load_file",
            side_effect=ConfigStoreFileNotFound("file not found"),
        ):
            rsp = client.get(f"/v1/device/{uuid4()}/boot-script")
            assert rsp.json() == {"detail": "file not found"}
            assert rsp.status_code == 404

        with patch(
            "nv_config_manager.ztp.device.DeviceData.load_file",
            side_effect=ConfigStoreException("config store query error"),
        ):
            rsp = client.get(f"/v1/device/{uuid4()}/boot-script")
            assert rsp.json() == {"detail": "config store query error"}
            assert rsp.status_code == 500


@patch("nv_config_manager.ztp.api.device_v1.Request.client")
def test_device_v1_provisioned(mock_request_client, mock_device_data, client):
    mock_request_client.host = "testclient"
    mock_device_data["data"]["config_manager_device"]["device"]["interfaces"] = [
        {"ip_addresses": [{"host": "testclient"}]}
    ]

    with patch(
        "nv_config_manager_dcim_nautobot_2x.provider.NautobotDCIMClient.graphql_query",
        new_callable=AsyncMock,
        return_value=mock_device_data,
    ):
        with patch(
            "nv_config_manager_dcim_nautobot_2x.provider.NautobotDCIMClient.set_status_provisioned",
            new_callable=AsyncMock,
        ):
            # Mock the temporal_client as an async context manager
            mock_temporal_client = MagicMock()
            mock_temporal_client.invoke_backup_workflow = AsyncMock()
            mock_temporal_client.__aenter__ = AsyncMock(return_value=mock_temporal_client)
            mock_temporal_client.__aexit__ = AsyncMock(return_value=None)

            with patch(
                "nv_config_manager.ztp.api.device_v1.temporal_client",
                return_value=mock_temporal_client,
            ):
                rsp = client.post(f"/v1/device/{uuid4()}/provisioned")
                assert rsp.json() == "OK"
                assert rsp.status_code == 200
                mock_temporal_client.invoke_backup_workflow.assert_called_once()

            mock_request_client.host = "testclient2"
            rsp = client.post(f"/v1/device/{uuid4()}/provisioned")
            assert rsp.json() == {
                "detail": "Unauthorized: client IP testclient2 is not associated with this device. "
                "Ensure the requesting IP is assigned to the device in the DCIM."
            }
            assert rsp.status_code == 403


@patch("nv_config_manager.ztp.api.device_v1.Request.client")
def test_device_v1_provisioned_auth_disabled_bypasses_ip_check(
    mock_request_client, mock_device_data, client
):
    """No-auth deployments should allow integration callers to mark provisioned."""
    mock_request_client.host = "testclient2"
    mock_device_data["data"]["config_manager_device"]["device"]["interfaces"] = [
        {"ip_addresses": [{"host": "10.0.0.1"}]}
    ]

    with patch("nv_config_manager.common.auth._auth_config", AuthConfig(required=False)):
        with patch(
            "nv_config_manager_dcim_nautobot_2x.provider.NautobotDCIMClient.graphql_query",
            new_callable=AsyncMock,
            return_value=mock_device_data,
        ):
            with patch(
                "nv_config_manager_dcim_nautobot_2x.provider.NautobotDCIMClient.set_status_provisioned",
                new_callable=AsyncMock,
            ):
                mock_temporal_client = MagicMock()
                mock_temporal_client.invoke_backup_workflow = AsyncMock()
                mock_temporal_client.__aenter__ = AsyncMock(return_value=mock_temporal_client)
                mock_temporal_client.__aexit__ = AsyncMock(return_value=None)

                with patch(
                    "nv_config_manager.ztp.api.device_v1.temporal_client",
                    return_value=mock_temporal_client,
                ):
                    rsp = client.post(f"/v1/device/{uuid4()}/provisioned")
                    assert rsp.json() == "OK"
                    assert rsp.status_code == 200
                    mock_temporal_client.invoke_backup_workflow.assert_called_once()


@patch("nv_config_manager.ztp.api.device_v1.Request.client")
def test_device_v1_validate_serial(mock_request_client, mock_device_data, client):
    mock_request_client.host = "testclient"
    mock_device_data["data"]["config_manager_device"]["device"]["interfaces"] = [
        {"ip_addresses": [{"host": "testclient"}]}
    ]

    with patch(
        "nv_config_manager_dcim_nautobot_2x.provider.NautobotDCIMClient.graphql_query",
        return_value=mock_device_data,
    ):
        with patch(
            "nv_config_manager_dcim_nautobot_2x.provider.NautobotDCIMClient.get_device_serial",
            return_value="expected_serial",
        ):
            rsp = client.post(
                f"/v1/device/{uuid4()}/validate_serial",
                json={"serial": "expected_serial"},
            )
            assert rsp.status_code == 200

            device_uuid = str(uuid4())
            with patch("nv_config_manager.ztp.api.device_v1.logger") as mock_logger:
                rsp = client.post(
                    f"/v1/device/{device_uuid}/validate_serial",
                    json={"serial": "invalid_serial"},
                )
            assert rsp.status_code == 400
            mock_logger.error.assert_called_once_with(
                "Serial number mismatch observed on device %s, expected: %s, observed: %s.",
                device_uuid,
                "expected_serial",
                "invalid_serial",
            )


def test_v1_firmware(client):
    """Test device firmware v1 endpoint."""

    mock_content = b"testcontent"

    # Create a proper mock for aiobotocore StreamingBody with iter_chunks
    mock_streaming_body = MagicMock()

    async def async_iter_chunks(chunk_size):
        yield mock_content

    mock_streaming_body.iter_chunks = async_iter_chunks

    # Object found - mock S3Client with async context manager and streaming methods
    mock_s3_class = MagicMock()
    mock_s3_instance = MagicMock()
    mock_s3_instance.__aenter__ = AsyncMock(return_value=mock_s3_instance)
    mock_s3_instance.__aexit__ = AsyncMock(return_value=None)
    mock_s3_instance.connect = AsyncMock(return_value=mock_s3_instance)
    mock_s3_instance.close = AsyncMock(return_value=None)
    mock_s3_instance.get_firmware_object = AsyncMock(
        return_value=ObjectStorageDownload(
            filename="testfname",
            file_handle=mock_streaming_body,
            content_length=len(mock_content),
            total_length=len(mock_content),
            backend="s3",
            object_key="arista_eos/testfname",
        )
    )
    mock_s3_class.return_value = mock_s3_instance

    with patch(
        "nv_config_manager.ztp.api.firmware_v1.get_storage_client", return_value=mock_s3_instance
    ):
        rsp = client.get("/v1/firmware/arista_eos/4.29.3M", headers=SSO_HEADERS)
        assert rsp.status_code == 200
        assert rsp.content == mock_content
        assert rsp.headers["content-disposition"] == (
            "attachment; filename=\"testfname\"; filename*=UTF-8''testfname"
        )

    # Object not found
    mock_s3_instance.get_firmware_object = AsyncMock(side_effect=S3NotFoundException())

    with patch(
        "nv_config_manager.ztp.api.firmware_v1.get_storage_client", return_value=mock_s3_instance
    ):
        rsp = client.get("/v1/firmware/arista_eos/4.29.3M", headers=SSO_HEADERS)
        assert rsp.status_code == 404


def test_v1_firmware_checksum(client):
    """Test device firmware checksum v1 endpoint."""

    # Object found - mock S3Client with async context manager
    mock_s3_class = MagicMock()
    mock_s3_instance = MagicMock()
    mock_s3_instance.__aenter__ = AsyncMock(return_value=mock_s3_instance)
    mock_s3_instance.__aexit__ = AsyncMock(return_value=None)
    mock_s3_instance.get_firmware_checksum = AsyncMock(return_value="testchecksum")
    mock_s3_class.return_value = mock_s3_instance

    with patch(
        "nv_config_manager.ztp.api.firmware_v1.get_storage_client", return_value=mock_s3_instance
    ):
        rsp = client.get("/v1/firmware/arista_eos/4.29.3M/checksum", headers=SSO_HEADERS)
        assert rsp.status_code == 200
        assert rsp.json() == {"checksum": "testchecksum"}

    # Object not found
    mock_s3_instance.get_firmware_checksum = AsyncMock(side_effect=S3NotFoundException())

    with patch(
        "nv_config_manager.ztp.api.firmware_v1.get_storage_client", return_value=mock_s3_instance
    ):
        rsp = client.get("/v1/firmware/arista_eos/4.29.3M/checksum", headers=SSO_HEADERS)
        assert rsp.status_code == 404


def test_v1_files_get(client):
    """Test v1 file get endpoint."""

    mock_content = b"testcontent"

    # Create a proper mock for aiobotocore StreamingBody with iter_chunks
    mock_streaming_body = MagicMock()

    async def async_iter_chunks(chunk_size):
        yield mock_content

    mock_streaming_body.iter_chunks = async_iter_chunks

    # Object found - mock S3Client with async context manager and streaming methods
    mock_s3_class = MagicMock()
    mock_s3_instance = MagicMock()
    mock_s3_instance.__aenter__ = AsyncMock(return_value=mock_s3_instance)
    mock_s3_instance.__aexit__ = AsyncMock(return_value=None)
    mock_s3_instance.connect = AsyncMock(return_value=mock_s3_instance)
    mock_s3_instance.close = AsyncMock(return_value=None)
    mock_s3_instance.get_object = AsyncMock(
        return_value=ObjectStorageDownload(
            filename="testfname\r\nFORGED",
            file_handle=mock_streaming_body,
            content_length=len(mock_content),
            total_length=len(mock_content),
            backend="s3",
            object_key="arista_eos/testfname",
        )
    )
    mock_s3_class.return_value = mock_s3_instance

    with (
        patch(
            "nv_config_manager.ztp.api.files_v1.get_storage_client",
            return_value=mock_s3_instance,
        ),
        patch("nv_config_manager.ztp.api.streaming.logger") as mock_logger,
    ):
        rsp = client.get("/v1/files/arista_eos/4.29.3M/image.bin", headers=SSO_HEADERS)
    assert rsp.status_code == 200
    assert rsp.content == mock_content
    assert rsp.headers["content-disposition"] == (
        "attachment; filename=\"testfnameFORGED\"; filename*=UTF-8''testfname%0D%0AFORGED"
    )
    assert rsp.headers["content-length"] == str(len(mock_content))
    assert rsp.headers["accept-ranges"] == "bytes"
    assert any(call.args[0] == "Storage stream opened" for call in mock_logger.info.call_args_list)

    # Object not found
    mock_s3_instance.get_object = AsyncMock(side_effect=S3NotFoundException())

    with patch(
        "nv_config_manager.ztp.api.files_v1.get_storage_client", return_value=mock_s3_instance
    ):
        rsp = client.get("/v1/files/arista_eos/4.29.3M/image.bin", headers=SSO_HEADERS)
        assert rsp.status_code == 404


def test_v1_files_get_range(client):
    """A single range is forwarded to storage and returned as a 206 response."""
    mock_content = b"3456"
    mock_streaming_body = MagicMock()

    async def async_iter_chunks(chunk_size):
        yield mock_content

    mock_streaming_body.iter_chunks = async_iter_chunks
    mock_s3_instance = MagicMock()
    mock_s3_instance.connect = AsyncMock(return_value=mock_s3_instance)
    mock_s3_instance.close = AsyncMock(return_value=None)
    mock_s3_instance.get_object = AsyncMock(
        return_value=ObjectStorageDownload(
            filename="image.bin",
            file_handle=mock_streaming_body,
            content_length=len(mock_content),
            total_length=10,
            backend="s3",
            object_key="arista_eos/4.29.3M/image.bin",
            byte_range=ObjectStorageByteRange(start=3, end=6),
        )
    )

    with patch(
        "nv_config_manager.ztp.api.files_v1.get_storage_client", return_value=mock_s3_instance
    ):
        rsp = client.get(
            "/v1/files/arista_eos/4.29.3M/image.bin",
            headers={**SSO_HEADERS, "Range": "bytes=3-6"},
        )

    assert rsp.status_code == 206
    assert rsp.content == mock_content
    assert rsp.headers["content-length"] == "4"
    assert rsp.headers["content-range"] == "bytes 3-6/10"
    assert rsp.headers["accept-ranges"] == "bytes"
    mock_s3_instance.get_object.assert_awaited_once_with(
        "arista_eos",
        "4.29.3M",
        "image.bin",
        range_header="bytes=3-6",
    )


def test_v1_files_get_unsatisfiable_range(client):
    """An unsatisfiable storage range becomes a standards-compliant HTTP 416."""
    mock_s3_instance = MagicMock()
    mock_s3_instance.connect = AsyncMock(return_value=mock_s3_instance)
    mock_s3_instance.close = AsyncMock(return_value=None)
    mock_s3_instance.get_object = AsyncMock(
        side_effect=ObjectStorageRangeNotSatisfiableException(total_length=10)
    )

    with patch(
        "nv_config_manager.ztp.api.files_v1.get_storage_client", return_value=mock_s3_instance
    ):
        rsp = client.get(
            "/v1/files/arista_eos/4.29.3M/image.bin",
            headers={**SSO_HEADERS, "Range": "bytes=10-"},
        )

    assert rsp.status_code == 416
    assert rsp.headers["accept-ranges"] == "bytes"
    assert rsp.headers["content-range"] == "bytes */10"


def test_v1_files_checksum(client):
    """Test v1 files checksum endpoint."""

    # Object found - mock S3Client with async context manager
    mock_s3_class = MagicMock()
    mock_s3_instance = MagicMock()
    mock_s3_instance.__aenter__ = AsyncMock(return_value=mock_s3_instance)
    mock_s3_instance.__aexit__ = AsyncMock(return_value=None)
    mock_s3_instance.get_checksum = AsyncMock(return_value="testchecksum")
    mock_s3_class.return_value = mock_s3_instance

    with patch(
        "nv_config_manager.ztp.api.files_v1.get_storage_client", return_value=mock_s3_instance
    ):
        rsp = client.get(
            "/v1/files/arista_eos/4.29.3M/image.bin/checksum",
            headers=SSO_HEADERS,
        )
        assert rsp.status_code == 200
        assert rsp.json() == {"checksum": "testchecksum"}

    # Object not found
    mock_s3_instance.get_checksum = AsyncMock(side_effect=S3NotFoundException())

    with patch(
        "nv_config_manager.ztp.api.files_v1.get_storage_client", return_value=mock_s3_instance
    ):
        rsp = client.get(
            "/v1/files/arista_eos/4.29.3M/image.bin/checksum",
            headers=SSO_HEADERS,
        )
        assert rsp.status_code == 404


def test_v1_files_list(client):
    """Test listing files for a given platform and version."""
    mock_list_result = [
        {"file": "image.bin", "last_modified": datetime(2015, 1, 1), "size": 12345},
        {"file": "image2.bin", "last_modified": datetime(2015, 1, 1), "size": 12345},
        {"file": "randomfile", "last_modified": datetime(2015, 1, 1), "size": 12345},
    ]

    expected_rsp_json = [
        {
            "file": "image.bin",
            "last_modified": datetime(2015, 1, 1).isoformat(),
            "size": 12345,
        },
        {
            "file": "image2.bin",
            "last_modified": datetime(2015, 1, 1).isoformat(),
            "size": 12345,
        },
        {
            "file": "randomfile",
            "last_modified": datetime(2015, 1, 1).isoformat(),
            "size": 12345,
        },
    ]
    # Mock S3Client with async context manager
    mock_s3_class = MagicMock()
    mock_s3_instance = MagicMock()
    mock_s3_instance.__aenter__ = AsyncMock(return_value=mock_s3_instance)
    mock_s3_instance.__aexit__ = AsyncMock(return_value=None)
    mock_s3_instance.list_object_keys = AsyncMock(return_value=mock_list_result)
    mock_s3_class.return_value = mock_s3_instance

    with patch(
        "nv_config_manager.ztp.api.files_v1.get_storage_client", return_value=mock_s3_instance
    ):
        rsp = client.get("/v1/files/arista_eos/4.29.3M", headers=SSO_HEADERS)
        assert rsp.json() == expected_rsp_json


def test_v1_files_upload_file(client):
    """Test upload_file endpoint."""
    sso_headers = {"X-Auth-Request-Email": "test@nvidia.com"}

    # File created successfully - mock S3Client with async context manager
    mock_s3_class = MagicMock()
    mock_s3_instance = MagicMock()
    mock_s3_instance.__aenter__ = AsyncMock(return_value=mock_s3_instance)
    mock_s3_instance.__aexit__ = AsyncMock(return_value=None)
    mock_s3_instance.upload_file = AsyncMock(return_value=None)
    mock_s3_class.return_value = mock_s3_instance

    with patch(
        "nv_config_manager.ztp.api.files_v1.get_storage_client", return_value=mock_s3_instance
    ):
        rsp = client.post(
            "/v1/files/cumulus_linux/5.7.0/test?checksum=testchecksum&overwrite=False",
            files={"file": ("filename", io.BytesIO(b"test"), "plain/text")},
            headers=sso_headers,
        )
        assert rsp.status_code == 200

    # File exists
    mock_s3_instance.upload_file = AsyncMock(side_effect=S3ExistsException())

    with patch(
        "nv_config_manager.ztp.api.files_v1.get_storage_client", return_value=mock_s3_instance
    ):
        rsp = client.post(
            "/v1/files/cumulus_linux/5.7.0/test?checksum=testchecksum&overwrite=False",
            files={"file": ("filename", io.BytesIO(b"test"), "plain/text")},
            headers=sso_headers,
        )
        assert rsp.status_code == 400

    # Unauthenticated request should be rejected
    with patch(
        "nv_config_manager.ztp.api.files_v1.get_storage_client", return_value=mock_s3_instance
    ):
        rsp = client.post(
            "/v1/files/cumulus_linux/5.7.0/test?checksum=testchecksum&overwrite=False",
            files={"file": ("filename", io.BytesIO(b"test"), "plain/text")},
        )
        assert rsp.status_code == 403


def test_v1_files_upload_file_auth_disabled(client):
    """Test upload_file endpoint when auth is disabled (no auth deployment)."""
    mock_s3_instance = MagicMock()
    mock_s3_instance.__aenter__ = AsyncMock(return_value=mock_s3_instance)
    mock_s3_instance.__aexit__ = AsyncMock(return_value=None)
    mock_s3_instance.upload_file = AsyncMock(return_value=None)

    with patch("nv_config_manager.common.auth._auth_config", AuthConfig(required=False)):
        with patch(
            "nv_config_manager.ztp.api.files_v1.get_storage_client", return_value=mock_s3_instance
        ):
            # Upload without any SSO headers should succeed
            rsp = client.post(
                "/v1/files/cumulus_linux/5.7.0/test?checksum=testchecksum&overwrite=False",
                files={"file": ("filename", io.BytesIO(b"test"), "plain/text")},
            )
            assert rsp.status_code == 200


def test_v1_files_list_all(client):
    """Test listing all files endpoint."""
    mock_list_result = [
        {
            "key": "cumulus-linux/5.4.0/image.bin",
            "last_modified": datetime(2024, 1, 1),
            "size": 1024,
            "etag": "abc123",
            "metadata": {"sha256-checksum": "def456"},
            "tags": {"firmware-image": "true"},
        },
        {
            "key": "sonic/4.0.0/image.bin",
            "last_modified": datetime(2024, 1, 2),
            "size": 2048,
            "etag": "xyz789",
            "metadata": {"sha256-checksum": "ghi012"},
            "tags": {"platform": "sonic"},
        },
    ]

    expected_rsp_json = [
        {
            "key": "cumulus-linux/5.4.0/image.bin",
            "last_modified": datetime(2024, 1, 1).isoformat(),
            "size": 1024,
            "etag": "abc123",
            "metadata": {"sha256-checksum": "def456"},
            "tags": {"firmware-image": "true"},
        },
        {
            "key": "sonic/4.0.0/image.bin",
            "last_modified": datetime(2024, 1, 2).isoformat(),
            "size": 2048,
            "etag": "xyz789",
            "metadata": {"sha256-checksum": "ghi012"},
            "tags": {"platform": "sonic"},
        },
    ]

    # Mock S3Client with async context manager
    mock_s3_class = MagicMock()
    mock_s3_instance = MagicMock()
    mock_s3_instance.__aenter__ = AsyncMock(return_value=mock_s3_instance)
    mock_s3_instance.__aexit__ = AsyncMock(return_value=None)
    mock_s3_instance.list_all_objects = AsyncMock(return_value=mock_list_result)
    mock_s3_class.return_value = mock_s3_instance

    with patch(
        "nv_config_manager.ztp.api.files_v1.get_storage_client", return_value=mock_s3_instance
    ):
        rsp = client.get("/v1/files/", headers=SSO_HEADERS)
        assert rsp.status_code == 200
        assert rsp.json() == expected_rsp_json
