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
"""Tests for how the archive handler settles each JetStream message."""

import base64
import gzip
import json
from configparser import ConfigParser
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nv_config_manager_infrastructure.nats.admin import ARCHIVE_CONSUMER_MAX_DELIVER

from nv_config_manager.temporal.archive import main as archive_main
from nv_config_manager.temporal.archive.main import (
    ARCHIVE_RETRY_DELAY_SECONDS,
    ArchiveRejectedError,
    handle_archive_msg,
)

EVENT = {"workflow_id": "wf-1", "publish_time": "2026-10-11T00:00:00+00:00"}


def _msg(payload: bytes | None = None, num_delivered: int = 1) -> MagicMock:
    msg = MagicMock()
    msg.data = json.dumps(EVENT).encode() if payload is None else payload
    msg.metadata.num_delivered = num_delivered
    msg.ack = AsyncMock()
    msg.nak = AsyncMock()
    msg.term = AsyncMock()
    return msg


def _settled(msg: MagicMock) -> list[str]:
    return [name for name in ("ack", "nak", "term") if getattr(msg, name).await_count]


@pytest.mark.asyncio
async def test_archived_workflow_is_acked() -> None:
    """A successful write acks the message and nothing else."""
    msg = _msg()
    with patch.object(archive_main, "_archive_workflow", new_callable=AsyncMock) as archive:
        await handle_archive_msg(msg)

    archive.assert_awaited_once_with(EVENT)
    assert _settled(msg) == ["ack"]


@pytest.mark.asyncio
async def test_failed_write_is_retried_later_instead_of_acked() -> None:
    """A backend outage hands the message back to JetStream with a delay."""
    msg = _msg(num_delivered=1)
    with patch.object(
        archive_main,
        "_archive_workflow",
        new_callable=AsyncMock,
        side_effect=RuntimeError("connection timeout"),
    ):
        await handle_archive_msg(msg)

    assert _settled(msg) == ["nak"]
    msg.nak.assert_awaited_once_with(delay=ARCHIVE_RETRY_DELAY_SECONDS)


@pytest.mark.asyncio
async def test_last_allowed_delivery_is_discarded() -> None:
    """A record that keeps failing stops occupying an in-flight slot."""
    msg = _msg(num_delivered=ARCHIVE_CONSUMER_MAX_DELIVER)
    with patch.object(
        archive_main,
        "_archive_workflow",
        new_callable=AsyncMock,
        side_effect=RuntimeError("connection timeout"),
    ):
        await handle_archive_msg(msg)

    assert _settled(msg) == ["term"]


@pytest.mark.asyncio
async def test_rejected_record_is_discarded_without_retry() -> None:
    """A record the backend refuses outright is not retried."""
    msg = _msg()
    with patch.object(
        archive_main,
        "_archive_workflow",
        new_callable=AsyncMock,
        side_effect=ArchiveRejectedError("status 400"),
    ):
        await handle_archive_msg(msg)

    assert _settled(msg) == ["term"]


@pytest.mark.asyncio
async def test_unreadable_message_is_discarded() -> None:
    """A payload that is not JSON is never handed to the archive backend."""
    msg = _msg(payload=b"not json")
    with patch.object(archive_main, "_archive_workflow", new_callable=AsyncMock) as archive:
        await handle_archive_msg(msg)

    archive.assert_not_awaited()
    assert _settled(msg) == ["term"]


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [b"[]", b'"workflow"', b"42", b"null"])
async def test_non_object_payload_is_discarded(payload: bytes) -> None:
    """Valid JSON that is not an object cannot name a workflow and is never retried."""
    msg = _msg(payload=payload)
    with patch.object(archive_main, "_archive_workflow", new_callable=AsyncMock) as archive:
        await handle_archive_msg(msg)

    archive.assert_not_awaited()
    assert _settled(msg) == ["term"]


@pytest.mark.asyncio
async def test_event_without_workflow_id_is_acked() -> None:
    """An event that names no workflow has nothing to archive."""
    msg = _msg(payload=json.dumps({"publish_time": "2026-10-11T00:00:00+00:00"}).encode())
    with patch.object(archive_main, "_archive_workflow", new_callable=AsyncMock) as archive:
        await handle_archive_msg(msg)

    archive.assert_not_awaited()
    assert _settled(msg) == ["ack"]


@pytest.fixture
def nvdataflow_backend(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Run _archive_nvdataflow against a fake client, whatever backend was imported."""
    config = ConfigParser()
    config.read_string(
        "[temporal]\napi_url = https://temporal.example\n[temporal.nvdataflow]\nproject = archive\n"
    )
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(archive_main, "config", config)
    monkeypatch.setattr(
        archive_main, "NVDataflowClient", MagicMock(return_value=client), raising=False
    )
    for name, value in {"base64": base64, "gzip": gzip, "datetime": datetime, "UTC": UTC}.items():
        monkeypatch.setattr(archive_main, name, value, raising=False)
    return client


WORKFLOW_DETAILS = {
    "id": "wf-1",
    "start_time": "2026-10-11T00:00:00+00:00",
    "close_time": None,
    "started_by": "scheduler",
    "status": "COMPLETED",
    "workflow_type": "BackupWorkflow",
    "search_attributes": {},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [201, 202])
async def test_nvdataflow_success_returns(nvdataflow_backend: MagicMock, status: int) -> None:
    """Both NVDataflow success codes count as archived."""
    nvdataflow_backend.post = AsyncMock(return_value=status)

    await archive_main._archive_nvdataflow(dict(WORKFLOW_DETAILS))


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 413])
async def test_nvdataflow_rejection_is_permanent(
    nvdataflow_backend: MagicMock, status: int
) -> None:
    """A malformed or oversized record is reported as not worth retrying."""
    nvdataflow_backend.post = AsyncMock(return_value=status)

    with pytest.raises(ArchiveRejectedError):
        await archive_main._archive_nvdataflow(dict(WORKFLOW_DETAILS))


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 500, 503])
async def test_nvdataflow_other_failures_are_retryable(
    nvdataflow_backend: MagicMock, status: int
) -> None:
    """Outages and auth problems raise an ordinary error so the message is retried."""
    nvdataflow_backend.post = AsyncMock(return_value=status)

    with pytest.raises(RuntimeError, match=str(status)):
        await archive_main._archive_nvdataflow(dict(WORKFLOW_DETAILS))
