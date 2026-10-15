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
"""Workflow registry snapshot and the workflow catalogs the Temporal API serves."""

from collections.abc import Sequence
from typing import Any

from nv_config_manager.common.log import LogCategory, escape_log_newlines, get_logger
from nv_config_manager.temporal.api.form_option_endpoints import (
    build_workflow_form_surface,
)
from nv_config_manager.temporal.common.mixins.metadata import WorkflowMetadataMixin
from nv_config_manager.temporal.common.rbac_config import RBACConfig
from nv_config_manager.temporal.workflow_registry import build_workflow_registry
from nv_config_manager_workflows.registration.form_catalog import WorkflowFormCatalog

logger = get_logger(__name__, category=LogCategory.TEMPORAL_WORKFLOW)

# Dynamic routes are constructed while the API module is imported. Build the
# registry once so every API catalog surface uses the same validated snapshot.
WORKFLOW_REGISTRY = build_workflow_registry()
WORKFLOW_FORM_SURFACE = build_workflow_form_surface(WorkflowFormCatalog.build(WORKFLOW_REGISTRY))
WORKFLOW_FORM_CATALOG = WORKFLOW_FORM_SURFACE.catalog
WORKFLOW_FORM_OPTION_ROUTER = WORKFLOW_FORM_SURFACE.router
# API-enabled workflows: dynamic POST routes and /metadata.
WORKFLOW_API_CATALOG = tuple(WORKFLOW_REGISTRY.api_workflows)
# Every registered workflow, including API-disabled child workflows: /types.
WORKFLOW_TYPE_CATALOG = tuple(WORKFLOW_REGISTRY.all_workflows)


def build_workflow_metadata(workflows: Sequence[type]) -> dict[str, dict[str, Any]]:
    """Build API metadata, including RBAC roles, for the supplied workflows."""
    workflows_info: dict[str, dict[str, Any]] = {}
    rbac_config = RBACConfig()

    for workflow_class in workflows:
        if not issubclass(workflow_class, WorkflowMetadataMixin):
            continue

        metadata_workflow = workflow_class
        if not metadata_workflow.has_complete_metadata():
            continue

        input_class = metadata_workflow.get_workflow_input_class()
        workflow_roles = rbac_config.get_workflow_roles(workflow_class.__name__)
        workflows_info[workflow_class.__name__] = {
            "name": workflow_class.__name__,
            "display_name": metadata_workflow.get_workflow_name(),
            "endpoint": metadata_workflow.get_workflow_api_endpoint(),
            "input_class": input_class.__name__ if input_class else "Unknown",
            "description": metadata_workflow.get_workflow_description(),
            "namespace": metadata_workflow.get_workflow_namespace(),
            "cli_name": metadata_workflow.get_workflow_cli_name(),
            "group": metadata_workflow.get_workflow_group(),
            "read_roles": sorted(workflow_roles["read_roles"] if workflow_roles else []),
            "execute_roles": sorted(workflow_roles["execute_roles"] if workflow_roles else []),
        }

    return workflows_info


def log_workflow_form_diagnostics(catalog: WorkflowFormCatalog) -> None:
    """Log sanitized form failures and non-fatal provider declaration warnings."""
    for diagnostic in catalog.diagnostics.values():
        logger.warning(
            "Workflow form unavailable for %s from plugin %s: %s",
            escape_log_newlines(diagnostic.workflow),
            escape_log_newlines(diagnostic.plugin),
            escape_log_newlines(diagnostic.message),
            extra={
                "event_type": "workflow_form_unavailable",
                "plugin": escape_log_newlines(diagnostic.plugin),
                "workflow": escape_log_newlines(diagnostic.workflow),
            },
        )
    for warning in catalog.warnings:
        logger.warning(
            "Workflow form warning for %s from plugin %s: %s",
            escape_log_newlines(warning.workflow),
            escape_log_newlines(warning.plugin),
            escape_log_newlines(warning.message),
            extra={
                "event_type": "workflow_form_warning",
                "plugin": escape_log_newlines(warning.plugin),
                "workflow": escape_log_newlines(warning.workflow),
            },
        )


__all__ = [
    "WORKFLOW_API_CATALOG",
    "WORKFLOW_FORM_CATALOG",
    "WORKFLOW_FORM_OPTION_ROUTER",
    "WORKFLOW_FORM_SURFACE",
    "WORKFLOW_REGISTRY",
    "WORKFLOW_TYPE_CATALOG",
    "build_workflow_metadata",
    "log_workflow_form_diagnostics",
]
