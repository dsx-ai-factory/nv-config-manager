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
"""Compatibility adapter for the relocated Jira ticketing provider."""

from __future__ import annotations

from nv_config_manager.temporal.factories.ticketing import ticketing_client_settings
from nv_config_manager_workflows.clients.ticketing import (
    JiraClientError,
    JiraSettings,
)
from nv_config_manager_workflows.clients.ticketing import (
    JiraTicketingProvider as WorkflowJiraTicketingProvider,
)


class JiraTicketingProvider(WorkflowJiraTicketingProvider):
    """Preserve configuration-backed construction at the legacy import path."""

    @classmethod
    def from_config(cls) -> JiraTicketingProvider:
        """Construct Jira using the service-owned INI settings adapter."""
        return cls(**ticketing_client_settings(platform="jira"))


__all__ = ["JiraClientError", "JiraSettings", "JiraTicketingProvider"]
