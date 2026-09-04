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
"""Tests for the Slack activity."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from nv_config_manager.temporal.ngc.activities import slack as slack_activity
from nv_config_manager.temporal.ngc.activities.slack import (
    SlackMessageInput,
    SlackMessageOutput,
    send_slack_message,
)
from nv_config_manager_workflows import runtime as runtime_module
from nv_config_manager_workflows.runtime import SlackNotConfiguredError


class TestSendSlackMessage:
    """Tests for the send_slack_message activity."""

    @pytest.mark.asyncio
    async def test_fails_clearly_before_runtime_configuration(self, monkeypatch):
        monkeypatch.setattr(runtime_module, "_slack", runtime_module._UNSET)

        with pytest.raises(SlackNotConfiguredError, match="configure_slack"):
            await send_slack_message(SlackMessageInput(message="hello"))

    @pytest.mark.asyncio
    async def test_noop_when_slack_unconfigured(self, custom_ini):
        """Activity returns empty output without calling Slack API."""
        custom_ini(
            """
[temporal]
ui_url = http://localhost:8080
"""
        )
        result = await send_slack_message(
            SlackMessageInput(message="hello"),
        )
        assert result == SlackMessageOutput(thread_ts=None)

    @pytest.mark.asyncio
    async def test_noop_when_slack_section_empty(self, custom_ini):
        """Activity returns empty output when [slack] keys are blank."""
        custom_ini(
            """
[slack]
bot_token =
channel_name =
"""
        )
        result = await send_slack_message(
            SlackMessageInput(message="hello"),
        )
        assert result == SlackMessageOutput(thread_ts=None)

    @pytest.mark.asyncio
    @patch("nv_config_manager.temporal.ngc.activities.slack.WebClient")
    async def test_sends_message_when_configured(self, mock_webclient_cls):
        """Activity sends a Slack message and returns thread_ts."""
        mock_client = MagicMock()
        mock_client.chat_postMessage.return_value = {"ts": "1234567890.123456"}
        mock_webclient_cls.return_value = mock_client

        result = await send_slack_message(
            SlackMessageInput(message="test message"),
        )

        mock_webclient_cls.assert_called_once_with(token="DUMMY")
        mock_client.chat_postMessage.assert_called_once_with(
            channel="#nv-config-manager-test",
            text="test message",
            thread_ts=None,
        )
        assert result.thread_ts == "1234567890.123456"

    @pytest.mark.asyncio
    @patch("nv_config_manager.temporal.ngc.activities.slack.WebClient")
    async def test_sends_message_with_thread_ts(self, mock_webclient_cls):
        """Activity passes thread_ts for threaded replies."""
        mock_client = MagicMock()
        mock_client.chat_postMessage.return_value = {"ts": "1234567890.999999"}
        mock_webclient_cls.return_value = mock_client

        result = await send_slack_message(
            SlackMessageInput(message="reply", thread_ts="1234567890.123456"),
        )

        mock_client.chat_postMessage.assert_called_once_with(
            channel="#nv-config-manager-test",
            text="reply",
            thread_ts="1234567890.123456",
        )
        assert result.thread_ts == "1234567890.999999"

    @pytest.mark.asyncio
    @patch("nv_config_manager.temporal.ngc.activities.slack.WebClient")
    async def test_workflow_link_message_is_unchanged(self, mock_webclient_cls, monkeypatch):
        """Runtime configuration does not change the Slack message wire content."""
        mock_client = MagicMock()
        mock_client.chat_postMessage.return_value = {"ts": "1234567890.123456"}
        mock_webclient_cls.return_value = mock_client
        monkeypatch.setattr(
            slack_activity.activity,
            "info",
            lambda: SimpleNamespace(workflow_id="workflow-123"),
        )

        await send_slack_message(SlackMessageInput(message="done", link_workflow=True))

        mock_client.chat_postMessage.assert_called_once_with(
            channel="#nv-config-manager-test",
            text="done\nView workflow: https://temporal-ui.example.com/workflows/workflow-123",
            thread_ts=None,
        )
