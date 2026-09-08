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
"""The checks run while the registry is built."""

import inspect
from collections.abc import Callable, Mapping, Sequence
from typing import Any, NamedTuple, cast

from pydantic import BaseModel

from nv_config_manager_workflows.metadata import WorkflowMetadataMixin
from nv_config_manager_workflows.registration.contract import (
    activity_has_definition,
    activity_is_dynamic,
    activity_name,
    declared_required_activities,
    is_activity_sequence,
    missing_metadata_attributes,
    normalized_api_endpoint,
    required_activity_name,
    workflow_api_enabled,
    workflow_api_endpoint,
    workflow_class_name,
    workflow_cli_name,
    workflow_declared_name,
    workflow_has_complete_metadata,
    workflow_has_definition,
    workflow_is_dynamic,
    workflow_mcp_enabled,
    workflow_mcp_tool_name,
    workflow_required_activity_names,
    workflow_type_name,
)
from nv_config_manager_workflows.registration.descriptor import WorkflowPluginDescriptor
from nv_config_manager_workflows.registration.errors import (
    WorkflowConflictError,
    WorkflowRegistrationError,
    WorkflowRequiredActivityError,
)
from nv_config_manager_workflows.registration.scheduler import WorkflowScheduler
from nv_config_manager_workflows.stage import StageMixin

REQUIRED_WORKFLOW_BASES = (StageMixin, WorkflowMetadataMixin)


class _Owned[ItemT](NamedTuple):
    """One workflow or activity together with the plugin that contributed it."""

    plugin: str
    item: ItemT


type _OwnedWorkflowCandidate = _Owned[type]
type _OwnedWorkflow = _Owned[type[WorkflowMetadataMixin]]
type _OwnedActivity = _Owned[Callable[..., Any]]
type _OwnedScheduler = _Owned[type[WorkflowScheduler]]


def validate_plugins(plugins: Mapping[str, WorkflowPluginDescriptor]) -> None:
    """Run every registration check over the merged set of plugin descriptors.

    Raises:
        WorkflowRegistrationError: A workflow declares metadata that is present
            but not valid, or an activity carries no Temporal definition.
        WorkflowConflictError: Two plugins contribute the same class name,
            workflow name, Temporal workflow type, activity name, API endpoint,
            CLI name or MCP tool name.
        WorkflowRequiredActivityError: A workflow requires an activity that no
            plugin supplies.
    """
    workflow_candidates: list[_OwnedWorkflowCandidate] = [
        _Owned(name, item) for name, d in plugins.items() for item in d.workflows
    ]
    activities: list[_OwnedActivity] = [
        _Owned(name, item) for name, d in plugins.items() for item in d.activities
    ]
    schedulers: list[_OwnedScheduler] = [
        _Owned(name, item) for name, d in plugins.items() for item in d.schedulers
    ]

    workflows = _require_plugin_workflow_bases(workflow_candidates)
    _require_named_workflows(workflows)
    _require_named_activities(activities)
    _require_scheduler_contracts(schedulers)
    _require_valid_metadata(workflows)

    api_enabled = [owned for owned in workflows if workflow_api_enabled(owned.item)]
    mcp_enabled = [owned for owned in workflows if workflow_mcp_enabled(owned.item)]

    _reject_duplicates(workflows, workflow_class_name, "workflow class name")
    _reject_duplicates(workflows, workflow_declared_name, "workflow name")
    _reject_duplicates(workflows, workflow_type_name, "Temporal workflow type")
    _reject_duplicates(activities, activity_name, "activity name")
    _reject_duplicates(api_enabled, normalized_api_endpoint, "workflow API endpoint")
    _reject_duplicates(api_enabled, workflow_cli_name, "workflow CLI name")
    _reject_duplicates(mcp_enabled, workflow_mcp_tool_name, "MCP tool name")
    _require_declared_activities(workflows, activities)


def validate_workflow_bases(workflows: Sequence[type]) -> None:
    """Require every workflow in a concrete runtime catalog to use the shared bases."""
    for workflow in workflows:
        name = getattr(workflow, "__qualname__", None) or repr(workflow)
        _require_workflow_bases(workflow, f'Workflow "{name}"')


def _require_valid_metadata(workflows: list[_OwnedWorkflow]) -> None:
    for owned in workflows:
        label = _label(owned, "Workflow")
        _require_text(workflow_declared_name(owned.item), "workflow_name", label)
        _require_text(
            getattr(owned.item, "workflow_description", None), "workflow_description", label
        )
        _require_input_class(owned.item, label)
        _require_bool_api_flag(owned.item, label)
        _require_bool_mcp_flag(owned.item, label)
        _require_activity_names_wellformed(owned.item, label)
        _require_endpoint_wellformed(owned.item, label)
        _require_metadata_for_exposed_surfaces(owned.item, label)
        _require_cli_name(owned.item, label)


