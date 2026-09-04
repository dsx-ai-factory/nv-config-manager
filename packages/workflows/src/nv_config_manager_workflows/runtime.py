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
"""Process-local runtime configuration for reusable workflow activities."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Final

from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.lock import configure_lock_backend

if TYPE_CHECKING:
    from redis.asyncio import Redis


class RuntimeConfigurationError(ApplicationError):
    """Non-retryable failure caused by missing process startup configuration."""

    def __init__(self, message: str) -> None:
        """Initialize a permanent activity configuration failure."""
        super().__init__(message, non_retryable=True)


class NatsNotConfiguredError(RuntimeConfigurationError):
    """Raised when NATS configuration is read before startup configured it."""


class SlackNotConfiguredError(RuntimeConfigurationError):
    """Raised when Slack configuration is read before startup configured it."""


class UIBaseURLNotConfiguredError(RuntimeConfigurationError):
    """Raised when the UI base URL is unavailable to a workflow activity."""


class _Unset:
    """Distinguish an omitted startup call from an intentionally disabled resource."""


_UNSET: Final = _Unset()

type NatsConfiguration = tuple[str | None, str | None]
type NatsConfigurationProvider = Callable[[], NatsConfiguration]
type SlackConfiguration = tuple[str | None, str | None]
type SlackConfigurationProvider = Callable[[], SlackConfiguration]
type UIBaseURLProvider = Callable[[], str | None]

_nats: NatsConfigurationProvider | None | _Unset = _UNSET
_slack: SlackConfigurationProvider | None | _Unset = _UNSET
_ui_base_url: UIBaseURLProvider | None | _Unset = _UNSET


def configure_nats(provider: NatsConfigurationProvider | None) -> None:
    """Configure a NATS provider, or disable NATS when ``None``."""
    global _nats
    _nats = provider


def configure_slack(provider: SlackConfigurationProvider | None) -> None:
    """Configure a Slack provider, or disable Slack when ``None``."""
    global _slack
    _slack = provider


def configure_ui_base_url(provider: UIBaseURLProvider | None) -> None:
    """Configure an NVCM UI URL provider, or disable links when ``None``."""
    global _ui_base_url
    _ui_base_url = provider


def get_nats_configuration() -> tuple[str, str]:
    """Return configured NATS archive settings or fail with a startup hint."""
    configured = _nats
    if isinstance(configured, _Unset):
        raise NatsNotConfiguredError(
            "NATS runtime is not configured. Call configure_nats(provider) "
            "or configure_runtime() at application startup."
        )
    if configured is None:
        raise NatsNotConfiguredError("NATS runtime is disabled")
    stream, subject = configured()
    if stream is None or subject is None:
        raise NatsNotConfiguredError("NATS runtime is disabled or incomplete")
    return stream, subject


def get_slack_configuration() -> tuple[str | None, str | None]:
    """Return configured Slack settings, including an intentional disabled state."""
    configured = _slack
    if isinstance(configured, _Unset):
        raise SlackNotConfiguredError(
            "Slack runtime is not configured. Call configure_slack(provider) "
            "or configure_runtime() at application startup."
        )
    if configured is None:
        return None, None
    return configured()


def get_ui_base_url() -> str:
    """Return the configured NVCM UI base URL or a clear configuration error."""
    configured = _ui_base_url
    if isinstance(configured, _Unset):
        raise UIBaseURLNotConfiguredError(
            "UI base URL is not configured. Call configure_ui_base_url(provider) "
            "or configure_runtime() at application startup."
        )
    if configured is None:
        raise UIBaseURLNotConfiguredError("UI base URL is disabled")
    url = configured()
    if url is None:
        raise UIBaseURLNotConfiguredError("UI base URL is disabled")
    return url


def configure_runtime(
    *,
    lock_redis: Redis | None = None,
    nats_provider: NatsConfigurationProvider | None = None,
    slack_provider: SlackConfigurationProvider | None = None,
    ui_base_url_provider: UIBaseURLProvider | None = None,
) -> None:
    """Apply every process-local workflow resource configuration in one call."""
    configure_lock_backend(lock_redis)
    configure_nats(nats_provider)
    configure_slack(slack_provider)
    configure_ui_base_url(ui_base_url_provider)


__all__ = [
    "NatsNotConfiguredError",
    "NatsConfigurationProvider",
    "RuntimeConfigurationError",
    "SlackConfigurationProvider",
    "SlackNotConfiguredError",
    "UIBaseURLProvider",
    "UIBaseURLNotConfiguredError",
    "configure_nats",
    "configure_runtime",
    "configure_slack",
    "configure_ui_base_url",
    "get_nats_configuration",
    "get_slack_configuration",
    "get_ui_base_url",
]
