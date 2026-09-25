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
"""Tests for configuration-independent ticketing provider resolution."""

from unittest.mock import MagicMock, patch

import pytest

from nv_config_manager_workflows.clients.ticketing import (
    TICKETING_PROVIDERS,
    JiraTicketingProvider,
    TicketingProvider,
    get_ticketing_provider,
)

JIRA_SETTINGS = {
    "base_url": "https://jira.example.com",
    "api_token": "secret-token",
}


def test_registry_constructs_provider_from_explicit_settings() -> None:
    expected = MagicMock(spec=TicketingProvider)

    with (
        patch.dict(TICKETING_PROVIDERS, {"jira": JiraTicketingProvider}, clear=True),
        patch.object(
            JiraTicketingProvider,
            "from_settings",
            return_value=expected,
        ) as from_settings,
    ):
        provider = get_ticketing_provider("jira", JIRA_SETTINGS)

    from_settings.assert_called_once_with(JIRA_SETTINGS)
    assert provider is expected


def test_unknown_platform_error_preserves_main_message_contract() -> None:
    with (
        patch.dict(TICKETING_PROVIDERS, {"jira": JiraTicketingProvider}, clear=True),
        pytest.raises(ValueError) as error,
    ):
        get_ticketing_provider("unknown-platform", {})

    assert "unknown-platform" in str(error.value)
    assert "jira" in str(error.value)


def test_provider_names_remain_case_sensitive() -> None:
    with (
        patch.dict(TICKETING_PROVIDERS, {"jira": JiraTicketingProvider}, clear=True),
        pytest.raises(ValueError, match="Jira"),
    ):
        get_ticketing_provider("Jira", JIRA_SETTINGS)
