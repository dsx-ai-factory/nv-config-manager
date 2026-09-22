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
"""Process-local dependencies used by reusable workflow activities."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Protocol

from nv_config_manager_infrastructure.lock import TokenLockBackend
from temporalio.exceptions import ApplicationError


class RuntimeConfigurationError(ApplicationError):
    """Non-retryable failure caused by missing process runtime configuration."""

    def __init__(self, message: str) -> None:
        """Initialize a permanent activity configuration failure."""
        super().__init__(message, type=self.__class__.__name__, non_retryable=True)


class NatsNotConfiguredError(RuntimeConfigurationError):
    """Raised when NATS publishing is used without an available provider."""


class SlackNotConfiguredError(RuntimeConfigurationError):
    """Raised when Slack configuration is read before startup configured it."""


class UIBaseURLNotConfiguredError(RuntimeConfigurationError):
    """Raised when the NVCM UI base URL is unavailable to an activity."""


class LockNotConfiguredError(RuntimeConfigurationError):
    """Raised when workflow locking is used without an available backend."""


class NatsPublisher(Protocol):
    """Narrow publishing capability required by the NATS archive activity."""

    server: str

    async def publish(self, subject: str, message: str, stream: str | None = None) -> None:
        """Publish ``message`` to a NATS JetStream subject."""


class LockBackend(Protocol):
    """Distributed lock operations required by workflow lock activities."""

    async def acquire(
        self,
        name: str,
        token: str,
        *,
        timeout: int,
        blocking_timeout: float | None = None,
        blocking: bool = True,
    ) -> bool:
        """Acquire or refresh the lock identified by ``name``."""

    async def renew(self, name: str, token: str, *, timeout: int) -> bool:
        """Renew a lock held by ``token``."""

    async def release(self, name: str, token: str) -> bool:
        """Release a lock held by ``token``."""


@dataclass(frozen=True, slots=True)
class NatsRuntime:
    """NATS publisher and routing values used by the archive activity."""

    publisher: NatsPublisher
    stream: str
    subject: str


@dataclass(frozen=True, slots=True)
class SlackRuntime:
    """Slack credentials and default destination used by notification activities."""

    token: str
    channel: str


type NatsRuntimeProvider = Callable[[], NatsRuntime | None]
type SlackRuntimeProvider = Callable[[], SlackRuntime | None]
type UIBaseURLProvider = Callable[[], str | None]
type LockBackendProvider = Callable[[], LockBackend]


class _Unset:
    """Distinguish omitted startup configuration from an intentionally disabled resource."""


_UNSET: Final = _Unset()
_NOOP_LOCK_BACKEND: Final[LockBackend] = TokenLockBackend(None)

_nats_provider: NatsRuntimeProvider | None | _Unset = _UNSET
_slack_provider: SlackRuntimeProvider | None | _Unset = _UNSET
_ui_base_url_provider: UIBaseURLProvider | None | _Unset = _UNSET
_lock_backend_provider: LockBackendProvider | _Unset = _UNSET


def configure_nats(provider: NatsRuntimeProvider | None) -> None:
    """Configure NATS publishing, or explicitly disable it with ``None``."""
    global _nats_provider  # noqa: PLW0603
    _nats_provider = provider


def configure_slack(provider: SlackRuntimeProvider | None) -> None:
    """Configure Slack notifications, or explicitly disable them with ``None``."""
    global _slack_provider  # noqa: PLW0603
    _slack_provider = provider


def configure_ui_base_url(provider: UIBaseURLProvider | None) -> None:
    """Configure the NVCM UI URL, or explicitly disable it with ``None``."""
    global _ui_base_url_provider  # noqa: PLW0603
    _ui_base_url_provider = provider


def _noop_lock_backend() -> LockBackend:
    """Return the stable infrastructure-owned no-op lock backend."""
    return _NOOP_LOCK_BACKEND


def configure_lock_backend(provider: LockBackendProvider | None) -> None:
    """Configure workflow locking, or explicitly use no-op locking with ``None``."""
    global _lock_backend_provider  # noqa: PLW0603
    _lock_backend_provider = _noop_lock_backend if provider is None else provider


def get_nats_runtime() -> NatsRuntime:
    """Return current NATS publishing dependencies or raise a named error."""
    provider = _nats_provider
    if isinstance(provider, _Unset):
        raise NatsNotConfiguredError(
            "NATS runtime is not configured. Call configure_nats(provider) or "
            "configure_runtime() at worker startup."
        )
    if provider is None:
        raise NatsNotConfiguredError("NATS runtime is disabled")

    runtime = provider()
    if runtime is None or not runtime.stream:
        raise NatsNotConfiguredError("NATS runtime is disabled or incomplete")
    return runtime


def get_slack_runtime() -> SlackRuntime | None:
    """Return current Slack settings, including an intentional disabled state."""
    provider = _slack_provider
    if isinstance(provider, _Unset):
        raise SlackNotConfiguredError(
            "Slack runtime is not configured. Call configure_slack(provider) or "
            "configure_runtime() at worker startup."
        )
    if provider is None:
        return None
    return provider()


def get_ui_base_url() -> str:
    """Return the current NVCM UI base URL or raise a named error."""
    provider = _ui_base_url_provider
    if isinstance(provider, _Unset):
        raise UIBaseURLNotConfiguredError(
            "UI base URL is not configured. Call configure_ui_base_url(provider) or "
            "configure_runtime() at worker startup."
        )
    if provider is None:
        raise UIBaseURLNotConfiguredError("UI base URL is disabled")

    url = provider()
    if not url:
        raise UIBaseURLNotConfiguredError("UI base URL is disabled")
    return url


def get_lock_backend() -> LockBackend:
    """Return the current workflow lock backend or raise a named error."""
    provider = _lock_backend_provider
    if isinstance(provider, _Unset):
        raise LockNotConfiguredError(
            "Workflow lock backend is not configured. Call configure_lock_backend(provider) or "
            "configure_runtime() at worker startup."
        )
    return provider()


def configure_runtime(
    *,
    nats_provider: NatsRuntimeProvider | None,
    slack_provider: SlackRuntimeProvider | None,
    ui_base_url_provider: UIBaseURLProvider | None,
    lock_backend_provider: LockBackendProvider | None,
) -> None:
    """Apply every currently supported workflow activity dependency."""
    configure_nats(nats_provider)
    configure_slack(slack_provider)
    configure_ui_base_url(ui_base_url_provider)
    configure_lock_backend(lock_backend_provider)
