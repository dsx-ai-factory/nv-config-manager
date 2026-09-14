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
"""Tests for the configuration-independent ticketing provider registry."""

from unittest.mock import MagicMock, patch

import pytest

from nv_config_manager_workflows.clients import (
    TICKETING_PROVIDERS,
    JiraTicketingProvider,
    TicketingProvider,
    get_ticketing_provider,
)

JIRA_SETTINGS = {
    "base_url": "https://jira.example.com",
    "api_token": "secret-token",
}


def test_builtin_registry_resolves_jira_provider() -> None:
    assert TICKETING_PROVIDERS["jira"] is JiraTicketingProvider
    assert issubclass(TICKETING_PROVIDERS["jira"], TicketingProvider)


def test_registry_constructs_jira_from_explicit_settings() -> None:
    expected = MagicMock(spec=TicketingProvider)

    with patch.object(
        JiraTicketingProvider,
        "from_settings",
        return_value=expected,
    ) as from_settings:
        provider = get_ticketing_provider("jira", JIRA_SETTINGS)

    from_settings.assert_called_once_with(JIRA_SETTINGS)
    assert provider is expected


def test_unknown_platform_error_lists_registered_providers() -> None:
    with pytest.raises(ValueError) as error:
        get_ticketing_provider("unknown-platform", {})

    assert "unknown-platform" in str(error.value)
    assert "jira" in str(error.value)


def test_provider_names_remain_case_sensitive() -> None:
    with pytest.raises(ValueError, match="Jira"):
        get_ticketing_provider("Jira", JIRA_SETTINGS)
