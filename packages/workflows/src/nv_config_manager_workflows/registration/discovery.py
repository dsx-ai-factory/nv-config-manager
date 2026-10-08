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
"""Entry-point scanning for installed workflow plugins."""

from importlib.metadata import EntryPoint, entry_points

from nv_config_manager_workflows.registration.descriptor import (
    UNKNOWN_PLUGIN_VERSION,
    WorkflowPluginDescriptor,
)
from nv_config_manager_workflows.registration.errors import (
    WorkflowPluginDiscoveryError,
    WorkflowPluginDuplicateError,
)

WORKFLOW_PLUGIN_ENTRY_POINT_GROUP = "nv_config_manager.workflows"


def discover_workflow_plugins() -> dict[str, WorkflowPluginDescriptor]:
    """Discover installed workflow plugins without reading application config.

    Returns descriptors keyed by entry-point name, ordered by that name.

    Raises:
        WorkflowPluginDuplicateError: Two entry points share a name.
        WorkflowPluginDiscoveryError: An entry point fails to load, does not
            resolve to a :class:`WorkflowPluginDescriptor`, or resolves to a
            descriptor whose ``name`` disagrees with the entry-point name.
    """
    plugins: dict[str, WorkflowPluginDescriptor] = {}
    for entry_point in _entry_points_for_group():
        if entry_point.name in plugins:
            raise WorkflowPluginDuplicateError(
                f'Workflow plugin "{entry_point.name}" is registered more than once in '
                f"entry-point group {WORKFLOW_PLUGIN_ENTRY_POINT_GROUP}; the duplicate "
                f"registration is {_describe(entry_point)}"
            )
        plugins[entry_point.name] = _load_descriptor(entry_point)
    return plugins


def _entry_points_for_group() -> tuple[EntryPoint, ...]:
    """Return the entry points in the workflow plugin group, in a stable order."""
    discovered = entry_points(group=WORKFLOW_PLUGIN_ENTRY_POINT_GROUP)
    return tuple(
        sorted(discovered, key=lambda ep: (ep.name, (ep.dist.name or "") if ep.dist else ""))
    )


def _load_descriptor(entry_point: EntryPoint) -> WorkflowPluginDescriptor:
    """Resolve one entry point to a validated descriptor."""
    try:
        loaded = entry_point.load()
        descriptor = loaded() if callable(loaded) else loaded
    except Exception as exc:  # noqa: BLE001 - any plugin failure is a discovery failure
        raise WorkflowPluginDiscoveryError(
            f'Workflow plugin "{entry_point.name}" ({entry_point.value}) failed to load: {exc}'
        ) from exc

    if not isinstance(descriptor, WorkflowPluginDescriptor):
        raise WorkflowPluginDiscoveryError(
            f'Workflow plugin "{entry_point.name}" ({entry_point.value}) does not return '
            f"WorkflowPluginDescriptor, got {type(descriptor).__name__}"
        )
    if descriptor.name != entry_point.name:
        raise WorkflowPluginDiscoveryError(
            f'Workflow plugin "{entry_point.name}" declares name "{descriptor.name}"; '
            f"the descriptor name and the entry-point name must match"
        )
    if descriptor.version is None:
        return descriptor.model_copy(update={"version": _distribution_version(entry_point)})
    return descriptor


def _distribution_version(entry_point: EntryPoint) -> str:
    """Return the installed version of the distribution registering an entry point."""
    distribution = entry_point.dist
    version = distribution.version if distribution is not None else None
    return version if isinstance(version, str) and version else UNKNOWN_PLUGIN_VERSION


def _describe(entry_point: EntryPoint) -> str:
    """Describe a duplicate registration for the error message."""
    distribution = entry_point.dist
    if distribution is None:
        return entry_point.value
    location = distribution.locate_file("")
    return (
        f"{entry_point.value} (distribution {distribution.name} "
        f"{distribution.version} at {location})"
    )
