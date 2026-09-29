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
"""Validated workflows visible to the Temporal API."""

from typing import cast

from nv_config_manager.temporal.hello_world.workflows import (
    REGISTERED_WORKFLOWS as HELLO_WORLD_WORKFLOWS,
)
from nv_config_manager.temporal.ngc.workflows import REGISTERED_WORKFLOWS as NGC_WORKFLOWS
from nv_config_manager_workflows.metadata import WorkflowMetadataMixin
from nv_config_manager_workflows.registration import validate_workflow_catalog
from nv_config_manager_workflows.registration.registry import WorkflowRegistry

_SERVICE_WORKFLOWS = cast(
    tuple[type[WorkflowMetadataMixin], ...],
    (*NGC_WORKFLOWS, *HELLO_WORLD_WORKFLOWS),
)


def build_workflow_api_catalog(
    registry: WorkflowRegistry | None = None,
) -> tuple[type[WorkflowMetadataMixin], ...]:
    """Merge service workflows with API-enabled workflows from the validated registry."""
    workflow_registry = WorkflowRegistry.build() if registry is None else registry
    all_workflows = tuple(dict.fromkeys((*_SERVICE_WORKFLOWS, *workflow_registry.all_workflows)))
    validate_workflow_catalog(
        all_workflows,
        activities=workflow_registry.all_activities,
    )
    return tuple(dict.fromkeys((*_SERVICE_WORKFLOWS, *workflow_registry.api_workflows)))


# Dynamic routes are constructed while the API module is imported. Build the
# registry once so every API catalog surface uses the same validated snapshot.
WORKFLOW_API_CATALOG = build_workflow_api_catalog()

__all__ = ["WORKFLOW_API_CATALOG", "build_workflow_api_catalog"]
