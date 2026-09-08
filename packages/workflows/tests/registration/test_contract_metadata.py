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

from typing import Any

import pytest
from pydantic import BaseModel
from temporalio import activity, workflow

from nv_config_manager_workflows.metadata import WorkflowMetadataMixin
from nv_config_manager_workflows.registration.contract import (
    METADATA_ATTRIBUTES,
    declared_required_activities,
    is_activity_sequence,
    mcp_tool_name_for_endpoint,
    missing_metadata_attributes,
    normalized_api_endpoint,
    workflow_api_enabled,
    workflow_api_endpoint,
    workflow_class_name,
    workflow_cli_name,
    workflow_declared_name,
    workflow_has_complete_metadata,
    workflow_mcp_enabled,
    workflow_mcp_tool_name,
    workflow_required_activity_names,
)
from nv_config_manager_workflows.registration.errors import WorkflowRegistrationError
from nv_config_manager_workflows.stage import StageMixin


class GoldenConfigInput(BaseModel):
    device: str


@activity.defn
async def collect_facts() -> None: ...


@activity.defn(name="apply_config")
async def apply_configuration() -> None: ...


async def undecorated_activity() -> None: ...


@workflow.defn
class FullyDeclaredWorkflow(WorkflowMetadataMixin, StageMixin):
    workflow_name = "Apply Golden Config"
    workflow_description = "Applies the golden configuration to one device"
    workflow_input_class = GoldenConfigInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/config/apply-golden-config"
    workflow_mcp_enabled = True
    workflow_required_activities = (collect_facts, apply_configuration)

    @workflow.run
    async def run(self, workflow_input: BaseModel) -> None: ...

    @classmethod
    def get_workflow_cli_name(cls) -> str:
        return "apply-golden-config"


@workflow.defn
class BareWorkflow(WorkflowMetadataMixin, StageMixin):
    """Declares nothing: registrable by the worker, exposed nowhere else."""

    @workflow.run
    async def run(self, workflow_input: BaseModel) -> None: ...


class TrailingSlashWorkflow(WorkflowMetadataMixin, StageMixin):
    workflow_api_endpoint = "/config/apply-golden-config/"


class RootEndpointWorkflow(WorkflowMetadataMixin, StageMixin):
    workflow_api_endpoint = "/"
    workflow_mcp_enabled = True


class NestedEndpointWorkflow(WorkflowMetadataMixin, StageMixin):
    workflow_api_endpoint = "/config/v2/apply-golden-config"
    workflow_mcp_enabled = True


class DisabledMcpWorkflow(WorkflowMetadataMixin, StageMixin):
    workflow_api_endpoint = "/config/apply-golden-config"
    workflow_mcp_enabled = False


class FailingCliNameWorkflow(WorkflowMetadataMixin, StageMixin):
    @classmethod
    def get_workflow_cli_name(cls) -> str:
        raise RuntimeError("the plugin looked up a name it does not have")


class DiagnosingCliNameWorkflow(WorkflowMetadataMixin, StageMixin):
    @classmethod
    def get_workflow_cli_name(cls) -> str:
        raise WorkflowRegistrationError("the plugin's own diagnosis")


class PartiallyDeclaredWorkflow(WorkflowMetadataMixin, StageMixin):
    workflow_description = "Declares a description and nothing else"


class AccessorActivitiesWorkflow(WorkflowMetadataMixin, StageMixin):
    workflow_required_activities = (collect_facts,)

    @classmethod
    def get_workflow_required_activities(cls) -> tuple[Any, ...]:
        return (apply_configuration,)


class TestDeclaredNames:
    def test_class_name_is_the_python_name(self) -> None:
        assert workflow_class_name(FullyDeclaredWorkflow) == "FullyDeclaredWorkflow"

    def test_declared_name_is_the_human_readable_one(self) -> None:
        assert workflow_declared_name(FullyDeclaredWorkflow) == "Apply Golden Config"

    def test_undeclared_name_reads_as_none(self) -> None:
        assert workflow_declared_name(BareWorkflow) is None


class TestApiEndpoint:
    def test_declared_endpoint_is_returned_verbatim(self) -> None:
        assert workflow_api_endpoint(FullyDeclaredWorkflow) == "/config/apply-golden-config"

    def test_undeclared_endpoint_reads_as_none(self) -> None:
        assert workflow_api_endpoint(BareWorkflow) is None
        assert normalized_api_endpoint(BareWorkflow) is None

    def test_a_trailing_slash_does_not_make_a_second_endpoint(self) -> None:
        assert normalized_api_endpoint(TrailingSlashWorkflow) == normalized_api_endpoint(
            FullyDeclaredWorkflow
        )

    def test_the_root_endpoint_normalizes_to_no_path(self) -> None:
        """It is not an endpoint a workflow may claim, and validation rejects it."""
        assert normalized_api_endpoint(RootEndpointWorkflow) == ""


