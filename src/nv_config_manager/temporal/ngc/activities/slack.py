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
"""Slack activities."""

from pydantic import BaseModel
from slack_sdk import WebClient
from temporalio import activity

from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager_workflows.runtime import (
    get_slack_configuration,
    get_ui_base_url,
)

logger = get_logger(__name__, category=LogCategory.TEMPORAL_ACTIVITY)


class SlackMessageInput(BaseModel):
    """Slack message input."""

    message: str
    thread_ts: str | None = None
    link_workflow: bool = False


class SlackMessageOutput(BaseModel):
    """Slack message output."""

    thread_ts: str | None = None


@activity.defn
async def send_slack_message(input: SlackMessageInput) -> SlackMessageOutput:
    """Send a message to Slack. No-op when Slack is not configured."""
    configured_token, configured_channel = get_slack_configuration()
    bot_token = (configured_token or "").strip()
    channel_name = (configured_channel or "").strip()
    if not bot_token or not channel_name:
        logger.info("Slack is not configured; skipping notification.")
        return SlackMessageOutput()

    client = WebClient(token=bot_token)
    channel = f"#{channel_name}"

    message = input.message
    if input.link_workflow:
        ui_url = get_ui_base_url().rstrip("/")
        workflow_id = activity.info().workflow_id
        message += f"\nView workflow: {ui_url}/workflows/{workflow_id}"

    result = client.chat_postMessage(channel=channel, text=message, thread_ts=input.thread_ts)
    return SlackMessageOutput(thread_ts=result["ts"])
