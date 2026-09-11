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
"""Unit tests for storage factory and byte-range parsing."""

import pytest

from nv_config_manager.common.config import get_storage_client
from nv_config_manager.common.config_loader import clear_config_cache
from nv_config_manager.ztp.filestore import FileStoreClient
from nv_config_manager.ztp.s3 import S3Client
from nv_config_manager.ztp.storage import (
    ObjectStorageByteRange,
    ObjectStorageRangeNotSatisfiableException,
    parse_http_range,
)


@pytest.fixture(autouse=True)
def reset_config_cache():
    """Keep config loaded in one test from leaking into another."""
    clear_config_cache()
    yield
    clear_config_cache()


def test_get_storage_client_default_s3(monkeypatch):
    """Test that S3Client is returned by default."""
    monkeypatch.delenv("STORAGE_TYPE", raising=False)
    monkeypatch.delenv("FILE_STORE_PATH", raising=False)

    client = get_storage_client()
    assert isinstance(client, S3Client)


def test_get_storage_client_explicit_s3(monkeypatch):
    """Test that S3Client is returned when STORAGE_TYPE=s3."""
    monkeypatch.setenv("STORAGE_TYPE", "s3")
    monkeypatch.delenv("FILE_STORE_PATH", raising=False)

    client = get_storage_client()
    assert isinstance(client, S3Client)


def test_get_storage_client_file_storage_success(monkeypatch, tmp_path):
    """Test that FileStoreClient is returned when STORAGE_TYPE=file."""
    monkeypatch.setenv("STORAGE_TYPE", "file")
    monkeypatch.setenv("FILE_STORE_PATH", str(tmp_path))

    # Create a minimal manifest
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text('{"images": []}')

    client = get_storage_client()
    assert isinstance(client, FileStoreClient)


def test_get_storage_client_file_storage_missing_path(monkeypatch):
    """Test that ValueError is raised when STORAGE_TYPE=file but FILE_STORE_PATH not set."""
    monkeypatch.setenv("STORAGE_TYPE", "file")
    monkeypatch.delenv("FILE_STORE_PATH", raising=False)

    with pytest.raises(ValueError, match="storage_type is 'file' but file_store_path is not set"):
        get_storage_client()


def test_get_storage_client_case_insensitive(monkeypatch, tmp_path):
    """Test that STORAGE_TYPE is case insensitive."""
    monkeypatch.setenv("STORAGE_TYPE", "FILE")
    monkeypatch.setenv("FILE_STORE_PATH", str(tmp_path))

    # Create a minimal manifest
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text('{"images": []}')

    client = get_storage_client()
    assert isinstance(client, FileStoreClient)

    # Test S3 case insensitivity
    monkeypatch.setenv("STORAGE_TYPE", "S3")
    client = get_storage_client()
    assert isinstance(client, S3Client)


def test_get_storage_client_s3_from_ini(monkeypatch, tmp_path):
    """Test that S3 settings are read from [ztp] with env fallback still available."""
    config_file = tmp_path / "nv-config-manager.ini"
    config_file.write_text(
        "\n".join(
            [
                "[ztp]",
                "storage_type = s3",
                "s3_bucket = ini-bucket",
                "s3_endpoint = https://s3.example.test",
                "s3_region = us-west-2",
                "s3_access_key = ini-access-key",
                "s3_secret_key = ini-secret-key",
            ]
        )
    )
    monkeypatch.setenv("NV_CONFIG_MANAGER_INI", str(config_file))
    monkeypatch.setenv("CUSTOM_S3_BUCKET", "env-bucket")
    monkeypatch.setenv("CUSTOM_S3_ENDPOINT", "https://env-s3.example.test")
    monkeypatch.setenv("CUSTOM_S3_ACCESS_KEY", "env-access-key")
    monkeypatch.setenv("CUSTOM_S3_SECRET_KEY", "env-secret-key")

    client = get_storage_client()

    assert isinstance(client, S3Client)
    assert client.bucket == "ini-bucket"
    assert client.custom_endpoint == "https://s3.example.test"
    assert client.region == "us-west-2"
    assert client.custom_access_key == "ini-access-key"
    assert client.custom_secret_key == "ini-secret-key"


def test_get_storage_client_s3_blank_ini_values_fall_back_to_env(monkeypatch, tmp_path):
    """Blank S3 INI values should not suppress environment fallback."""
    config_file = tmp_path / "nv-config-manager.ini"
    config_file.write_text(
        "\n".join(
            [
                "[ztp]",
                "storage_type = s3",
                "s3_bucket = ",
                "s3_endpoint = ",
                "s3_access_key = ",
                "s3_secret_key = ",
            ]
        )
    )
    monkeypatch.setenv("NV_CONFIG_MANAGER_INI", str(config_file))
    monkeypatch.setenv("CUSTOM_S3_BUCKET", "env-bucket")
    monkeypatch.setenv("CUSTOM_S3_ENDPOINT", "https://env-s3.example.test")
    monkeypatch.setenv("CUSTOM_S3_ACCESS_KEY", "env-access-key")
    monkeypatch.setenv("CUSTOM_S3_SECRET_KEY", "env-secret-key")

    client = get_storage_client()

    assert isinstance(client, S3Client)
    assert client.bucket == "env-bucket"
    assert client.custom_endpoint == "https://env-s3.example.test"
    assert client.custom_access_key == "env-access-key"
    assert client.custom_secret_key == "env-secret-key"


def test_get_storage_client_file_storage_from_ini(monkeypatch, tmp_path):
    """Test that file storage can be configured from [ztp]."""
    storage_path = tmp_path / "images"
    storage_path.mkdir()
    (storage_path / "manifest.json").write_text('{"images": []}')
    config_file = tmp_path / "nv-config-manager.ini"
    config_file.write_text(
        "\n".join(
            [
                "[ztp]",
                "storage_type = file",
                f"file_store_path = {storage_path}",
            ]
        )
    )
    monkeypatch.setenv("NV_CONFIG_MANAGER_INI", str(config_file))
    monkeypatch.delenv("STORAGE_TYPE", raising=False)
    monkeypatch.delenv("FILE_STORE_PATH", raising=False)

    client = get_storage_client()

    assert isinstance(client, FileStoreClient)
    assert client.base_path == storage_path


@pytest.mark.parametrize(
    ("range_header", "total_length", "expected"),
    [
        (None, 10, None),
        ("bytes=2-5", 10, ObjectStorageByteRange(start=2, end=5)),
        ("bytes=2-", 10, ObjectStorageByteRange(start=2, end=9)),
        ("bytes=-4", 10, ObjectStorageByteRange(start=6, end=9)),
        ("bytes=8-99", 10, ObjectStorageByteRange(start=8, end=9)),
    ],
)
def test_parse_http_range(
    range_header: str | None,
    total_length: int,
    expected: ObjectStorageByteRange | None,
) -> None:
    """Single HTTP byte ranges resolve to their inclusive bounds."""
    assert parse_http_range(range_header, total_length) == expected


@pytest.mark.parametrize(
    "range_header",
    ["bytes=", "bytes=10-", "bytes=5-2", "bytes=-0", "items=0-1", "bytes=0-1,2-3"],
)
def test_parse_http_range_rejects_unsatisfiable_or_multi_ranges(range_header: str) -> None:
    """Invalid and multi-range requests are consistently mapped to HTTP 416 upstream."""
    with pytest.raises(ObjectStorageRangeNotSatisfiableException) as exc_info:
        parse_http_range(range_header, 10)

    assert exc_info.value.total_length == 10
