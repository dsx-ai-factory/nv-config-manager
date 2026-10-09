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
"""Workflow registry bootstrap and startup diagnostics shared by Temporal processes."""

from nv_config_manager.common.log import LogCategory, escape_log_newlines, get_logger
from nv_config_manager_workflows.registration import (
    BUILTIN_PLUGIN_NAME,
    WORKFLOW_PLUGIN_ENTRY_POINT_GROUP,
    WorkflowPluginDiscoveryError,
    WorkflowRegistry,
    registry_manifest,
)

logger = get_logger(__name__, category=LogCategory.TEMPORAL_WORKFLOW)


def build_workflow_registry() -> WorkflowRegistry:
    """Build the installed workflow registry and require the built-in plugin.

    A missing built-in plugin means the installation is broken; fail here with
    a clear error instead of a later, misleading one (or none at all).

    Raises:
        WorkflowPluginDiscoveryError: The built-in plugin is not registered.
        WorkflowRegistrationError: Discovery or validation rejected the
            installed plugins.
    """
    registry = WorkflowRegistry.build()
    if all(plugin.name != BUILTIN_PLUGIN_NAME for plugin in registry.plugin_diagnostics):
        raise WorkflowPluginDiscoveryError(
            f'Workflow plugin "{BUILTIN_PLUGIN_NAME}" is not registered in entry-point group '
            f"{WORKFLOW_PLUGIN_ENTRY_POINT_GROUP}; the nv-config-manager-workflows "
            "installation is incomplete"
        )
    return registry


def log_workflow_registry(registry: WorkflowRegistry) -> None:
    """Log one record per plugin and one manifest summary.

    Records carry only plugin names, versions, contribution counts, scheduler
    identities, and the manifest fingerprint: never descriptor metadata,
    entry-point values, or configuration. The ``service`` field set by
    ``configure_logging()`` identifies the emitting process.
    """
    manifest = registry_manifest(registry)
    for plugin in manifest.plugins:
        logger.info(
            "Loaded workflow plugin %s version %s: %d workflows, %d activities, %d schedulers",
            plugin.name,
            plugin.version,
            plugin.workflow_count,
            plugin.activity_count,
            plugin.scheduler_count,
            extra={
                "event_type": "workflow_plugin",
                "plugin": escape_log_newlines(plugin.name),
                "plugin_version": escape_log_newlines(plugin.version),
                "workflow_count": plugin.workflow_count,
                "activity_count": plugin.activity_count,
                "scheduler_count": plugin.scheduler_count,
                "scheduler_identities": [
                    registration.identity
                    for registration in registry.scheduler_registrations
                    if registration.plugin == plugin.name
                ],
                "registry_fingerprint": manifest.fingerprint,
            },
        )
    logger.info(
        "Workflow registry manifest %s: %d plugins, %d workflows, %d activities, %d schedulers",
        manifest.fingerprint,
        len(manifest.plugins),
        len(manifest.workflows),
        len(manifest.activities),
        len(manifest.schedulers),
        extra={
            "event_type": "workflow_registry_manifest",
            "registry_fingerprint": manifest.fingerprint,
            "plugins": [
                escape_log_newlines(f"{plugin.name}=={plugin.version}")
                for plugin in manifest.plugins
            ],
            "workflow_count": len(manifest.workflows),
            "activity_count": len(manifest.activities),
            "scheduler_count": len(manifest.schedulers),
            "scheduler_identities": list(manifest.schedulers),
        },
    )