def _require_named_workflows(workflows: list[_OwnedWorkflow]) -> None:
    """Require every workflow to declare a Temporal type of its own."""
    for owned in workflows:
        if workflow_is_dynamic(owned.item):
            raise WorkflowRegistrationError(_dynamic_rejection(owned, "Workflow", "workflow"))
        if not workflow_has_definition(owned.item):
            raise WorkflowRegistrationError(
                f"{_label(owned, 'Workflow')} is not decorated with @workflow.defn"
            )


def _require_plugin_workflow_bases(
    workflows: list[_OwnedWorkflowCandidate],
) -> list[_OwnedWorkflow]:
    """Require plugin workflows to inherit every mandatory base and narrow their type."""
    validated: list[_OwnedWorkflow] = []
    for owned in workflows:
        _require_workflow_bases(owned.item, _label(owned, "Workflow"))
        validated.append(_Owned(owned.plugin, cast(type[WorkflowMetadataMixin], owned.item)))
    return validated


def _require_workflow_bases(workflow: type, label: str) -> None:
    missing = [base.__name__ for base in REQUIRED_WORKFLOW_BASES if not issubclass(workflow, base)]
    if missing:
        raise WorkflowRegistrationError(f"{label} does not inherit {', '.join(missing)}")


def _require_named_activities(activities: list[_OwnedActivity]) -> None:
    """Require every activity to declare a Temporal name of its own."""
    for owned in activities:
        if activity_is_dynamic(owned.item):
            raise WorkflowRegistrationError(_dynamic_rejection(owned, "Activity", "activity"))
        if not activity_has_definition(owned.item):
            raise WorkflowRegistrationError(
                f"{_label(owned, 'Activity')} is not decorated with @activity.defn"
            )


def _require_scheduler_contracts(schedulers: list[_OwnedScheduler]) -> None:
    """Require schedulers to expose an asynchronous no-argument runner."""
    for owned in schedulers:
        label = _label(owned, "Scheduler")
        if inspect.isabstract(owned.item):
            raise WorkflowRegistrationError(f"{label} is abstract and cannot be constructed")

        run = getattr(owned.item, "run", None)
        if run is None:
            raise WorkflowRegistrationError(f"{label} does not declare run()")
        if not inspect.iscoroutinefunction(run):
            raise WorkflowRegistrationError(f"{label} declares run(), which is not async")

        run_descriptor = inspect.getattr_static(owned.item, "run")
        implicit_arguments = (
            () if isinstance(run_descriptor, (classmethod, staticmethod)) else (None,)
        )
        try:
            inspect.signature(run).bind(*implicit_arguments)
        except (TypeError, ValueError):
            raise WorkflowRegistrationError(
                f"{label} declares run(), which cannot be called without arguments"
            ) from None


def _dynamic_rejection[ItemT](owned: _Owned[ItemT], kind: str, decorator: str) -> str:
    """Explain why a plugin may not contribute a dynamic handler.

    Temporal dispatches every type name no registered handler claimed to the
    dynamic one, so a single plugin would silently receive work aimed at names
    the other installed plugins never registered, and none of the duplicate
    checks below can see it. The worker also permits only one, so two plugins
    each contributing one fail worker construction naming neither of them.
    """
    return (
        f"{_label(owned, kind)} is declared with @{decorator}.defn(dynamic=True); a workflow "
        f"plugin may not contribute a dynamic {decorator} because it would receive every "
        f"{decorator} the other installed plugins do not claim"
    )


def _require_input_class(workflow: type[WorkflowMetadataMixin], label: str) -> None:
    """Require workflow inputs to use a Pydantic model class."""
    input_class = getattr(workflow, "workflow_input_class", None)
    if input_class is None:
        return
    if not isinstance(input_class, type):
        raise WorkflowRegistrationError(
            f"{label} declares workflow_input_class {input_class!r}, which is not a class"
        )
    if not issubclass(input_class, BaseModel):
        raise WorkflowRegistrationError(
            f"{label} declares workflow_input_class {input_class!r}, which is not a "
            f"Pydantic BaseModel subclass"
        )


def _require_bool_api_flag(workflow: type[WorkflowMetadataMixin], label: str) -> None:
    """Reject an API opt-in that is not a bool."""
    api_enabled = getattr(workflow, "workflow_api_enabled", False)
    if not isinstance(api_enabled, bool):
        raise WorkflowRegistrationError(
            f"{label} declares workflow_api_enabled {api_enabled!r}, which is not a bool"
        )


def _require_bool_mcp_flag(workflow: type[WorkflowMetadataMixin], label: str) -> None:
    """Reject an MCP opt-in that is not a bool."""
    mcp_enabled = getattr(workflow, "workflow_mcp_enabled", False)
    if not isinstance(mcp_enabled, bool):
        raise WorkflowRegistrationError(
            f"{label} declares workflow_mcp_enabled {mcp_enabled!r}, which is not a bool"
        )


