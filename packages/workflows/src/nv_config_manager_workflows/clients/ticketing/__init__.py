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
"""Ticketing provider interfaces, registry, and built-in implementations."""

from nv_config_manager_workflows.clients.ticketing.base import (
    TicketingProvider,
    TicketingSettings,
)
from nv_config_manager_workflows.clients.ticketing.jira import (
    JiraClientError,
    JiraSettings,
    JiraTicketingProvider,
)
from nv_config_manager_workflows.clients.ticketing.registry import (
    TICKETING_PROVIDERS,
    get_ticketing_provider,
)

__all__ = [
    "JiraClientError",
    "JiraSettings",
    "JiraTicketingProvider",
    "TICKETING_PROVIDERS",
    "TicketingProvider",
    "TicketingSettings",
    "get_ticketing_provider",
]
