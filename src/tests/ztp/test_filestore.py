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
"""Unit tests for FileStoreClient."""

import asyncio
import io
import json
import os
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from nv_config_manager.ztp.filestore import (
    FileStoreClient,
    FileStoreException,
    FileStoreExistsException,
    FileStoreNotFoundException,
)
from nv_config_manager.ztp.storage import ObjectStorageChangedException

MOCK_CONTENT = b"testcontent"
MOCK_MANIFEST = {
    "images": [
        {
            "platform": "cumulus-linux",
            "version": "5.9.0",
            "path": "cumulus-linux/5.9.0/cumulus.bin",
            "filename": "cumulus.bin",
            "sha256": "abc123def456",
        },
        {
            "platform": "cumulus-linux",
            "version": "5.14.0",
            "path": "cumulus-linux/5.14.0/cumulus.bin",
            "filename": "cumulus.bin",
            "sha256": "def789ghi012",
        },
        {
            "platform": "mlnx-os",
            "version": "3.10.4000",
            "path": "mlnx-os/3.10.4000/mlnx-os.bin",
            "filename": "mlnx-os.bin",
            "sha256": "ghi345jkl678",
        },
    ]
}


@pytest.fixture
def temp_storage_dir():
    """Create a temporary storage directory with manifest."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Create manifest.json
        manifest_path = tmpdir_path / "manifest.json"
        manifest_path.write_text(json.dumps(MOCK_MANIFEST))

        # Create image directories and files
        for image in MOCK_MANIFEST["images"]:
            image_path = tmpdir_path / image["path"]
            image_path.parent.mkdir(parents=True, exist_ok=True)
            image_path.write_bytes(MOCK_CONTENT)

        yield tmpdir


@pytest.mark.asyncio
async def test_filestore_client_initialization_success(temp_storage_dir, monkeypatch):
    """Test FileStoreClient initialization with valid FILE_STORE_PATH."""
    monkeypatch.setenv("FILE_STORE_PATH", temp_storage_dir)

    client = FileStoreClient()
    await client.connect()

    assert client.base_path == Path(temp_storage_dir)
    assert client.manifest is not None
    assert len(client.manifest["images"]) == 3


@pytest.mark.asyncio
async def test_filestore_client_initialization_no_env_var(monkeypatch):
    """Test FileStoreClient initialization fails without FILE_STORE_PATH."""
    monkeypatch.delenv("FILE_STORE_PATH", raising=False)

    with pytest.raises(
        FileStoreException, match="FILE_STORE_PATH environment variable must be set"
    ):
        FileStoreClient()


@pytest.mark.asyncio
async def test_filestore_client_initialization_invalid_path(monkeypatch):
    """Test FileStoreClient initialization fails with non-existent path."""
    monkeypatch.setenv("FILE_STORE_PATH", "/non/existent/path")

    with pytest.raises(FileStoreException, match="File store base path does not exist"):
        FileStoreClient()


@pytest.mark.asyncio
async def test_filestore_client_initialization_no_manifest(monkeypatch):
    """Test FileStoreClient bootstraps an empty manifest when none exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setenv("FILE_STORE_PATH", tmpdir)

        client = FileStoreClient()
        await client.connect()

        # An empty manifest should have been created automatically
        assert client.manifest is not None
        assert client.manifest["images"] == []
        assert (Path(tmpdir) / "manifest.json").exists()


@pytest.mark.asyncio
async def test_get_firmware_object_success(temp_storage_dir, monkeypatch):
    """Test successful firmware object retrieval."""
    monkeypatch.setenv("FILE_STORE_PATH", temp_storage_dir)

    client = FileStoreClient()
    await client.connect()

    filename, file_handle = await client.get_firmware_object("cumulus-linux", "5.9.0")

    assert filename == "cumulus.bin"
    content = file_handle.read()
    assert content == MOCK_CONTENT
    file_handle.close()