def _require_endpoint_wellformed(workflow: type[WorkflowMetadataMixin], label: str) -> None:
    """Reject an API path the router could not serve."""
    endpoint = workflow_api_endpoint(workflow)
    if endpoint is None:
        return
    _require_text(endpoint, "workflow_api_endpoint", label)
    if not endpoint.startswith("/") or any(character.isspace() for character in endpoint):
        raise WorkflowRegistrationError(
            f'{label} declares workflow_api_endpoint "{endpoint}", which must start with "/" '
            f"and contain no whitespace"
        )
    if not endpoint.strip("/"):
        raise WorkflowRegistrationError(
            f'{label} declares workflow_api_endpoint "{endpoint}", which names no path segment; '
            f"a workflow is invoked under a path of its own, not the API root"
        )


def _require_metadata_for_exposed_surfaces(
    workflow: type[WorkflowMetadataMixin], label: str
) -> None:
    """Require the full metadata set from workflows the API or MCP exposes."""

    api_enabled = workflow_api_enabled(workflow)
    mcp_enabled = workflow_mcp_enabled(workflow)
    if not api_enabled and not mcp_enabled:
        return

    if mcp_enabled and not api_enabled:
        raise WorkflowRegistrationError(f"{label} enables MCP but does not enable API")

    if workflow_has_complete_metadata(workflow):
        return

    missing = ", ".join(missing_metadata_attributes(workflow)) or "required metadata"
    if mcp_enabled:
        raise WorkflowRegistrationError(f"{label} enables MCP but is missing {missing}")
    raise WorkflowRegistrationError(f"{label} enables API but is missing {missing}")


def _require_cli_name(workflow: type[WorkflowMetadataMixin], label: str) -> None:
    """Reject a declared CLI accessor that does not return a usable name."""
    if not callable(getattr(workflow, "get_workflow_cli_name", None)):
        return
    name = workflow_cli_name(workflow)
    if not isinstance(name, str) or not name.strip():
        raise WorkflowRegistrationError(
            f"{label} returns CLI name {name!r} from get_workflow_cli_name(), "
            f"which is not a non-empty string"
        )


def _require_text(value: Any, attribute: str, label: str) -> None:
    """Reject a declared attribute that is not a non-blank string."""

    if value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise WorkflowRegistrationError(
            f"{label} declares {attribute} {value!r}, which is not a non-empty string"
        )


def _require_activity_names_wellformed(workflow: type[WorkflowMetadataMixin], label: str) -> None:
    """Reject a required-activity declaration that is not a sequence of activities."""

    declared = declared_required_activities(workflow)
    if declared is None:
        return
    if not is_activity_sequence(declared):
        raise WorkflowRegistrationError(
            f"{label} declares workflow_required_activities {declared!r}, which is not a "
            f"sequence of activity functions"
        )
    for entry in declared:
        if required_activity_name(entry) is None:
            raise WorkflowRegistrationError(
                f"{label} declares required activity {entry!r}, which is neither an activity "
                f"function nor a non-empty activity name"
            )


def _reject_duplicates[ItemT](
    owned_items: list[_Owned[ItemT]],
    key: Callable[[ItemT], str | None],
    subject: str,
) -> None:
    seen: dict[str, _Owned[ItemT]] = {}
    for owned in owned_items:
        value = key(owned.item)
        if value is None:
            continue
        previous = seen.get(value)
        if previous is None:
            seen[value] = owned
            continue
        if previous.item is owned.item:
            # The same object listed twice is a redundant declaration, not a conflict.
            continue
        raise WorkflowConflictError(
            f'Duplicate {subject} "{value}" {_contributors(previous.plugin, owned.plugin)}'
        )


def _require_declared_activities(
    workflows: list[_OwnedWorkflow], activities: list[_OwnedActivity]
) -> None:
    available = {name for owned in activities if (name := activity_name(owned.item)) is not None}
    for owned in workflows:
        for required in workflow_required_activity_names(owned.item):
            if required not in available:
                raise WorkflowRequiredActivityError(
                    f'{_label(owned, "Workflow")} requires activity "{required}", which no '
                    f"installed workflow plugin supplies"
                )


def _contributors(first: str, second: str) -> str:
    """Name the plugins on both sides of a conflict."""
    if first == second:
        return f'contributed twice by workflow plugin "{first}"'
    return f'contributed by workflow plugins "{first}" and "{second}"'


def _label[ItemT](owned: _Owned[ItemT], kind: str) -> str:
    """Describe a workflow or activity and the plugin that contributed it."""
    name = getattr(owned.item, "__qualname__", None) or repr(owned.item)
    return f'{kind} "{name}" from plugin "{owned.plugin}"'
