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

import asyncio
import base64
import gzip
import json
from configparser import ConfigParser
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from elastic_transport import ApiResponseMeta, HttpHeaders, NodeConfig
from elasticsearch import ApiError, BadRequestError
from nv_config_manager_infrastructure.nats.admin import (
    ARCHIVE_CONSUMER_MAX_DELIVER,
    CONSUMER_ACK_WAIT_SECONDS,
)

from nv_config_manager.temporal.archive import main as archive_main
from nv_config_manager.temporal.archive.main import (
    ARCHIVE_RETRY_DELAY_SECONDS,
    ARCHIVE_TIMEOUT_SECONDS,
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
        await handle_archive_msg(msg, ARCHIVE_CONSUMER_MAX_DELIVER)

    assert _settled(msg) == ["term"]


@pytest.mark.asyncio
async def test_lower_durable_limit_gives_up_at_that_limit() -> None:
    """JetStream enforces the durable's limit, so the handler gives up at that one."""
    msg = _msg(num_delivered=5)
    with patch.object(
        archive_main,
        "_archive_workflow",
        new_callable=AsyncMock,
        side_effect=RuntimeError("connection timeout"),
    ):
        await handle_archive_msg(msg, 5)

    assert _settled(msg) == ["term"]


@pytest.mark.asyncio
async def test_unbounded_durable_never_gives_up() -> None:
    """A durable with max_deliver=-1 keeps retrying the record."""
    msg = _msg(num_delivered=ARCHIVE_CONSUMER_MAX_DELIVER * 10)
    with patch.object(
        archive_main,
        "_archive_workflow",
        new_callable=AsyncMock,
        side_effect=RuntimeError("connection timeout"),
    ):
        await handle_archive_msg(msg, -1)

    assert _settled(msg) == ["nak"]


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


def test_archive_deadline_is_inside_the_ack_wait() -> None:
    """An abandoned archive is settled before JetStream would redeliver it."""
    assert ARCHIVE_TIMEOUT_SECONDS < CONSUMER_ACK_WAIT_SECONDS


@pytest.mark.asyncio
async def test_archive_past_its_deadline_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hung Temporal or backend call is abandoned and retried later."""
    monkeypatch.setattr(archive_main, "ARCHIVE_TIMEOUT_SECONDS", 0.01)
    msg = _msg()

    async def hang(_data: dict) -> None:
        await asyncio.sleep(10)

    with patch.object(archive_main, "_archive_workflow", side_effect=hang):
        await handle_archive_msg(msg)

    msg.nak.assert_awaited_once_with(delay=ARCHIVE_RETRY_DELAY_SECONDS)


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", [{"start_time": None}, {"start_time": "yesterday"}, {"started_by": ...}]
)
async def test_nvdataflow_malformed_record_is_rejected(
    nvdataflow_backend: MagicMock, change: dict
) -> None:
    """A workflow missing or mistyping a field never reaches NVDataflow and is not retried."""
    nvdataflow_backend.post = AsyncMock(return_value=201)
    details = {**WORKFLOW_DETAILS, **change}
    details = {key: value for key, value in details.items() if value is not ...}

    with pytest.raises(ArchiveRejectedError, match="malformed"):
        await archive_main._archive_nvdataflow(details)
    nvdataflow_backend.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_nvdataflow_missing_server_setting_is_retried(
    nvdataflow_backend: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deployment missing its Temporal URL is not mistaken for a malformed record."""
    nvdataflow_backend.post = AsyncMock(return_value=201)
    config = ConfigParser()
    config.read_string("[temporal]\n[temporal.nvdataflow]\nproject = archive\n")
    monkeypatch.setattr(archive_main, "config", config)

    with pytest.raises(KeyError, match="api_url"):
        await archive_main._archive_nvdataflow(dict(WORKFLOW_DETAILS))


@pytest.mark.asyncio
async def test_elasticsearch_event_without_publish_time_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An event missing the field the Elasticsearch record needs is not retried."""
    monkeypatch.setattr(archive_main, "ARCHIVE_BACKEND", "elasticsearch")
    monkeypatch.setattr(archive_main, "get_client", AsyncMock(return_value=MagicMock()))
    response = MagicMock()
    response.model_dump.return_value = dict(WORKFLOW_DETAILS)
    monkeypatch.setattr(
        archive_main.WorkflowDetailResponse, "from_handle", AsyncMock(return_value=response)
    )
    write = MagicMock()
    monkeypatch.setattr(archive_main, "_archive_elasticsearch", write)

    with pytest.raises(ArchiveRejectedError, match="malformed"):
        await archive_main._archive_workflow({"workflow_id": "wf-1"})
    write.assert_not_called()


def _api_error(cls: type[ApiError], status: int, error: str) -> ApiError:
    meta = ApiResponseMeta(
        status=status,
        http_version="1.1",
        headers=HttpHeaders(),
        duration=0.0,
        node=NodeConfig("https", "localhost", 9200),
    )
    return cls(error, meta=meta, body={"error": {"type": error}})


@pytest.fixture
def elasticsearch_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Run _archive_elasticsearch against a fake client with the index already present."""
    config = ConfigParser()
    config.read_string(
        "[temporal]\napi_url = https://temporal.example\n"
        "[temporal.elasticsearch]\nlocal = true\nserver = https://localhost:9200\n"
        "user = archive\npassword = placeholder\n"
    )
    client = MagicMock()
    client.indices.exists.return_value = True
    monkeypatch.setattr(archive_main, "config", config)
    monkeypatch.setattr(archive_main, "Elasticsearch", MagicMock(return_value=client))
    return client


@pytest.mark.parametrize("status", [400, 413])
def test_elasticsearch_rejection_is_permanent(elasticsearch_client: MagicMock, status: int) -> None:
    """A document Elasticsearch refuses, such as a mapping conflict, is not retried."""
    elasticsearch_client.index.side_effect = _api_error(
        ApiError, status, "mapper_parsing_exception"
    )

    with pytest.raises(ArchiveRejectedError, match=str(status)):
        archive_main._archive_elasticsearch(dict(WORKFLOW_DETAILS))


@pytest.mark.parametrize("status", [401, 429, 503])
def test_elasticsearch_other_failures_are_retryable(
    elasticsearch_client: MagicMock, status: int
) -> None:
    """Outages, throttling and auth problems raise an ordinary error so the message is retried."""
    elasticsearch_client.index.side_effect = _api_error(ApiError, status, "unavailable")

    with pytest.raises(ApiError):
        archive_main._archive_elasticsearch(dict(WORKFLOW_DETAILS))


def test_elasticsearch_retry_overwrites_the_same_run(elasticsearch_client: MagicMock) -> None:
    """A retry after an abandoned write replaces that run's document instead of duplicating it."""
    archive_main._archive_elasticsearch(dict(WORKFLOW_DETAILS))
    archive_main._archive_elasticsearch(dict(WORKFLOW_DETAILS))
    rerun = {**WORKFLOW_DETAILS, "start_time": "2026-10-12T00:00:00+00:00"}
    archive_main._archive_elasticsearch(rerun)

    ids = [call.kwargs["id"] for call in elasticsearch_client.index.call_args_list]
    assert ids[0] == ids[1] == "wf-1@2026-10-11T00:00:00+00:00"
    assert ids[2] != ids[0]


def test_index_created_by_a_concurrent_archive_is_used(elasticsearch_client: MagicMock) -> None:
    """Two in-flight archives of one workflow type can both find the index missing."""
    elasticsearch_client.indices.exists.return_value = False
    elasticsearch_client.indices.create.side_effect = _api_error(
        BadRequestError, 400, "resource_already_exists_exception"
    )

    archive_main._archive_elasticsearch(dict(WORKFLOW_DETAILS))

    elasticsearch_client.index.assert_called_once()


def test_other_index_creation_failures_are_retryable(elasticsearch_client: MagicMock) -> None:
    """A failure to create the index is not mistaken for a rejected record."""
    elasticsearch_client.indices.exists.return_value = False
    elasticsearch_client.indices.create.side_effect = _api_error(
        BadRequestError, 400, "illegal_argument_exception"
    )

    with pytest.raises(BadRequestError):
        archive_main._archive_elasticsearch(dict(WORKFLOW_DETAILS))
    elasticsearch_client.index.assert_not_called()