@pytest.mark.asyncio
async def test_get_firmware_object_platform_not_found(temp_storage_dir, monkeypatch):
    """Test firmware object retrieval with non-existent platform."""
    monkeypatch.setenv("FILE_STORE_PATH", temp_storage_dir)

    client = FileStoreClient()
    await client.connect()

    with pytest.raises(
        FileStoreNotFoundException,
        match="Image not found in manifest: platform=nonexistent, version=1.0.0",
    ):
        await client.get_firmware_object("nonexistent", "1.0.0")


@pytest.mark.asyncio
async def test_get_firmware_object_version_not_found(temp_storage_dir, monkeypatch):
    """Test firmware object retrieval with non-existent version."""
    monkeypatch.setenv("FILE_STORE_PATH", temp_storage_dir)

    client = FileStoreClient()
    await client.connect()

    with pytest.raises(
        FileStoreNotFoundException,
        match="Image not found in manifest: platform=cumulus-linux, version=99.99.99",
    ):
        await client.get_firmware_object("cumulus-linux", "99.99.99")


@pytest.mark.asyncio
async def test_get_firmware_object_file_missing(temp_storage_dir, monkeypatch):
    """Test firmware object retrieval when file doesn't exist on disk."""
    monkeypatch.setenv("FILE_STORE_PATH", temp_storage_dir)

    client = FileStoreClient()
    await client.connect()

    # Remove the actual file
    image_path = Path(temp_storage_dir) / "cumulus-linux/5.9.0/cumulus.bin"
    image_path.unlink()

    with pytest.raises(FileStoreNotFoundException, match="Firmware image file not found"):
        await client.get_firmware_object("cumulus-linux", "5.9.0")


@pytest.mark.asyncio
async def test_get_firmware_checksum_success(temp_storage_dir, monkeypatch):
    """Test successful firmware checksum retrieval."""
    monkeypatch.setenv("FILE_STORE_PATH", temp_storage_dir)

    client = FileStoreClient()
    await client.connect()

    checksum = await client.get_firmware_checksum("cumulus-linux", "5.9.0")
    assert checksum == "abc123def456"


@pytest.mark.asyncio
async def test_get_firmware_checksum_not_found(temp_storage_dir, monkeypatch):
    """Test firmware checksum retrieval with non-existent image."""
    monkeypatch.setenv("FILE_STORE_PATH", temp_storage_dir)

    client = FileStoreClient()
    await client.connect()

    with pytest.raises(FileStoreNotFoundException, match="Image not found in manifest"):
        await client.get_firmware_checksum("nonexistent", "1.0.0")


@pytest.mark.asyncio
async def test_get_object_success(temp_storage_dir, monkeypatch):
    """Test successful arbitrary file retrieval."""
    monkeypatch.setenv("FILE_STORE_PATH", temp_storage_dir)

    client = FileStoreClient()
    await client.connect()

    # Create a non-firmware file
    test_file_path = Path(temp_storage_dir) / "cumulus-linux/5.9.0/config.txt"
    test_file_path.write_bytes(b"config content")

    filename, file_handle = await client.get_object("cumulus-linux", "5.9.0", "config.txt")

    assert filename == "config.txt"
    content = file_handle.read()
    assert content == b"config content"
    file_handle.close()


@pytest.mark.asyncio
async def test_get_object_not_found(temp_storage_dir, monkeypatch):
    """Test arbitrary file retrieval with non-existent file."""
    monkeypatch.setenv("FILE_STORE_PATH", temp_storage_dir)

    client = FileStoreClient()
    await client.connect()

    with pytest.raises(FileStoreNotFoundException, match="File not found"):
        await client.get_object("cumulus-linux", "5.9.0", "nonexistent.txt")


@pytest.mark.asyncio
async def test_get_object_deleted_while_opening(temp_storage_dir, monkeypatch):
    """A file removed immediately before open is still reported as not found."""
    monkeypatch.setenv("FILE_STORE_PATH", temp_storage_dir)
    client = FileStoreClient()
    await client.connect()

    with (
        patch("builtins.open", side_effect=FileNotFoundError),
        pytest.raises(FileStoreNotFoundException, match="File not found"),
    ):
        await client.get_object("cumulus-linux", "5.9.0", "config.txt")


