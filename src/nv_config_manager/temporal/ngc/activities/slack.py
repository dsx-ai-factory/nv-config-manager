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
"""Deprecated service import path for the reusable Slack activity."""

# isort: off
from nv_config_manager_workflows.activities.slack import (
    SLACK_ACTIVITIES as SLACK_ACTIVITIES,
    SlackMessageInput as SlackMessageInput,
    SlackMessageOutput as SlackMessageOutput,
    send_slack_message as send_slack_message,
)
# isort: on

__all__ = [
    "SLACK_ACTIVITIES",
    "SlackMessageInput",
    "SlackMessageOutput",
    "send_slack_message",
]
