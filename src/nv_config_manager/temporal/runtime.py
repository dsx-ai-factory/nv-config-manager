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
"""Translate service INI configuration into workflow package runtime state."""

from __future__ import annotations

from redis.asyncio import Redis

from nv_config_manager.common.config_loader import load_config
from nv_config_manager.common.http_config import nats_archive_config
from nv_config_manager_workflows.runtime import configure_runtime


def _nats_configuration() -> tuple[str | None, str | None]:
    """Read the current archive stream and subject from the service configuration."""
    config = load_config()
    if config.has_section("nats"):
        return nats_archive_config(config)
    return None, None


def _slack_configuration() -> tuple[str | None, str | None]:
    """Read the current Slack token and channel from the service configuration."""
    config = load_config()
    return (
        config.get("slack", "bot_token", fallback=None),
        config.get("slack", "channel_name", fallback=None),
    )


def _ui_base_url() -> str | None:
    """Read the current NVCM UI base URL from the service configuration."""
    return load_config().get("temporal", "ui_url", fallback=None)


def configure_workflow_runtime(*, lock_redis: Redis | None) -> None:
    """Configure workflow resources with providers backed by the hot-reloaded INI."""

    configure_runtime(
        lock_redis=lock_redis,
        nats_provider=_nats_configuration,
        slack_provider=_slack_configuration,
        ui_base_url_provider=_ui_base_url,
    )


__all__ = ["configure_workflow_runtime"]
