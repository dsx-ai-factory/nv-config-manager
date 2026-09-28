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

from unittest.mock import MagicMock, patch

import pytest

from nv_config_manager.temporal.ngc.activities.slack import (
    SlackMessageInput,
    SlackMessageOutput,
    send_slack_message,
)
from nv_config_manager_workflows import runtime as runtime_module
from nv_config_manager_workflows.runtime import (
    SlackNotConfiguredError,
    SlackRuntime,
    configure_slack,
    configure_ui_base_url,
)


@pytest.fixture(autouse=True)
def configured_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure isolated default Slack and NVCM UI providers."""
    monkeypatch.setattr(runtime_module, "_slack_provider", runtime_module._UNSET)
    monkeypatch.setattr(runtime_module, "_ui_base_url_provider", runtime_module._UNSET)
    configure_slack(lambda: SlackRuntime("DUMMY", "nv-config-manager-test"))
    configure_ui_base_url(lambda: "https://temporal-ui.example.com")


class TestSendSlackMessage:
    """Tests for the send_slack_message activity."""

    @pytest.mark.asyncio
    @patch("nv_config_manager.temporal.ngc.activities.slack.WebClient")
    async def test_noop_when_slack_disabled(self, mock_webclient_cls: MagicMock) -> None:
        """Activity returns empty output when Slack is explicitly disabled."""
        configure_slack(None)

        result = await send_slack_message(SlackMessageInput(message="hello"))

        assert result == SlackMessageOutput(thread_ts=None)
        mock_webclient_cls.assert_not_called()

    @pytest.mark.asyncio
    @patch("nv_config_manager.temporal.ngc.activities.slack.WebClient")
    async def test_noop_when_slack_settings_are_empty(self, mock_webclient_cls: MagicMock) -> None:
        """Activity returns empty output when configured Slack values are blank."""
        configure_slack(lambda: SlackRuntime("", ""))

        result = await send_slack_message(SlackMessageInput(message="hello"))

        assert result == SlackMessageOutput(thread_ts=None)
        mock_webclient_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_unconfigured_slack_fails_clearly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An unconfigured worker reports its missing Slack startup dependency."""
        monkeypatch.setattr(runtime_module, "_slack_provider", runtime_module._UNSET)

        with pytest.raises(SlackNotConfiguredError, match="configure_slack"):
            await send_slack_message(SlackMessageInput(message="hello"))

    @pytest.mark.asyncio
    @patch("nv_config_manager.temporal.ngc.activities.slack.WebClient")
    async def test_sends_message_when_configured(self, mock_webclient_cls: MagicMock) -> None:
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
    async def test_sends_message_with_thread_ts(self, mock_webclient_cls: MagicMock) -> None:
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
    @patch("nv_config_manager.temporal.ngc.activities.slack.activity.info")
    @patch("nv_config_manager.temporal.ngc.activities.slack.WebClient")
    async def test_workflow_link_content_is_unchanged(
        self, mock_webclient_cls: MagicMock, mock_info: MagicMock
    ) -> None:
        """Workflow links retain their exact historical message representation."""
        configure_ui_base_url(lambda: "http://localhost:8080/")
        mock_info.return_value.workflow_id = "workflow-123"
        mock_client = MagicMock()
        mock_client.chat_postMessage.return_value = {"ts": "1234567890.123456"}
        mock_webclient_cls.return_value = mock_client

        await send_slack_message(
            SlackMessageInput(message="test message", link_workflow=True),
        )

        mock_client.chat_postMessage.assert_called_once_with(
            channel="#nv-config-manager-test",
            text="test message\nView workflow: http://localhost:8080/workflows/workflow-123",
            thread_ts=None,
        )
