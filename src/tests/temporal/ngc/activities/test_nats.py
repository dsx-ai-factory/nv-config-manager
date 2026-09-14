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

from unittest.mock import AsyncMock, patch

import nats.js.errors
import pytest

from nv_config_manager.temporal.ngc.activities.nats import (
    ARCHIVE_SUBJECT,
    PublishNatsInput,
    publish_nats,
)
from nv_config_manager_workflows import runtime as runtime_module
from nv_config_manager_workflows.mixins.archive import PUBLISH_NATS_ACTIVITY_NAME
from nv_config_manager_workflows.registration import activity_name
from nv_config_manager_workflows.runtime import NatsNotConfiguredError


@pytest.mark.asyncio
async def test_publish_nats_fails_clearly_before_runtime_configuration(monkeypatch):
    """A missing startup call is reported before constructing a NATS client."""
    monkeypatch.setattr(runtime_module, "_nats", runtime_module._UNSET)

    with pytest.raises(NatsNotConfiguredError, match="configure_nats"):
        await publish_nats(PublishNatsInput(message="payload"))


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.ngc.activities.nats.NatsProducer")
async def test_publish_nats_publishes(mock_producer_cls):
    """Publish is called with the given subject and message."""
    mock_client = AsyncMock()
    mock_producer_cls.return_value = mock_client

    await publish_nats(PublishNatsInput(subject=ARCHIVE_SUBJECT, message='{"workflow_id": "w1"}'))

    mock_client.publish.assert_called_once_with(
        ARCHIVE_SUBJECT, '{"workflow_id": "w1"}', stream="nv-config-manager"
    )


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.ngc.activities.nats.NatsProducer")
async def test_publish_nats_any_subject_publishes(mock_producer_cls):
    """Any subject is published."""
    mock_client = AsyncMock()
    mock_producer_cls.return_value = mock_client

    await publish_nats(PublishNatsInput(subject="other.subject", message="payload"))

    mock_client.publish.assert_called_once_with(
        "other.subject", "payload", stream="nv-config-manager"
    )


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.ngc.activities.nats.NatsProducer")
async def test_publish_nats_on_failure_raises(mock_producer_cls):
    """When publish raises a NATS error, the activity re-raises for visibility."""
    mock_client = AsyncMock()
    mock_client.publish.side_effect = nats.js.errors.NoStreamResponseError()
    mock_producer_cls.return_value = mock_client

    with pytest.raises(nats.js.errors.NoStreamResponseError):
        await publish_nats(PublishNatsInput(subject=ARCHIVE_SUBJECT, message="payload"))


def test_registered_under_the_name_archive_mixin_dispatches_on():
    """ArchiveMixin executes this activity by name, so a rename here breaks archival."""
    assert activity_name(publish_nats) == PUBLISH_NATS_ACTIVITY_NAME
