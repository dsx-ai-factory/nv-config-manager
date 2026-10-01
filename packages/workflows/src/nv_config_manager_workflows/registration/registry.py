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
"""The merged view of every installed workflow plugin."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Self, cast

from nv_config_manager_workflows.metadata import WorkflowMetadataMixin
from nv_config_manager_workflows.registration.contract import (
    workflow_api_enabled,
    workflow_mcp_enabled,
)
from nv_config_manager_workflows.registration.descriptor import (
    UNKNOWN_PLUGIN_VERSION,
    WorkflowPluginDescriptor,
)
from nv_config_manager_workflows.registration.discovery import discover_workflow_plugins
from nv_config_manager_workflows.registration.scheduler import WorkflowScheduler
from nv_config_manager_workflows.registration.validation import validate_plugins


@dataclass(frozen=True)
class PluginInfo:
    """What one discovered plugin contributed, for startup diagnostics."""

    name: str
    version: str
    workflow_count: int
    activity_count: int
    scheduler_count: int


@dataclass
class WorkflowRegistry:
    """Built-in and plugin workflows merged into one validated catalog."""

    all_workflows: list[type[WorkflowMetadataMixin]] = field(default_factory=list)
    all_activities: list[Callable[..., Any]] = field(default_factory=list)
    all_schedulers: list[type[WorkflowScheduler]] = field(default_factory=list)
    api_workflows: list[type[WorkflowMetadataMixin]] = field(default_factory=list)
    mcp_workflows: list[type[WorkflowMetadataMixin]] = field(default_factory=list)
    plugin_diagnostics: list[PluginInfo] = field(default_factory=list)

    @classmethod
    def build(cls, plugins: Mapping[str, WorkflowPluginDescriptor] | None = None) -> Self:
        """Load built-in and installed plugins, validate them, return the registry.

        Args:
            plugins: Descriptors keyed by plugin name. Defaults to whatever
                :func:`discover_workflow_plugins` finds installed; pass an
                explicit mapping to build a registry from known descriptors.

        Returns:
            A registry whose lists are empty on a clean install with no
            populated plugins, ordered by plugin name and then by the order each
            descriptor declares.

        Raises:
            WorkflowRegistrationError: Discovery or validation rejected the
                installed set; see the subclasses in ``errors`` for which.
        """
        discovered = discover_workflow_plugins() if plugins is None else dict(plugins)
        ordered = dict(sorted(discovered.items()))
        validate_plugins(ordered)

        all_workflows = [
            cast(type[WorkflowMetadataMixin], workflow)
            for workflow in dict.fromkeys(
                workflow for descriptor in ordered.values() for workflow in descriptor.workflows
            )
        ]
        all_activities = list(dict.fromkeys(a for d in ordered.values() for a in d.activities))
        all_schedulers = list(dict.fromkeys(s for d in ordered.values() for s in d.schedulers))

        return cls(
            all_workflows=all_workflows,
            all_activities=all_activities,
            all_schedulers=all_schedulers,
            api_workflows=[w for w in all_workflows if workflow_api_enabled(w)],
            mcp_workflows=[w for w in all_workflows if workflow_mcp_enabled(w)],
            plugin_diagnostics=[
                PluginInfo(
                    name=descriptor.name,
                    version=descriptor.version or UNKNOWN_PLUGIN_VERSION,
                    workflow_count=len(descriptor.workflows),
                    activity_count=len(descriptor.activities),
                    scheduler_count=len(descriptor.schedulers),
                )
                for descriptor in ordered.values()
            ],
        )