@pytest.mark.asyncio
async def test_get_object_range(temp_storage_dir, monkeypatch):
    """File storage seeks to the requested byte range and reports its exact length."""
    monkeypatch.setenv("FILE_STORE_PATH", temp_storage_dir)
    client = FileStoreClient()
    await client.connect()

    test_file_path = Path(temp_storage_dir) / "cumulus-linux/5.9.0/config.txt"
    test_file_path.write_bytes(b"0123456789")

    download = await client.get_object(
        "cumulus-linux",
        "5.9.0",
        "config.txt",
        range_header="bytes=3-6",
    )

    assert download.content_length == 4
    assert download.total_length == 10
    assert download.byte_range is not None
    assert download.byte_range.start == 3
    assert download.byte_range.end == 6
    assert download.file_handle.read(download.content_length) == b"3456"
    download.file_handle.close()


@pytest.mark.asyncio
async def test_get_checksum_success(temp_storage_dir, monkeypatch):
    """Test successful checksum retrieval for firmware image."""
    monkeypatch.setenv("FILE_STORE_PATH", temp_storage_dir)

    client = FileStoreClient()
    await client.connect()

    checksum = await client.get_checksum("cumulus-linux", "5.9.0", "cumulus.bin")
    assert checksum == "abc123def456"


@pytest.mark.asyncio
async def test_get_checksum_not_in_manifest(temp_storage_dir, monkeypatch):
    """Test checksum retrieval for file not in manifest."""
    monkeypatch.setenv("FILE_STORE_PATH", temp_storage_dir)

    client = FileStoreClient()
    await client.connect()

    with pytest.raises(
        FileStoreNotFoundException, match="Checksum not found for file.*File not in manifest"
    ):
        await client.get_checksum("cumulus-linux", "5.9.0", "other-file.txt")


@pytest.mark.asyncio
async def test_get_object_metadata_success(temp_storage_dir, monkeypatch):
    """Test successful object metadata retrieval."""
    monkeypatch.setenv("FILE_STORE_PATH", temp_storage_dir)

    client = FileStoreClient()
    await client.connect()

    metadata = await client.get_object_metadata("cumulus-linux", "5.9.0", "cumulus.bin")

    assert metadata["size"] == len(MOCK_CONTENT)
    assert "last_modified" in metadata
    assert metadata["metadata"]["sha256-checksum"] == "abc123def456"
    assert isinstance(metadata["etag"], str)


@pytest.mark.asyncio
async def test_get_object_range_rejects_changed_revision(temp_storage_dir, monkeypatch):
    """Multi-range FileStore downloads fail if the backing file changes."""
    monkeypatch.setenv("FILE_STORE_PATH", temp_storage_dir)
    client = FileStoreClient()
    await client.connect()

    metadata = await client.get_object_metadata("cumulus-linux", "5.9.0", "cumulus.bin")
    file_path = Path(temp_storage_dir) / "cumulus-linux/5.9.0/cumulus.bin"
    file_path.write_bytes(b"replacement")

    with pytest.raises(ObjectStorageChangedException, match="changed while"):
        await client.get_object(
            "cumulus-linux",
            "5.9.0",
            "cumulus.bin",
            range_header="bytes=0-3",
            known_total_length=metadata["size"],
            if_match=metadata["etag"],
        )


@pytest.mark.asyncio
async def test_get_object_metadata_not_found(temp_storage_dir, monkeypatch):
    """Test object metadata retrieval with non-existent file."""
    monkeypatch.setenv("FILE_STORE_PATH", temp_storage_dir)

    client = FileStoreClient()
    await client.connect()

    with pytest.raises(FileStoreNotFoundException, match="File not found"):
        await client.get_object_metadata("cumulus-linux", "5.9.0", "nonexistent.txt")


@pytest.mark.asyncio
async def test_list_object_keys_success(temp_storage_dir, monkeypatch):
    """Test successful listing of object keys."""
    monkeypatch.setenv("FILE_STORE_PATH", temp_storage_dir)

    client = FileStoreClient()
    await client.connect()

    objects = await client.list_object_keys("cumulus-linux", "5.9.0")

    assert len(objects) == 1
    assert objects[0]["file"] == "cumulus.bin"
    assert objects[0]["size"] == len(MOCK_CONTENT)


