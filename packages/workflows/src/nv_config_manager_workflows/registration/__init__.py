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
"""Discovery and registration of workflow plugins."""

from nv_config_manager_workflows.registration.builtin import (
    BUILTIN_PLUGIN_NAME,
    builtin_plugin,
)
from nv_config_manager_workflows.registration.contract import (
    METADATA_ATTRIBUTES,
    activity_name,
    mcp_tool_name_for_endpoint,
    workflow_api_enabled,
    workflow_api_endpoint,
    workflow_class_name,
    workflow_cli_name,
    workflow_declared_name,
    workflow_has_complete_metadata,
    workflow_mcp_enabled,
    workflow_mcp_tool_name,
    workflow_required_activity_names,
    workflow_type_name,
)
from nv_config_manager_workflows.registration.descriptor import WorkflowPluginDescriptor
from nv_config_manager_workflows.registration.discovery import (
    WORKFLOW_PLUGIN_ENTRY_POINT_GROUP,
    discover_workflow_plugins,
)
from nv_config_manager_workflows.registration.errors import (
    WorkflowConflictError,
    WorkflowPluginDiscoveryError,
    WorkflowPluginDuplicateError,
    WorkflowRegistrationError,
    WorkflowRequiredActivityError,
)
from nv_config_manager_workflows.registration.registry import PluginInfo, WorkflowRegistry
from nv_config_manager_workflows.registration.scheduler import WorkflowScheduler
from nv_config_manager_workflows.registration.validation import (
    REQUIRED_WORKFLOW_BASES,
    validate_workflow_bases,
)

__all__ = [
    "BUILTIN_PLUGIN_NAME",
    "METADATA_ATTRIBUTES",
    "WORKFLOW_PLUGIN_ENTRY_POINT_GROUP",
    "PluginInfo",
    "REQUIRED_WORKFLOW_BASES",
    "WorkflowConflictError",
    "WorkflowPluginDescriptor",
    "WorkflowPluginDiscoveryError",
    "WorkflowPluginDuplicateError",
    "WorkflowRegistrationError",
    "WorkflowRegistry",
    "WorkflowRequiredActivityError",
    "WorkflowScheduler",
    "activity_name",
    "builtin_plugin",
    "discover_workflow_plugins",
    "mcp_tool_name_for_endpoint",
    "workflow_api_enabled",
    "workflow_api_endpoint",
    "workflow_class_name",
    "workflow_cli_name",
    "workflow_declared_name",
    "workflow_has_complete_metadata",
    "workflow_mcp_enabled",
    "workflow_mcp_tool_name",
    "workflow_required_activity_names",
    "workflow_type_name",
    "validate_workflow_bases",
]
