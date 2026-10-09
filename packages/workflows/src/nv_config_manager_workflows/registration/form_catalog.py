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
"""Browser-form catalog built separately from the workflow execution registry."""

from dataclasses import dataclass, field
from typing import Any, Self

from nv_config_manager_workflows.metadata import WorkflowMetadataMixin
from nv_config_manager_workflows.registration.builtin import BUILTIN_PLUGIN_NAME
from nv_config_manager_workflows.registration.contract import workflow_form_enabled
from nv_config_manager_workflows.registration.registry import WorkflowRegistry
from nv_config_manager_workflows.ui import WorkflowFormContractError, build_form

_MAX_DIAGNOSTIC_LENGTH = 1000


@dataclass(frozen=True, slots=True)
class WorkflowFormDiagnostic:
    """Why a third-party workflow's form is unavailable; its workflow stays registered."""

    plugin: str
    workflow: str
    message: str


@dataclass
class WorkflowFormCatalog:
    """Validated browser forms for one workflow-registry snapshot."""

    forms: dict[type[WorkflowMetadataMixin], dict[str, Any]] = field(default_factory=dict)
    diagnostics: dict[type[WorkflowMetadataMixin], WorkflowFormDiagnostic] = field(
        default_factory=dict
    )

    @classmethod
    def build(cls, registry: WorkflowRegistry) -> Self:
        """Build enabled API forms without changing the execution registry.

        Invalid third-party forms become diagnostics and leave execution
        registration intact. Invalid built-in forms remain fatal for the API
        process and CI form validation.
        """
        forms: dict[type[WorkflowMetadataMixin], dict[str, Any]] = {}
        diagnostics: dict[type[WorkflowMetadataMixin], WorkflowFormDiagnostic] = {}
        for workflow in registry.api_workflows:
            if not workflow_form_enabled(workflow):
                continue
            owner = registry.owner(workflow)
            try:
                forms[workflow] = build_form(workflow.get_workflow_input_class())
            except WorkflowFormContractError as error:
                if owner == BUILTIN_PLUGIN_NAME:
                    raise
                diagnostics[workflow] = WorkflowFormDiagnostic(
                    plugin=owner,
                    workflow=workflow.__name__,
                    message=_sanitize(str(error)),
                )
        return cls(forms=forms, diagnostics=diagnostics)


def _sanitize(message: str) -> str:
    """Return a single-line, printable, bounded diagnostic message."""
    printable = "".join(c if c.isprintable() else " " for c in message)
    collapsed = " ".join(printable.split())
    if len(collapsed) > _MAX_DIAGNOSTIC_LENGTH:
        return collapsed[: _MAX_DIAGNOSTIC_LENGTH - 3] + "..."
    return collapsed