@pytest.mark.asyncio
async def test_list_object_keys_empty_directory(temp_storage_dir, monkeypatch):
    """Test listing object keys in non-existent directory."""
    monkeypatch.setenv("FILE_STORE_PATH", temp_storage_dir)

    client = FileStoreClient()
    await client.connect()

    objects = await client.list_object_keys("nonexistent", "1.0.0")
    assert objects == []


@pytest.mark.asyncio
async def test_list_all_objects_success(temp_storage_dir, monkeypatch):
    """Test successful listing of all objects."""
    monkeypatch.setenv("FILE_STORE_PATH", temp_storage_dir)

    client = FileStoreClient()
    await client.connect()

    objects = await client.list_all_objects()

    assert len(objects) == 3
    for obj in objects:
        assert "sha256-checksum" in obj["metadata"]


@pytest.mark.asyncio
async def test_upload_file_success(temp_storage_dir, monkeypatch):
    """Test successful file upload."""
    monkeypatch.setenv("FILE_STORE_PATH", temp_storage_dir)

    client = FileStoreClient()
    await client.connect()

    file_content = io.BytesIO(b"new file content")
    await client.upload_file(
        "test-platform", "1.0.0", "test-file.bin", "newchecksum123", file_content, overwrite=False
    )

    # Verify file was created
    uploaded_file = Path(temp_storage_dir) / "test-platform/1.0.0/test-file.bin"
    assert uploaded_file.exists()
    assert uploaded_file.read_bytes() == b"new file content"


@pytest.mark.asyncio
async def test_upload_file_already_exists(temp_storage_dir, monkeypatch):
    """Test file upload when file already exists."""
    monkeypatch.setenv("FILE_STORE_PATH", temp_storage_dir)

    client = FileStoreClient()
    await client.connect()

    # Create an existing file
    existing_file = Path(temp_storage_dir) / "test-platform/1.0.0/existing.bin"
    existing_file.parent.mkdir(parents=True, exist_ok=True)
    existing_file.write_bytes(b"existing content")

    file_content = io.BytesIO(b"new content")

    with pytest.raises(FileStoreExistsException, match="File already exists"):
        await client.upload_file(
            "test-platform", "1.0.0", "existing.bin", "checksum", file_content, overwrite=False
        )


@pytest.mark.asyncio
async def test_upload_file_overwrite_success(temp_storage_dir, monkeypatch):
    """Test successful file upload with overwrite."""
    monkeypatch.setenv("FILE_STORE_PATH", temp_storage_dir)

    client = FileStoreClient()
    await client.connect()

    # Create an existing file
    existing_file = Path(temp_storage_dir) / "test-platform/1.0.0/existing.bin"
    existing_file.parent.mkdir(parents=True, exist_ok=True)
    existing_file.write_bytes(b"old content")

    file_content = io.BytesIO(b"new content")
    await client.upload_file(
        "test-platform", "1.0.0", "existing.bin", "newchecksum", file_content, overwrite=True
    )

    # Verify file was overwritten
    assert existing_file.read_bytes() == b"new content"


class _FailsMidCopy(io.BytesIO):
    """An upload whose source stops after its first chunk."""

    def read(self, size: int | None = -1) -> bytes:
        if self.tell():
            raise OSError("connection lost")
        return super().read(8)


