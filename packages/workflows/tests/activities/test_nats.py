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
"""Tests for the reusable NATS publish activity."""

from unittest.mock import AsyncMock, MagicMock

import nats.errors
import nats.js.errors
import pytest

from nv_config_manager_workflows import runtime as runtime_module
from nv_config_manager_workflows.activities.nats import (
    ARCHIVE_SUBJECT,
    PublishNatsInput,
    publish_nats,
)
from nv_config_manager_workflows.activities.nats import activities as nats_activities
from nv_config_manager_workflows.runtime import (
    NatsNotConfiguredError,
    NatsRuntime,
    configure_nats,
)


@pytest.fixture
def nats_publisher() -> AsyncMock:
    """Configure an isolated NATS publisher for each activity test."""
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


async def test_publish_nats_fails_clearly_before_runtime_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unconfigured worker reports the missing NATS startup dependency."""
    monkeypatch.setattr(runtime_module, "_nats_provider", runtime_module._UNSET)

    with pytest.raises(NatsNotConfiguredError, match="configure_nats") as raised:
        await publish_nats(PublishNatsInput(message="payload"))

    assert raised.value.non_retryable is True


async def test_publish_nats_uses_explicit_subject(nats_publisher: AsyncMock) -> None:
    """An activity input subject overrides the configured default."""
    await publish_nats(PublishNatsInput(subject="other.subject", message="payload"))

    nats_publisher.publish.assert_awaited_once_with(
        "other.subject",
        "payload",
        stream="nv-config-manager",
    )


async def test_publish_nats_uses_configured_subject(nats_publisher: AsyncMock) -> None:
    """The configured default subject is used when the input omits one."""
    await publish_nats(PublishNatsInput(message="payload"))

    nats_publisher.publish.assert_awaited_once_with(
        ARCHIVE_SUBJECT,
        "payload",
        stream="nv-config-manager",
    )


async def test_publish_nats_input_subject_overrides_blank_default(
    nats_publisher: AsyncMock,
) -> None:
    """An explicit input remains usable without a configured default subject."""
    configure_nats(lambda: NatsRuntime(nats_publisher, "nv-config-manager", ""))

    await publish_nats(PublishNatsInput(subject="other.subject", message="payload"))

    nats_publisher.publish.assert_awaited_once_with(
        "other.subject",
        "payload",
        stream="nv-config-manager",
    )


async def test_publish_nats_requires_an_effective_subject(nats_publisher: AsyncMock) -> None:
    """A blank effective subject fails before any external publish call."""
    configure_nats(lambda: NatsRuntime(nats_publisher, "nv-config-manager", ""))

    with pytest.raises(NatsNotConfiguredError, match="subject") as raised:
        await publish_nats(PublishNatsInput(message="payload"))

    assert raised.value.non_retryable is True
    nats_publisher.publish.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        nats.errors.ConnectionClosedError(),
        nats.js.errors.NoStreamResponseError(),
    ],
)
async def test_publish_nats_redacts_server_and_reraises_supported_errors(
    nats_publisher: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    """Core and JetStream failures log no credentials and retain their identity."""
    nats_publisher.server = "tls://alice:secret@nats.example.test:4222?token=secret"
    nats_publisher.publish.side_effect = error
    log_error = MagicMock()
    monkeypatch.setattr(nats_activities.logger, "error", log_error)

    with pytest.raises(type(error)) as raised:
        await publish_nats(PublishNatsInput(subject=ARCHIVE_SUBJECT, message="payload"))

    assert raised.value is error
    log_error.assert_called_once_with(
        "NATS publish failed: subject=%s server=%s error=%s",
        ARCHIVE_SUBJECT,
        "tls://nats.example.test:4222",
        error,
        exc_info=True,
    )
