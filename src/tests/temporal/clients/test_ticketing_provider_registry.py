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
"""Tests for the service adapter over package-owned ticketing providers."""

from unittest.mock import MagicMock, patch

import pytest
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from nv_config_manager.temporal.client.jira import (
        JiraTicketingProvider as ServiceJiraTicketingProvider,
    )
    from nv_config_manager.temporal.client.ticketing import (
        TICKETING_PROVIDERS,
        TicketingProvider,
        get_ticketing_provider,
    )
    from nv_config_manager_workflows.clients.ticketing import (
        JiraTicketingProvider as PackageJiraTicketingProvider,
    )


# =============================================================================
# TICKETING_PROVIDERS registry
# =============================================================================


def test_registry_contains_jira():
    """The workflows package registers its Jira provider."""
    assert "jira" in TICKETING_PROVIDERS


def test_registry_jira_maps_to_jira_provider_class():
    """Importing the service shim does not replace the package provider."""
    assert TICKETING_PROVIDERS["jira"] is PackageJiraTicketingProvider
    assert TICKETING_PROVIDERS["jira"] is not ServiceJiraTicketingProvider


def test_registry_values_are_ticketing_provider_subclasses():
    """Every registered class is a subclass of TicketingProvider."""
    for name, cls in TICKETING_PROVIDERS.items():
        assert issubclass(cls, TicketingProvider), f"{name!r} is not a TicketingProvider subclass"


# =============================================================================
# get_ticketing_provider
# =============================================================================


def test_get_ticketing_provider_raises_for_unknown_platform():
    """ValueError is raised for a platform name not in the registry."""
    with pytest.raises(ValueError, match="unknown_platform"):
        get_ticketing_provider("unknown_platform")


def test_get_ticketing_provider_error_message_lists_known_platforms():
    """The ValueError message includes the list of registered platforms."""
    with pytest.raises(ValueError) as exc_info:
        get_ticketing_provider("unknown_platform")

    assert "jira" in str(exc_info.value)


def test_get_ticketing_provider_delegates_with_service_settings():
    """The service adapter resolves settings and delegates package construction."""
    settings = {"base_url": "https://jira.example.com", "api_token": "secret-token"}
    mock_instance = MagicMock()
    with (
        patch(
            "nv_config_manager.temporal.client.ticketing.ticketing_client_settings",
            return_value=settings,
        ) as client_settings,
        patch(
            "nv_config_manager.temporal.client.ticketing._get_ticketing_provider",
            return_value=mock_instance,
        ) as package_factory,
    ):
        result = get_ticketing_provider("jira")

    client_settings.assert_called_once_with(platform="jira")
    package_factory.assert_called_once_with("jira", settings)
    assert result is mock_instance


def test_get_ticketing_provider_returns_package_provider_after_service_shim_import():
    """Provider identity remains package-owned regardless of service shim import."""
    settings = {"base_url": "https://jira.example.com", "api_token": "secret-token"}
    with patch(
        "nv_config_manager.temporal.client.ticketing.ticketing_client_settings",
        return_value=settings,
    ):
        result = get_ticketing_provider("jira")

    assert type(result) is PackageJiraTicketingProvider
    assert isinstance(result, TicketingProvider)


def test_get_ticketing_provider_case_sensitive():
    """Platform names are case-sensitive — 'Jira' is not the same as 'jira'."""
    with pytest.raises(ValueError):
        get_ticketing_provider("Jira")