@pytest.mark.asyncio
async def test_interrupted_overwrite_keeps_the_previous_file(
    temp_storage_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A half-written image would no longer match the checksum the manifest publishes."""
    monkeypatch.setenv("FILE_STORE_PATH", temp_storage_dir)
    client = FileStoreClient()
    await client.connect()
    await client.upload_file("test-platform", "1.0.0", "image.bin", "old", io.BytesIO(b"old"))
    manifest_before = (Path(temp_storage_dir) / "manifest.json").read_text()

    with pytest.raises(OSError, match="connection lost"):
        await client.upload_file(
            "test-platform", "1.0.0", "image.bin", "new", _FailsMidCopy(b"x" * 64), overwrite=True
        )

    directory = Path(temp_storage_dir) / "test-platform/1.0.0"
    assert (directory / "image.bin").read_bytes() == b"old"
    assert [p.name for p in directory.iterdir()] == ["image.bin"]
    assert list((Path(temp_storage_dir) / ".uploads").iterdir()) == []
    assert (Path(temp_storage_dir) / "manifest.json").read_text() == manifest_before


@pytest.mark.asyncio
async def test_interrupted_new_upload_can_be_retried(
    temp_storage_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A leftover file with no manifest entry used to block the retry."""
    monkeypatch.setenv("FILE_STORE_PATH", temp_storage_dir)
    client = FileStoreClient()
    await client.connect()

    with pytest.raises(OSError, match="connection lost"):
        await client.upload_file(
            "test-platform", "1.0.0", "new.bin", "sum", _FailsMidCopy(b"x" * 64)
        )
    await client.upload_file("test-platform", "1.0.0", "new.bin", "sum", io.BytesIO(b"complete"))

    assert (Path(temp_storage_dir) / "test-platform/1.0.0/new.bin").read_bytes() == b"complete"


@pytest.mark.asyncio
async def test_cancelled_upload_still_records_the_file_it_stored(
    temp_storage_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancelled request must not leave a stored file out of the manifest.

    This covers cancellation inside a running process. A process killed
    between publishing the file and updating the manifest can still leave the
    two out of step.
    """
    monkeypatch.setenv("FILE_STORE_PATH", temp_storage_dir)
    client = FileStoreClient()
    await client.connect()
    reached_manifest = asyncio.Event()
    release = asyncio.Event()
    add_to_manifest = client._add_to_manifest

    async def slow_add(*args: Any, **kwargs: Any) -> None:
        reached_manifest.set()
        await release.wait()
        await add_to_manifest(*args, **kwargs)

    monkeypatch.setattr(client, "_add_to_manifest", slow_add)
    upload = asyncio.create_task(
        client.upload_file("test-platform", "1.0.0", "image.bin", "sum", io.BytesIO(b"data"))
    )
    await asyncio.wait_for(reached_manifest.wait(), timeout=5.0)
    upload.cancel()
    with pytest.raises(asyncio.CancelledError):
        await upload
    release.set()
    for _ in range(100):
        if "image.bin" in (Path(temp_storage_dir) / "manifest.json").read_text():
            break
        await asyncio.sleep(0.01)

    manifest = json.loads((Path(temp_storage_dir) / "manifest.json").read_text())
    assert "test-platform/1.0.0/image.bin" in [image["path"] for image in manifest["images"]]


@pytest.mark.asyncio
async def test_abandoned_staged_files_are_removed(
    temp_storage_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A staged file left by a process that stopped mid-copy is cleaned up; a live one is not."""
    monkeypatch.setenv("FILE_STORE_PATH", temp_storage_dir)
    client = FileStoreClient()
    await client.connect()
    staging = Path(temp_storage_dir) / ".uploads"
    staging.mkdir(exist_ok=True)
    stale = staging / "abandoned"
    stale.write_bytes(b"abandoned")
    os.utime(stale, (0, 0))
    fresh = staging / "in-progress"
    fresh.write_bytes(b"in progress")

    await client.upload_file("test-platform", "1.0.0", "image.bin", "sum", io.BytesIO(b"data"))

    assert sorted(p.name for p in staging.iterdir()) == ["in-progress"]


@pytest.mark.asyncio
async def test_stored_files_are_never_mistaken_for_staged_ones(
    temp_storage_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any name a caller can upload stays listed and survives later uploads."""
    monkeypatch.setenv("FILE_STORE_PATH", temp_storage_dir)
    client = FileStoreClient()
    await client.connect()
    await client.upload_file(
        "test-platform", "1.0.0", ".vendor.uploading", "sum", io.BytesIO(b"kept")
    )
    stored = Path(temp_storage_dir) / "test-platform/1.0.0/.vendor.uploading"
    os.utime(stored, (0, 0))

    await client.upload_file("test-platform", "1.0.0", "image.bin", "sum", io.BytesIO(b"data"))

    assert stored.read_bytes() == b"kept"
    assert sorted(o["file"] for o in await client.list_object_keys("test-platform", "1.0.0")) == [
        ".vendor.uploading",
        "image.bin",
    ]


@pytest.mark.asyncio
async def test_a_filename_at_the_filesystem_limit_can_be_uploaded(
    temp_storage_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The staging name must not make a valid long filename too long to write."""
    monkeypatch.setenv("FILE_STORE_PATH", temp_storage_dir)
    client = FileStoreClient()
    await client.connect()
    filename = "f" * 251 + ".bin"

    await client.upload_file("test-platform", "1.0.0", filename, "sum", io.BytesIO(b"data"))

    assert (Path(temp_storage_dir) / "test-platform/1.0.0" / filename).read_bytes() == b"data"


@pytest.mark.asyncio
async def test_a_duplicate_upload_that_finishes_first_is_not_replaced(
    temp_storage_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without overwrite, the name must still be free when the copy completes."""
    monkeypatch.setenv("FILE_STORE_PATH", temp_storage_dir)
    client = FileStoreClient()
    await client.connect()
    destination = Path(temp_storage_dir) / "test-platform/1.0.0/image.bin"

    class _OtherUploadFinishesDuringCopy(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            destination.write_bytes(b"first")
            return super().read(size)

    with pytest.raises(FileStoreExistsException, match="File already exists"):
        await client.upload_file(
            "test-platform", "1.0.0", "image.bin", "sum", _OtherUploadFinishesDuringCopy(b"second")
        )

    assert destination.read_bytes() == b"first"
    assert list((Path(temp_storage_dir) / ".uploads").iterdir()) == []


@pytest.mark.asyncio
async def test_upload_file_firmware_image_overwrite(temp_storage_dir, monkeypatch):
    """Test that firmware images in manifest can be overwritten with overwrite=True."""
    monkeypatch.setenv("FILE_STORE_PATH", temp_storage_dir)

    client = FileStoreClient()
    await client.connect()

    file_content = io.BytesIO(b"updated firmware")

    await client.upload_file(
        "cumulus-linux", "5.9.0", "cumulus.bin", "newchecksum", file_content, overwrite=True
    )

    # Verify file was overwritten
    uploaded_file = Path(temp_storage_dir) / "cumulus-linux/5.9.0/cumulus.bin"
    assert uploaded_file.read_bytes() == b"updated firmware"


@pytest.mark.asyncio
async def test_platform_name_normalization(temp_storage_dir, monkeypatch):
    """Test that platform names with spaces are normalized correctly."""
    monkeypatch.setenv("FILE_STORE_PATH", temp_storage_dir)

    # Add an image with spaces to manifest
    modified_manifest = MOCK_MANIFEST.copy()
    modified_manifest["images"].append(
        {
            "platform": "Cumulus Linux",
            "version": "5.10.0",
            "path": "cumulus_linux/5.10.0/cumulus.bin",
            "filename": "cumulus.bin",
            "sha256": "xyz789abc012",
        }
    )

    manifest_path = Path(temp_storage_dir) / "manifest.json"
    manifest_path.write_text(json.dumps(modified_manifest))

    # Create the image file
    image_path = Path(temp_storage_dir) / "cumulus_linux/5.10.0/cumulus.bin"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(MOCK_CONTENT)

    client = FileStoreClient()
    await client.connect()

    # Should find the image even with spaces in the platform name
    filename, file_handle = await client.get_firmware_object("Cumulus Linux", "5.10.0")
    assert filename == "cumulus.bin"
    file_handle.close()


@pytest.mark.asyncio
async def test_context_manager(temp_storage_dir, monkeypatch):
    """Test FileStoreClient as async context manager."""
    monkeypatch.setenv("FILE_STORE_PATH", temp_storage_dir)

    async with FileStoreClient() as client:
        assert client.manifest is not None
        checksum = await client.get_firmware_checksum("cumulus-linux", "5.9.0")
        assert checksum == "abc123def456"
