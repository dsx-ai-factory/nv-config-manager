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
"""Translate service configuration into workflow activity dependencies."""

from __future__ import annotations

from typing import cast

from nv_config_manager.common.client import NatsProducer
from nv_config_manager.common.config import load_config, nats_archive_config
from nv_config_manager.common.lock import token_lock_backend
from nv_config_manager_workflows.runtime import (
    NatsPublisher,
    NatsRuntime,
    SlackRuntime,
    configure_runtime,
)


def _nats_runtime() -> NatsRuntime | None:
    """Build current NATS publishing dependencies from service configuration."""
    config = load_config()
    if not config.has_section("nats"):
        return None

    stream, subject = nats_archive_config(config)
    return NatsRuntime(
        publisher=cast(NatsPublisher, NatsProducer.from_config(config)),
        stream=stream,
        subject=subject,
    )


def _slack_runtime() -> SlackRuntime | None:
    """Return current Slack settings, or None when notifications are disabled."""
    config = load_config()
    token = config.get("slack", "bot_token", fallback="").strip()
    channel = config.get("slack", "channel_name", fallback="").strip()
    if not token or not channel:
        return None
    return SlackRuntime(token=token, channel=channel)


def _ui_base_url() -> str | None:
    """Return the current NVCM workflow UI base URL."""
    url = load_config().get("temporal", "ui_url", fallback="").strip()
    return url or None


def configure_workflow_runtime() -> None:
    """Install reload-aware workflow activity providers for this service."""
    configure_runtime(
        nats_provider=_nats_runtime,
        slack_provider=_slack_runtime,
        ui_base_url_provider=_ui_base_url,
        lock_backend_provider=token_lock_backend,
    )
