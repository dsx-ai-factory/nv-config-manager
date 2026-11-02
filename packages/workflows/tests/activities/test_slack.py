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
"""Tests for the reusable Slack notification activity."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from nv_config_manager_workflows import runtime as runtime_module
from nv_config_manager_workflows.activities.slack import (
    SlackMessageInput,
    SlackMessageOutput,
    send_slack_message,
)
from nv_config_manager_workflows.activities.slack import activities as slack_activities
from nv_config_manager_workflows.runtime import (
    SlackNotConfiguredError,
    SlackRuntime,
    configure_slack,
    configure_ui_base_url,
)


@pytest.fixture(autouse=True)
def configured_slack_runtime() -> None:
    """Configure isolated Slack and workflow UI providers."""
    configure_slack(lambda: SlackRuntime("DUMMY", "nv-config-manager-test"))
    configure_ui_base_url(lambda: "https://temporal-ui.example.com")


@pytest.fixture
def slack_client(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    """Replace the Slack SDK client with a no-I/O mock."""
    client = MagicMock()
    client.chat_postMessage.return_value = {"ts": "1234567890.123456"}
    web_client = MagicMock(return_value=client)
    monkeypatch.setattr(slack_activities, "WebClient", web_client)
    return web_client, client


async def test_send_slack_message_noops_when_disabled(
    slack_client: tuple[MagicMock, MagicMock],
) -> None:
    """An explicitly disabled integration does not construct an SDK client."""
    web_client, _ = slack_client
    configure_slack(None)

    result = await send_slack_message(SlackMessageInput(message="hello"))

    assert result == SlackMessageOutput(thread_ts=None)
    web_client.assert_not_called()


async def test_send_slack_message_noops_when_settings_are_blank(
    slack_client: tuple[MagicMock, MagicMock],
) -> None:
    """Blank token and channel settings retain the disabled behavior."""
    web_client, _ = slack_client
    configure_slack(lambda: SlackRuntime("", ""))

    result = await send_slack_message(SlackMessageInput(message="hello"))

    assert result == SlackMessageOutput(thread_ts=None)
    web_client.assert_not_called()


async def test_send_slack_message_fails_before_runtime_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing worker startup configuration raises a permanent named failure."""
    monkeypatch.setattr(runtime_module, "_slack_provider", runtime_module._UNSET)

    with pytest.raises(SlackNotConfiguredError, match="configure_slack") as raised:
        await send_slack_message(SlackMessageInput(message="hello"))

    assert raised.value.non_retryable is True


async def test_send_slack_message_uses_channel_and_returns_thread_timestamp(
    slack_client: tuple[MagicMock, MagicMock],
) -> None:
    """Configured messages retain channel formatting and output extraction."""
    web_client, client = slack_client

    result = await send_slack_message(SlackMessageInput(message="test message"))

    web_client.assert_called_once_with(token="DUMMY")
    client.chat_postMessage.assert_called_once_with(
        channel="#nv-config-manager-test",
        text="test message",
        thread_ts=None,
    )
    assert result == SlackMessageOutput(thread_ts="1234567890.123456")


async def test_send_slack_message_passes_through_thread_timestamp(
    slack_client: tuple[MagicMock, MagicMock],
) -> None:
    """Threaded replies forward their input timestamp and return Slack's timestamp."""
    _, client = slack_client
    client.chat_postMessage.return_value = {"ts": "1234567890.999999"}

    result = await send_slack_message(
        SlackMessageInput(message="reply", thread_ts="1234567890.123456")
    )

    client.chat_postMessage.assert_called_once_with(
        channel="#nv-config-manager-test",
        text="reply",
        thread_ts="1234567890.123456",
    )
    assert result == SlackMessageOutput(thread_ts="1234567890.999999")


async def test_send_slack_message_preserves_workflow_link_content(
    slack_client: tuple[MagicMock, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workflow links retain their exact historical message representation."""
    _, client = slack_client
    configure_ui_base_url(lambda: "http://localhost:8080/")
    monkeypatch.setattr(
        slack_activities.activity,
        "info",
        lambda: SimpleNamespace(workflow_id="workflow-123"),
    )

    await send_slack_message(SlackMessageInput(message="test message", link_workflow=True))

    client.chat_postMessage.assert_called_once_with(
        channel="#nv-config-manager-test",
        text="test message\nView workflow: http://localhost:8080/workflows/workflow-123",
        thread_ts=None,
    )
