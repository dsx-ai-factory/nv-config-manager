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
from nv_config_manager_workflows.registration.builtin import BUILTIN_PLUGIN_NAME
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
from nv_config_manager_workflows.ui import WorkflowFormContractError, build_form

_MAX_DIAGNOSTIC_LENGTH = 1000


@dataclass(frozen=True)
class PluginInfo:
    """What one discovered plugin contributed, for startup diagnostics."""

    name: str
    version: str
    workflow_count: int
    activity_count: int
    scheduler_count: int


@dataclass(frozen=True, slots=True)
class SchedulerRegistration:
    """One validated scheduler together with its immutable plugin provenance."""

    plugin: str
    identity: str
    scheduler: type[WorkflowScheduler]


@dataclass(frozen=True, slots=True)
class WorkflowFormDiagnostic:
    """Why a third-party workflow's form is unavailable; its workflow stays registered."""

    plugin: str
    workflow: str
    message: str


@dataclass
class WorkflowRegistry:
    """Built-in and plugin workflows merged into one validated catalog."""

    all_workflows: list[type[WorkflowMetadataMixin]] = field(default_factory=list)
    all_activities: list[Callable[..., Any]] = field(default_factory=list)
    all_schedulers: list[type[WorkflowScheduler]] = field(default_factory=list)
    scheduler_registrations: tuple[SchedulerRegistration, ...] = ()
    api_workflows: list[type[WorkflowMetadataMixin]] = field(default_factory=list)
    mcp_workflows: list[type[WorkflowMetadataMixin]] = field(default_factory=list)
    plugin_diagnostics: list[PluginInfo] = field(default_factory=list)
    workflow_owners: dict[type[WorkflowMetadataMixin], str] = field(default_factory=dict)
    forms: dict[type[WorkflowMetadataMixin], dict[str, Any]] = field(default_factory=dict)
    form_diagnostics: dict[type[WorkflowMetadataMixin], WorkflowFormDiagnostic] = field(
        default_factory=dict
    )

    def owner(self, workflow: type[WorkflowMetadataMixin]) -> str:
        """Return the plugin that canonically owns a workflow: its first contributor."""
        return self.workflow_owners[workflow]

    @classmethod
    def build(cls, plugins: Mapping[str, WorkflowPluginDescriptor] | None = None) -> Self:
        """Load built-in and installed plugins, validate them, return the registry.

        Args:
            plugins: Descriptors keyed by plugin name. Defaults to whatever
                :func:`discover_workflow_plugins` finds installed; pass an
                explicit mapping to build a registry from known descriptors.

        Returns:
            A registry whose lists are empty on a clean install with no
            populated plugins, ordered with the built-in plugin first, then by
            plugin name, and then by the order each descriptor declares.

        Every API workflow's ``/form`` envelope is built and validated here. A
        third-party plugin's invalid form is recorded in ``form_diagnostics``
        and leaves its workflow registered.

        Raises:
            WorkflowRegistrationError: Discovery or validation rejected the
                installed set; see the subclasses in ``errors`` for which.
            WorkflowFormContractError: A built-in workflow declares an invalid form.
        """
        discovered = discover_workflow_plugins() if plugins is None else dict(plugins)
        ordered = dict(
            sorted(discovered.items(), key=lambda item: (item[0] != BUILTIN_PLUGIN_NAME, item[0]))
        )
        validate_plugins(ordered)

        all_workflows = [
            cast(type[WorkflowMetadataMixin], workflow)
            for workflow in dict.fromkeys(
                workflow for descriptor in ordered.values() for workflow in descriptor.workflows
            )
        ]
        workflow_owners: dict[type[WorkflowMetadataMixin], str] = {}
        for plugin_name, descriptor in ordered.items():
            for workflow in descriptor.workflows:
                workflow_owners.setdefault(cast(type[WorkflowMetadataMixin], workflow), plugin_name)
        api_workflows = [w for w in all_workflows if workflow_api_enabled(w)]
        forms, form_diagnostics = _build_forms(api_workflows, workflow_owners)
        all_activities = list(dict.fromkeys(a for d in ordered.values() for a in d.activities))
        scheduler_registrations = tuple(
            SchedulerRegistration(
                plugin=plugin_name,
                identity=scheduler.scheduler_identity,
                scheduler=scheduler,
            )
            for plugin_name, descriptor in ordered.items()
            for scheduler in descriptor.schedulers
        )
        all_schedulers = [registration.scheduler for registration in scheduler_registrations]

        return cls(
            all_workflows=all_workflows,
            all_activities=all_activities,
            all_schedulers=all_schedulers,
            scheduler_registrations=scheduler_registrations,
            api_workflows=api_workflows,
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
            workflow_owners=workflow_owners,
            forms=forms,
            form_diagnostics=form_diagnostics,
        )


def _build_forms(
    workflows: list[type[WorkflowMetadataMixin]],
    owners: Mapping[type[WorkflowMetadataMixin], str],
) -> tuple[
    dict[type[WorkflowMetadataMixin], dict[str, Any]],
    dict[type[WorkflowMetadataMixin], WorkflowFormDiagnostic],
]:
    """Build every API workflow's form; isolate a third-party plugin's invalid form."""
    forms: dict[type[WorkflowMetadataMixin], dict[str, Any]] = {}
    diagnostics: dict[type[WorkflowMetadataMixin], WorkflowFormDiagnostic] = {}
    for workflow in workflows:
        owner = owners[workflow]
        try:
            forms[workflow] = build_form(workflow.get_workflow_input_class())
        except WorkflowFormContractError as error:
            if owner == BUILTIN_PLUGIN_NAME:
                raise
            diagnostics[workflow] = WorkflowFormDiagnostic(
                plugin=owner, workflow=workflow.__name__, message=_sanitize(str(error))
            )
    return forms, diagnostics


def _sanitize(message: str) -> str:
    """Return a single-line, printable, bounded diagnostic message."""
    printable = "".join(c if c.isprintable() else " " for c in message)
    collapsed = " ".join(printable.split())
    if len(collapsed) > _MAX_DIAGNOSTIC_LENGTH:
        return collapsed[: _MAX_DIAGNOSTIC_LENGTH - 3] + "..."
    return collapsed
