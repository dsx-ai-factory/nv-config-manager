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
"""Test NATS activities."""

from unittest.mock import AsyncMock

import nats.js.errors
import pytest
from pytest_mock import MockerFixture

from nv_config_manager.temporal.ngc.activities.nats import (
    ARCHIVE_SUBJECT,
    PublishNatsInput,
    publish_nats,
)
from nv_config_manager_workflows import runtime as runtime_module
from nv_config_manager_workflows.runtime import (
    NatsNotConfiguredError,
    NatsRuntime,
    configure_nats,
)


@pytest.fixture
def nats_publisher(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Configure an isolated NATS publisher for each activity test."""
    monkeypatch.setattr(runtime_module, "_nats_provider", runtime_module._UNSET)
    publisher = AsyncMock()
    publisher.server = "nats://nats.example.test:4222"
    configure_nats(
        lambda: NatsRuntime(
            publisher=publisher,
            stream="nv-config-manager",
            subject=ARCHIVE_SUBJECT,
        )
    )
    return publisher


@pytest.mark.asyncio
async def test_publish_nats_fails_clearly_before_runtime_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unconfigured worker reports the missing NATS startup dependency."""
    monkeypatch.setattr(runtime_module, "_nats_provider", runtime_module._UNSET)

    with pytest.raises(NatsNotConfiguredError, match="configure_nats"):
        await publish_nats(PublishNatsInput(message="payload"))


@pytest.mark.asyncio
async def test_publish_nats_publishes(nats_publisher: AsyncMock) -> None:
    """Publish is called with the given subject and message."""
    await publish_nats(PublishNatsInput(subject=ARCHIVE_SUBJECT, message='{"workflow_id": "w1"}'))

    nats_publisher.publish.assert_awaited_once_with(
        ARCHIVE_SUBJECT, '{"workflow_id": "w1"}', stream="nv-config-manager"
    )


@pytest.mark.asyncio
async def test_publish_nats_uses_configured_subject(nats_publisher: AsyncMock) -> None:
    """The configured subject is used when the activity input omits one."""
    await publish_nats(PublishNatsInput(message="payload"))

    nats_publisher.publish.assert_awaited_once_with(
        ARCHIVE_SUBJECT, "payload", stream="nv-config-manager"
    )


@pytest.mark.asyncio
async def test_publish_nats_any_subject_publishes(nats_publisher: AsyncMock) -> None:
    """Any subject is published."""
    await publish_nats(PublishNatsInput(subject="other.subject", message="payload"))

    nats_publisher.publish.assert_awaited_once_with(
        "other.subject", "payload", stream="nv-config-manager"
    )


@pytest.mark.asyncio
async def test_publish_nats_input_subject_overrides_blank_default(
    nats_publisher: AsyncMock,
) -> None:
    """An explicit activity subject remains usable without a configured default."""
    configure_nats(
        lambda: NatsRuntime(
            publisher=nats_publisher,
            stream="nv-config-manager",
            subject="",
        )
    )

    await publish_nats(PublishNatsInput(subject="other.subject", message="payload"))

    nats_publisher.publish.assert_awaited_once_with(
        "other.subject", "payload", stream="nv-config-manager"
    )


@pytest.mark.asyncio
async def test_publish_nats_requires_an_effective_subject(nats_publisher: AsyncMock) -> None:
    """A missing configured and input subject raises the named runtime error."""
    configure_nats(
        lambda: NatsRuntime(
            publisher=nats_publisher,
            stream="nv-config-manager",
            subject="",
        )
    )

    with pytest.raises(NatsNotConfiguredError, match="subject"):
        await publish_nats(PublishNatsInput(message="payload"))

    nats_publisher.publish.assert_not_called()


@pytest.mark.asyncio
async def test_publish_nats_on_failure_redacts_server_and_raises(
    nats_publisher: AsyncMock,
    mocker: MockerFixture,
) -> None:
    """A publish failure logs a safe endpoint and re-raises for visibility."""
    error = nats.js.errors.NoStreamResponseError()
    nats_publisher.server = "tls://alice:secret@nats.example.test:4222?token=secret"
    nats_publisher.publish.side_effect = error
    log_error = mocker.patch("nv_config_manager.temporal.ngc.activities.nats.logger.error")

    with pytest.raises(nats.js.errors.NoStreamResponseError):
        await publish_nats(PublishNatsInput(subject=ARCHIVE_SUBJECT, message="payload"))

    log_error.assert_called_once_with(
        "NATS publish failed: subject=%s server=%s error=%s",
        ARCHIVE_SUBJECT,
        "tls://nats.example.test:4222",
        error,
        exc_info=True,
    )
