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
"""Tests for process-local diagnostics workflow runtime dependencies."""

from typing import cast

import pytest

from nv_config_manager_workflows.clients.ticketing.base import TicketingProvider
from nv_config_manager_workflows.runtime import (
    APIBaseURLNotConfiguredError,
    RedisCache,
    RedisNotConfiguredError,
    TicketingNotConfiguredError,
    configure_api_base_url,
    configure_redis_client,
    configure_ticketing,
    get_api_base_url,
    get_redis_client,
    get_ticketing_provider,
)


def test_unconfigured_diagnostics_runtime_raises_named_errors(
    unconfigured_workflow_runtime: None,
) -> None:
    """Omitted startup wiring is distinct from intentional disablement."""
    with pytest.raises(RedisNotConfiguredError, match="configure_redis_client"):
        get_redis_client()
    with pytest.raises(TicketingNotConfiguredError, match="configure_ticketing"):
        get_ticketing_provider("jira")
    with pytest.raises(APIBaseURLNotConfiguredError, match="configure_api_base_url"):
        get_api_base_url()


def test_disabled_and_incomplete_diagnostics_runtime_raises_named_errors() -> None:
    """Disabled and incomplete provider results produce permanent named failures."""
    configure_redis_client(None)
    configure_ticketing(None)
    configure_api_base_url(None)
    with pytest.raises(RedisNotConfiguredError, match="disabled$"):
        get_redis_client()
    with pytest.raises(TicketingNotConfiguredError, match="disabled$"):
        get_ticketing_provider("jira")
    with pytest.raises(APIBaseURLNotConfiguredError, match="disabled$"):
        get_api_base_url()

    configure_redis_client(lambda: None)
    configure_ticketing(lambda _platform: None)
    configure_api_base_url(lambda: None)
    with pytest.raises(RedisNotConfiguredError, match="disabled or incomplete"):
        get_redis_client()
    with pytest.raises(TicketingNotConfiguredError, match="disabled or incomplete"):
        get_ticketing_provider("jira")
    with pytest.raises(APIBaseURLNotConfiguredError, match="disabled or incomplete"):
        get_api_base_url()


def test_diagnostics_runtime_providers_are_replaceable_and_receive_platform() -> None:
    """Every getter reads the latest provider and preserves explicit arguments."""
    redis_first = cast(RedisCache, object())
    redis_second = cast(RedisCache, object())
    ticket_first = cast(TicketingProvider, object())
    ticket_second = cast(TicketingProvider, object())
    platforms: list[str] = []

    def first_ticket_provider(platform: str) -> TicketingProvider:
        platforms.append(platform)
        return ticket_first

    def second_ticket_provider(platform: str) -> TicketingProvider:
        platforms.append(platform)
        return ticket_second

    configure_redis_client(lambda: redis_first)
    configure_ticketing(first_ticket_provider)
    configure_api_base_url(lambda: "")
    assert get_redis_client() is redis_first
    assert get_ticketing_provider("jira") is ticket_first
    assert get_api_base_url() == ""

    configure_redis_client(lambda: redis_second)
    configure_ticketing(second_ticket_provider)
    configure_api_base_url(lambda: "https://api.example.test")
    assert get_redis_client() is redis_second
    assert get_ticketing_provider("custom") is ticket_second
    assert get_api_base_url() == "https://api.example.test"
    assert platforms == ["jira", "custom"]
