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
"""The fixture plugin's workflows."""

from datetime import timedelta

from pydantic import BaseModel
from temporalio import workflow

from nv_config_manager_workflows.decorators import run_nv_config_manager_workflow
from nv_config_manager_workflows.metadata import WorkflowMetadataMixin
from nv_config_manager_workflows.stage import StageMixin
from nvcm_fixture_plugin.activities import echo


class FixtureInput(BaseModel):
    """Input for the fixture workflows."""

    message: str


@workflow.defn
class FixtureEchoWorkflow(WorkflowMetadataMixin, StageMixin):
    """Echo a message through the fixture activity."""

    workflow_name = "Fixture Echo"
    workflow_description = "Fixture plugin workflow exposed through the API and MCP"
    workflow_input_class = FixtureInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/fixture/echo"
    workflow_namespace = "fixture"
    workflow_mcp_enabled = True
    workflow_required_activities = (echo,)

    @run_nv_config_manager_workflow
    async def run(self, workflow_input: FixtureInput) -> str:  # type: ignore[override, ty:invalid-method-override]
        """Return the activity's echo of the message."""
        return await workflow.execute_activity(
            echo,
            workflow_input.message,
            schedule_to_close_timeout=timedelta(seconds=5),
        )


@workflow.defn
class FixtureApiOnlyWorkflow(WorkflowMetadataMixin, StageMixin):
    """Return the message, exposed through the API but not MCP."""

    workflow_name = "Fixture API Only"
    workflow_description = "Fixture plugin workflow exposed through the API only"
    workflow_input_class = FixtureInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/fixture/api-only"
    workflow_namespace = "fixture"
    workflow_mcp_enabled = False

    @run_nv_config_manager_workflow
    async def run(self, workflow_input: FixtureInput) -> str:  # type: ignore[override, ty:invalid-method-override]
        """Return the message."""
        return workflow_input.message


FIXTURE_WORKFLOWS = (FixtureEchoWorkflow, FixtureApiOnlyWorkflow)

__all__ = ["FIXTURE_WORKFLOWS", "FixtureApiOnlyWorkflow", "FixtureEchoWorkflow", "FixtureInput"]