class TestApiExposure:
    def test_api_exposure_is_opt_in(self) -> None:
        assert not workflow_api_enabled(BareWorkflow)
        assert workflow_api_enabled(FullyDeclaredWorkflow)


class TestCliName:
    def test_the_base_accessor_provides_a_cli_name(self) -> None:
        assert workflow_cli_name(BareWorkflow) == "bare"

    def test_declared_cli_name_is_returned(self) -> None:
        assert workflow_cli_name(FullyDeclaredWorkflow) == "apply-golden-config"

    def test_a_failing_accessor_is_reported_as_a_registration_failure(self) -> None:
        with pytest.raises(WorkflowRegistrationError) as raised:
            workflow_cli_name(FailingCliNameWorkflow)

        assert "get_workflow_cli_name()" in str(raised.value)
        assert isinstance(raised.value.__cause__, RuntimeError)

    def test_a_registration_error_from_the_plugin_is_not_rewritten(self) -> None:
        with pytest.raises(WorkflowRegistrationError, match="the plugin's own diagnosis"):
            workflow_cli_name(DiagnosingCliNameWorkflow)


class TestMcpExposure:
    def test_mcp_is_opt_in(self) -> None:
        assert not workflow_mcp_enabled(BareWorkflow)
        assert workflow_mcp_enabled(FullyDeclaredWorkflow)

    def test_tool_name_is_derived_from_the_endpoint(self) -> None:
        assert workflow_mcp_tool_name(FullyDeclaredWorkflow) == "run_apply_golden_config"

    def test_a_workflow_that_did_not_opt_in_has_no_tool_name(self) -> None:
        assert workflow_mcp_tool_name(DisabledMcpWorkflow) is None

    def test_an_endpointless_workflow_has_no_tool_name(self) -> None:
        assert workflow_mcp_tool_name(BareWorkflow) is None

    def test_the_root_endpoint_yields_no_tool_name(self) -> None:
        assert workflow_mcp_tool_name(RootEndpointWorkflow) is None

    def test_only_the_last_path_segment_names_the_tool(self) -> None:
        """Two API versions of one workflow therefore collide, which validation catches."""
        assert workflow_mcp_tool_name(NestedEndpointWorkflow) == "run_apply_golden_config"

    @pytest.mark.parametrize(
        ("endpoint", "expected"),
        [
            ("/backup", "run_backup"),
            ("/config/apply-golden-config", "run_apply_golden_config"),
            ("/config/backup/", "run_backup"),
        ],
    )
    def test_tool_names_are_derived_from_a_path_alone(self, endpoint: str, expected: str) -> None:
        assert mcp_tool_name_for_endpoint(endpoint) == expected


class TestMetadataCompleteness:
    def test_the_api_metadata_set_is_the_documented_one(self) -> None:
        assert METADATA_ATTRIBUTES == (
            "workflow_name",
            "workflow_description",
            "workflow_input_class",
            "workflow_api_endpoint",
        )

    def test_a_fully_declared_workflow_is_complete(self) -> None:
        assert workflow_has_complete_metadata(FullyDeclaredWorkflow)
        assert missing_metadata_attributes(FullyDeclaredWorkflow) == ()

    def test_a_bare_workflow_is_missing_everything(self) -> None:
        assert not workflow_has_complete_metadata(BareWorkflow)
        assert missing_metadata_attributes(BareWorkflow) == METADATA_ATTRIBUTES

    def test_missing_attributes_are_reported_in_declaration_order(self) -> None:
        assert missing_metadata_attributes(PartiallyDeclaredWorkflow) == (
            "workflow_name",
            "workflow_input_class",
            "workflow_api_endpoint",
        )


class TestRequiredActivities:
    def test_declared_functions_resolve_to_their_temporal_names(self) -> None:
        assert workflow_required_activity_names(FullyDeclaredWorkflow) == (
            "collect_facts",
            "apply_config",
        )

    def test_the_accessor_overrides_the_attribute(self) -> None:
        assert declared_required_activities(AccessorActivitiesWorkflow) == (apply_configuration,)
        assert workflow_required_activity_names(AccessorActivitiesWorkflow) == ("apply_config",)

    def test_an_undeclared_requirement_reads_as_no_requirement(self) -> None:
        assert workflow_required_activity_names(BareWorkflow) == ()
        assert declared_required_activities(BareWorkflow) == ()

    @pytest.mark.parametrize("declared", [(), [], (collect_facts,)])
    def test_sequences_are_accepted(self, declared: Any) -> None:
        assert is_activity_sequence(declared)

    @pytest.mark.parametrize(
        "declared",
        [
            None,
            "collect_facts",
            42,
            collect_facts,
            iter((collect_facts,)),
            {collect_facts},
            {"collect_facts": None},
        ],
    )
    def test_non_sequences_are_rejected(self, declared: Any) -> None:
        assert not is_activity_sequence(declared)
