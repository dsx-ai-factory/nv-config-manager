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

from collections.abc import Sequence
from typing import Any

import pytest
from pydantic import BaseModel
from temporalio import activity, workflow
from temporalio.common import RawValue

from nv_config_manager_workflows.metadata import WorkflowMetadataMixin
from nv_config_manager_workflows.registration.descriptor import WorkflowPluginDescriptor
from nv_config_manager_workflows.registration.errors import (
    WorkflowConflictError,
    WorkflowRegistrationError,
    WorkflowRequiredActivityError,
)
from nv_config_manager_workflows.registration.scheduler import WorkflowScheduler
from nv_config_manager_workflows.registration.validation import (
    REQUIRED_WORKFLOW_BASES,
    validate_plugins,
    validate_workflow_bases,
)
from nv_config_manager_workflows.stage import StageMixin


class DeviceInput(BaseModel):
    device: str


@activity.defn
async def collect_facts() -> None: ...


@activity.defn
async def push_config() -> None: ...


@activity.defn(name="collect_facts")
async def collect_facts_twin() -> None: ...


@activity.defn(name="apply_config")
async def apply_configuration() -> None: ...


@activity.defn(dynamic=True)
async def catch_all_activity(args: Sequence[RawValue]) -> None: ...


async def undecorated_activity() -> None: ...


@workflow.defn
class AlphaWorkflow(WorkflowMetadataMixin, StageMixin):
    workflow_name = "Alpha"
    workflow_description = "The reference workflow: complete and fully exposed"
    workflow_input_class = DeviceInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/config/alpha"
    workflow_mcp_enabled = True
    workflow_required_activities = (collect_facts,)

    @workflow.run
    async def run(self, workflow_input: BaseModel) -> None: ...

    @classmethod
    def get_workflow_cli_name(cls) -> str:
        return "alpha"


@workflow.defn
class BetaWorkflow(WorkflowMetadataMixin, StageMixin):
    workflow_name = "Beta"
    workflow_description = "A second plugin's workflow, disjoint from Alpha"
    workflow_input_class = DeviceInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/config/beta"
    workflow_mcp_enabled = True
    workflow_required_activities = (push_config,)

    @workflow.run
    async def run(self, workflow_input: BaseModel) -> None: ...

    @classmethod
    def get_workflow_cli_name(cls) -> str:
        return "beta"


@workflow.defn
class BareWorkflow(WorkflowMetadataMixin, StageMixin):
    """Declares no metadata: registrable by the worker, exposed nowhere else."""

    @workflow.run
    async def run(self, workflow_input: BaseModel) -> None: ...


@workflow.defn
class InternalWorkflow(WorkflowMetadataMixin, StageMixin):
    """Complete invocation metadata, but no public API endpoint."""

    workflow_name = "Internal"
    workflow_description = "Invoked only by another workflow"
    workflow_input_class = DeviceInput

    @workflow.run
    async def run(self, workflow_input: BaseModel) -> None: ...


@workflow.defn(name="AlphaWorkflow")
class AlphaTypeTwinWorkflow(WorkflowMetadataMixin, StageMixin):
    """A different class claiming the Temporal type Alpha already registers."""

    @workflow.run
    async def run(self, workflow_input: BaseModel) -> None: ...


@workflow.defn(name="AlphaNameTwinType")
class AlphaNameTwinWorkflow(WorkflowMetadataMixin, StageMixin):
    """A second distribution's class that happens to share Alpha's Python name."""

    @workflow.run
    async def run(self, workflow_input: BaseModel) -> None: ...


# Temporal requires the run method's qualified name to match the class declaring
# it, so the collision with AlphaWorkflow is introduced after decoration.
AlphaNameTwinWorkflow.__name__ = "AlphaWorkflow"


@workflow.defn
class UnlaunchableCliWorkflow(WorkflowMetadataMixin, StageMixin):
    """Claims Alpha's CLI name, but has no metadata to be launched with."""

    @workflow.run
    async def run(self, workflow_input: BaseModel) -> None: ...

    @classmethod
    def get_workflow_cli_name(cls) -> str:
        return "alpha"


@workflow.defn(dynamic=True)
class CatchAllWorkflow(WorkflowMetadataMixin, StageMixin):
    @workflow.run
    # This intentionally violates the normal workflow signature to exercise rejection
    # of Temporal's dynamic workflow contract during plugin validation.
    async def run(  # type: ignore[override]  # ty: ignore[invalid-method-override]
        self, args: Sequence[RawValue]
    ) -> None: ...


class UndecoratedWorkflow(WorkflowMetadataMixin, StageMixin):
    @workflow.run
    async def run(self, workflow_input: BaseModel) -> None: ...


class UndecoratedMisdeclaredWorkflow(WorkflowMetadataMixin, StageMixin):
    """Undecorated, and what metadata it declares is unusable as well."""

    workflow_name = 42  # type: ignore[assignment]  # pyright: ignore[reportAssignmentType]

    @workflow.run
    async def run(self, workflow_input: BaseModel) -> None: ...


@workflow.defn
class MissingBothBasesWorkflow:
    @workflow.run
    async def run(self) -> None: ...


@workflow.defn
class MissingStageMixinWorkflow(WorkflowMetadataMixin):
    @workflow.run
    async def run(self) -> None: ...


@workflow.defn
class MissingMetadataMixinWorkflow(StageMixin):
    @workflow.run
    async def run(self, workflow_input: BaseModel) -> None: ...


@workflow.defn
class MissingBasesWithBadMetadataWorkflow:
    workflow_name = 42

    @workflow.run
    async def run(self) -> None: ...


class AdditionalWorkflowMixin:
    """An optional plugin-owned workflow capability."""


@workflow.defn
class ExtendedWorkflow(AdditionalWorkflowMixin, WorkflowMetadataMixin, StageMixin):
    @workflow.run
    async def run(self, workflow_input: BaseModel) -> None: ...


class AsyncScheduler:
    async def run(self) -> None: ...


class SynchronousScheduler:
    def run(self) -> None: ...


class SchedulerWithRequiredRunArgument:
    async def run(self, interval: int) -> None: ...


class SchedulerWithoutRun: ...


class SchedulerInheritingProtocolStub(WorkflowScheduler): ...


def plugin(
    name: str,
    *,
    workflows: tuple[type, ...] = (),
    activities: tuple[Any, ...] = (),
    schedulers: tuple[type, ...] = (),
) -> WorkflowPluginDescriptor:
    return WorkflowPluginDescriptor(
        name=name,
        workflows=workflows,
        activities=activities,
        schedulers=schedulers,
    )


def installed(*descriptors: WorkflowPluginDescriptor) -> dict[str, WorkflowPluginDescriptor]:
    """Key descriptors by their own name, the way discovery hands them over."""
    return {descriptor.name: descriptor for descriptor in descriptors}


def alpha_plugin(name: str = "alpha-plugin") -> WorkflowPluginDescriptor:
    return plugin(name, workflows=(AlphaWorkflow,), activities=(collect_facts,))


def beta_plugin(name: str = "beta-plugin") -> WorkflowPluginDescriptor:
    return plugin(name, workflows=(BetaWorkflow,), activities=(push_config,))


class TestAcceptedPlugins:
    def test_an_environment_with_no_plugins_is_valid(self) -> None:
        validate_plugins({})

    def test_a_plugin_contributing_nothing_is_valid(self) -> None:
        validate_plugins(installed(plugin("empty-plugin")))

    def test_a_fully_declared_plugin_is_valid(self) -> None:
        validate_plugins(installed(alpha_plugin()))

    def test_an_async_scheduler_is_valid(self) -> None:
        validate_plugins(installed(plugin("scheduler-plugin", schedulers=(AsyncScheduler,))))

    def test_a_workflow_may_decline_to_declare_any_metadata(self) -> None:
        validate_plugins(installed(plugin("bare-plugin", workflows=(BareWorkflow,))))

    def test_an_api_disabled_workflow_may_omit_its_endpoint(self) -> None:
        validate_plugins(installed(plugin("internal-plugin", workflows=(InternalWorkflow,))))

    def test_a_workflow_may_inherit_additional_bases(self) -> None:
        validate_plugins(installed(plugin("extended-plugin", workflows=(ExtendedWorkflow,))))

    def test_plugins_with_disjoint_catalogs_are_valid(self) -> None:
        validate_plugins(installed(alpha_plugin(), beta_plugin()))

    def test_the_same_workflow_listed_twice_is_redundant_rather_than_conflicting(self) -> None:
        validate_plugins(
            installed(
                plugin(
                    "alpha-plugin",
                    workflows=(AlphaWorkflow, AlphaWorkflow),
                    activities=(collect_facts, collect_facts),
                )
            )
        )

    def test_two_plugins_may_re_export_one_shared_workflow(self) -> None:
        """A plugin built on another's catalog contributes the same objects."""
        validate_plugins(installed(alpha_plugin(), alpha_plugin("downstream-plugin")))


class TestTemporalDefinitionRequired:
    def test_an_undecorated_workflow_is_rejected(self) -> None:
        with pytest.raises(WorkflowRegistrationError) as raised:
            validate_plugins(installed(plugin("broken-plugin", workflows=(UndecoratedWorkflow,))))

        assert "not decorated with @workflow.defn" in str(raised.value)
        assert '"broken-plugin"' in str(raised.value)

    def test_an_undecorated_activity_is_rejected(self) -> None:
        with pytest.raises(WorkflowRegistrationError) as raised:
            validate_plugins(installed(plugin("broken-plugin", activities=(undecorated_activity,))))

        assert "not decorated with @activity.defn" in str(raised.value)

    def test_a_dynamic_workflow_may_not_be_contributed(self) -> None:
        with pytest.raises(WorkflowRegistrationError) as raised:
            validate_plugins(installed(plugin("greedy-plugin", workflows=(CatchAllWorkflow,))))

        assert "dynamic=True" in str(raised.value)
        assert "the other installed plugins do not claim" in str(raised.value)

    def test_a_dynamic_activity_may_not_be_contributed(self) -> None:
        with pytest.raises(WorkflowRegistrationError) as raised:
            validate_plugins(installed(plugin("greedy-plugin", activities=(catch_all_activity,))))

        assert "dynamic=True" in str(raised.value)

    def test_a_missing_definition_is_reported_ahead_of_unusable_metadata(self) -> None:
        """A class the worker could not register at all is the root cause to report."""
        with pytest.raises(WorkflowRegistrationError) as raised:
            validate_plugins(
                installed(plugin("broken-plugin", workflows=(UndecoratedMisdeclaredWorkflow,)))
            )

        assert "not decorated with @workflow.defn" in str(raised.value)


class TestSchedulerContract:
    def test_a_scheduler_must_declare_a_run_method(self) -> None:
        # model_construct bypasses the descriptor's protocol validation so the
        # registry's defensive error handling is exercised directly.
        descriptor = WorkflowPluginDescriptor.model_construct(
            name="broken-plugin",
            schedulers=(SchedulerWithoutRun,),
        )

        with pytest.raises(WorkflowRegistrationError) as raised:
            validate_plugins(installed(descriptor))

        assert "Scheduler" in str(raised.value)
        assert "does not declare run()" in str(raised.value)
        assert 'plugin "broken-plugin"' in str(raised.value)

    def test_a_scheduler_run_method_must_be_async(self) -> None:
        with pytest.raises(WorkflowRegistrationError) as raised:
            validate_plugins(installed(plugin("broken-plugin", schedulers=(SynchronousScheduler,))))

        assert "Scheduler" in str(raised.value)
        assert "run(), which is not async" in str(raised.value)
        assert 'plugin "broken-plugin"' in str(raised.value)

    def test_a_scheduler_run_method_must_accept_no_arguments(self) -> None:
        with pytest.raises(WorkflowRegistrationError) as raised:
            validate_plugins(
                installed(
                    plugin(
                        "broken-plugin",
                        schedulers=(SchedulerWithRequiredRunArgument,),
                    )
                )
            )

        assert "run(), which cannot be called without arguments" in str(raised.value)
        assert 'plugin "broken-plugin"' in str(raised.value)

    def test_a_scheduler_must_implement_the_protocol_method(self) -> None:
        with pytest.raises(WorkflowRegistrationError) as raised:
            validate_plugins(
                installed(
                    plugin(
                        "broken-plugin",
                        schedulers=(SchedulerInheritingProtocolStub,),
                    )
                )
            )

        assert "is abstract and cannot be constructed" in str(raised.value)
        assert 'plugin "broken-plugin"' in str(raised.value)


class TestRequiredWorkflowBases:
    def test_the_required_bases_are_the_two_public_mixins(self) -> None:
        assert REQUIRED_WORKFLOW_BASES == (StageMixin, WorkflowMetadataMixin)

    @pytest.mark.parametrize(
        ("workflow_class", "missing"),
        [
            (MissingBothBasesWorkflow, "StageMixin, WorkflowMetadataMixin"),
            (MissingStageMixinWorkflow, "StageMixin"),
            (MissingMetadataMixinWorkflow, "WorkflowMetadataMixin"),
        ],
    )
    def test_a_plugin_workflow_must_inherit_every_required_base(
        self, workflow_class: type, missing: str
    ) -> None:
        with pytest.raises(WorkflowRegistrationError) as raised:
            validate_plugins(installed(plugin("broken-plugin", workflows=(workflow_class,))))

        assert f"does not inherit {missing}" in str(raised.value)
        assert 'plugin "broken-plugin"' in str(raised.value)

    def test_missing_bases_are_reported_before_bad_metadata(self) -> None:
        with pytest.raises(WorkflowRegistrationError) as raised:
            validate_plugins(
                installed(
                    plugin(
                        "broken-plugin",
                        workflows=(MissingBasesWithBadMetadataWorkflow,),
                    )
                )
            )

        assert "does not inherit StageMixin, WorkflowMetadataMixin" in str(raised.value)
        assert "workflow_name" not in str(raised.value)

    def test_runtime_catalogs_use_the_same_base_validation(self) -> None:
        with pytest.raises(
            WorkflowRegistrationError,
            match="does not inherit StageMixin, WorkflowMetadataMixin",
        ):
            validate_workflow_bases((MissingBothBasesWorkflow,))


class TestDeclaredMetadata:
    @pytest.mark.parametrize("declared", [42, "", "   "])
    def test_workflow_name_must_be_a_non_empty_string(
        self, monkeypatch: pytest.MonkeyPatch, declared: Any
    ) -> None:
        monkeypatch.setattr(AlphaWorkflow, "workflow_name", declared)

        with pytest.raises(WorkflowRegistrationError) as raised:
            validate_plugins(installed(alpha_plugin()))

        assert "workflow_name" in str(raised.value)

    @pytest.mark.parametrize("declared", [42, "", "   "])
    def test_workflow_description_must_be_a_non_empty_string(
        self, monkeypatch: pytest.MonkeyPatch, declared: Any
    ) -> None:
        monkeypatch.setattr(AlphaWorkflow, "workflow_description", declared)

        with pytest.raises(WorkflowRegistrationError, match="workflow_description"):
            validate_plugins(installed(alpha_plugin()))

    def test_workflow_input_class_must_be_a_class(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(AlphaWorkflow, "workflow_input_class", DeviceInput(device="leaf-01"))

        with pytest.raises(WorkflowRegistrationError, match="which is not a class"):
            validate_plugins(installed(alpha_plugin()))

    @pytest.mark.parametrize("declared", [int, object])
    def test_workflow_input_class_must_be_a_pydantic_model(
        self, monkeypatch: pytest.MonkeyPatch, declared: type
    ) -> None:
        monkeypatch.setattr(AlphaWorkflow, "workflow_input_class", declared)

        with pytest.raises(
            WorkflowRegistrationError,
            match="not a Pydantic BaseModel subclass",
        ):
            validate_plugins(installed(alpha_plugin()))

    @pytest.mark.parametrize("declared", [1, "true", None])
    def test_api_opt_in_must_be_a_bool(
        self, monkeypatch: pytest.MonkeyPatch, declared: Any
    ) -> None:
        monkeypatch.setattr(AlphaWorkflow, "workflow_api_enabled", declared)

        with pytest.raises(WorkflowRegistrationError, match="which is not a bool"):
            validate_plugins(installed(alpha_plugin()))

    @pytest.mark.parametrize("declared", [1, "true", None])
    def test_mcp_opt_in_must_be_a_bool(
        self, monkeypatch: pytest.MonkeyPatch, declared: Any
    ) -> None:
        monkeypatch.setattr(AlphaWorkflow, "workflow_mcp_enabled", declared)

        with pytest.raises(WorkflowRegistrationError, match="which is not a bool"):
            validate_plugins(installed(alpha_plugin()))

    @pytest.mark.parametrize("declared", ["config/alpha", "/config alpha", "/config/alpha\n"])
    def test_endpoint_must_be_a_path_the_router_can_serve(
        self, monkeypatch: pytest.MonkeyPatch, declared: str
    ) -> None:
        monkeypatch.setattr(AlphaWorkflow, "workflow_api_endpoint", declared)

        with pytest.raises(WorkflowRegistrationError) as raised:
            validate_plugins(installed(alpha_plugin()))

        assert "workflow_api_endpoint" in str(raised.value)

    @pytest.mark.parametrize("declared", ["/", "//"])
    def test_endpoint_must_name_a_path_of_its_own(
        self, monkeypatch: pytest.MonkeyPatch, declared: str
    ) -> None:
        """The API root serves the service itself, so no workflow may be invoked there."""
        monkeypatch.setattr(AlphaWorkflow, "workflow_api_endpoint", declared)

        with pytest.raises(WorkflowRegistrationError) as raised:
            validate_plugins(installed(alpha_plugin()))

        assert "names no path segment" in str(raised.value)

    @pytest.mark.parametrize("declared", [42, ""])
    def test_endpoint_must_be_a_non_empty_string(
        self, monkeypatch: pytest.MonkeyPatch, declared: Any
    ) -> None:
        monkeypatch.setattr(AlphaWorkflow, "workflow_api_endpoint", declared)

        with pytest.raises(WorkflowRegistrationError, match="workflow_api_endpoint"):
            validate_plugins(installed(alpha_plugin()))

    def test_an_api_workflow_must_declare_the_whole_metadata_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(AlphaWorkflow, "workflow_description", None)
        monkeypatch.setattr(AlphaWorkflow, "workflow_mcp_enabled", False)

        with pytest.raises(WorkflowRegistrationError) as raised:
            validate_plugins(installed(alpha_plugin()))

        assert "enables API but is missing workflow_description" in str(raised.value)

    def test_an_api_enabled_workflow_must_declare_an_endpoint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(AlphaWorkflow, "workflow_api_endpoint", None)
        monkeypatch.setattr(AlphaWorkflow, "workflow_mcp_enabled", False)

        with pytest.raises(
            WorkflowRegistrationError,
            match="enables API but is missing workflow_api_endpoint",
        ):
            validate_plugins(installed(alpha_plugin()))

    def test_completeness_accessor_cannot_hide_missing_metadata(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(AlphaWorkflow, "workflow_description", None)
        monkeypatch.setattr(
            AlphaWorkflow,
            "has_complete_metadata",
            classmethod(lambda cls: True),
            raising=False,
        )

        with pytest.raises(WorkflowRegistrationError, match="missing workflow_description"):
            validate_plugins(installed(alpha_plugin()))

    def test_an_mcp_workflow_must_declare_the_whole_metadata_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(AlphaWorkflow, "workflow_api_endpoint", None)
        monkeypatch.setattr(AlphaWorkflow, "workflow_description", None)

        with pytest.raises(WorkflowRegistrationError) as raised:
            validate_plugins(installed(alpha_plugin()))

        assert "enables MCP but is missing" in str(raised.value)
        assert "workflow_description, workflow_api_endpoint" in str(raised.value)

    def test_an_mcp_workflow_must_also_enable_the_api(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(AlphaWorkflow, "workflow_api_enabled", False)

        with pytest.raises(
            WorkflowRegistrationError,
            match="enables MCP but does not enable API",
        ):
            validate_plugins(installed(alpha_plugin()))

    @pytest.mark.parametrize("declared", ["", "   ", None, 42])
    def test_a_declared_cli_accessor_must_return_a_usable_name(
        self, monkeypatch: pytest.MonkeyPatch, declared: Any
    ) -> None:
        """A silently dropped CLI command is the failure the registry replaces."""
        monkeypatch.setattr(
            AlphaWorkflow, "get_workflow_cli_name", classmethod(lambda cls: declared)
        )

        with pytest.raises(WorkflowRegistrationError) as raised:
            validate_plugins(installed(alpha_plugin()))

        assert "get_workflow_cli_name()" in str(raised.value)
        assert '"alpha-plugin"' in str(raised.value)

    def test_a_workflow_may_decline_to_declare_a_cli_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delattr(AlphaWorkflow, "get_workflow_cli_name")

        validate_plugins(installed(alpha_plugin()))

    @pytest.mark.parametrize("declared", [42, "collect_facts"])
    def test_required_activities_must_be_a_sequence(
        self, monkeypatch: pytest.MonkeyPatch, declared: Any
    ) -> None:
        monkeypatch.setattr(AlphaWorkflow, "workflow_required_activities", declared)

        with pytest.raises(WorkflowRegistrationError, match="not a sequence of activity functions"):
            validate_plugins(installed(alpha_plugin()))

    def test_required_activities_cannot_be_a_one_shot_iterator(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            AlphaWorkflow,
            "workflow_required_activities",
            iter((collect_facts,)),
        )

        with pytest.raises(WorkflowRegistrationError, match="not a sequence of activity functions"):
            validate_plugins(installed(alpha_plugin()))

    @pytest.mark.parametrize("entry", [None, "", "   ", 42, undecorated_activity])
    def test_each_required_activity_must_be_identifiable(
        self, monkeypatch: pytest.MonkeyPatch, entry: Any
    ) -> None:
        monkeypatch.setattr(AlphaWorkflow, "workflow_required_activities", (collect_facts, entry))

        with pytest.raises(WorkflowRegistrationError) as raised:
            validate_plugins(installed(alpha_plugin()))

        assert "neither an activity function nor a non-empty activity name" in str(raised.value)


class TestConflictsBetweenPlugins:
    def test_two_plugins_may_not_ship_the_same_class_name(self) -> None:
        with pytest.raises(WorkflowConflictError) as raised:
            validate_plugins(
                installed(
                    alpha_plugin(),
                    plugin("twin-plugin", workflows=(AlphaNameTwinWorkflow,)),
                )
            )

        assert 'Duplicate workflow class name "AlphaWorkflow"' in str(raised.value)
        assert 'plugins "alpha-plugin" and "twin-plugin"' in str(raised.value)

    def test_two_plugins_may_not_ship_the_same_workflow_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(BetaWorkflow, "workflow_name", "Alpha")

        with pytest.raises(WorkflowConflictError, match='Duplicate workflow name "Alpha"'):
            validate_plugins(installed(alpha_plugin(), beta_plugin()))

    def test_two_plugins_may_not_ship_the_same_temporal_type(self) -> None:
        with pytest.raises(WorkflowConflictError) as raised:
            validate_plugins(
                installed(
                    alpha_plugin(),
                    plugin("twin-plugin", workflows=(AlphaTypeTwinWorkflow,)),
                )
            )

        assert 'Duplicate Temporal workflow type "AlphaWorkflow"' in str(raised.value)

    def test_two_plugins_may_not_ship_the_same_activity_name(self) -> None:
        with pytest.raises(WorkflowConflictError) as raised:
            validate_plugins(
                installed(
                    alpha_plugin(),
                    plugin("twin-plugin", activities=(collect_facts_twin,)),
                )
            )

        assert 'Duplicate activity name "collect_facts"' in str(raised.value)

    def test_two_plugins_may_not_serve_the_same_endpoint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A trailing slash is the same route, so it is the same conflict."""
        monkeypatch.setattr(BetaWorkflow, "workflow_api_endpoint", "/config/alpha/")

        with pytest.raises(WorkflowConflictError) as raised:
            validate_plugins(installed(alpha_plugin(), beta_plugin()))

        assert 'Duplicate workflow API endpoint "/config/alpha"' in str(raised.value)

    def test_two_plugins_may_not_claim_the_same_cli_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(BetaWorkflow, "get_workflow_cli_name", classmethod(lambda cls: "alpha"))

        with pytest.raises(WorkflowConflictError, match='Duplicate workflow CLI name "alpha"'):
            validate_plugins(installed(alpha_plugin(), beta_plugin()))

    def test_api_disabled_workflows_do_not_claim_endpoints_or_cli_names(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(BetaWorkflow, "workflow_api_enabled", False)
        monkeypatch.setattr(BetaWorkflow, "workflow_mcp_enabled", False)
        monkeypatch.setattr(BetaWorkflow, "workflow_api_endpoint", "/config/alpha")
        monkeypatch.setattr(BetaWorkflow, "get_workflow_cli_name", classmethod(lambda cls: "alpha"))

        validate_plugins(installed(alpha_plugin(), beta_plugin()))

    def test_a_cli_name_on_an_unlaunchable_workflow_is_not_a_conflict(self) -> None:
        """The CLI only offers workflows it has the metadata to launch."""
        validate_plugins(
            installed(
                alpha_plugin(),
                plugin("unlaunchable-plugin", workflows=(UnlaunchableCliWorkflow,)),
            )
        )

    def test_two_api_versions_of_one_workflow_collide_as_mcp_tools(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """MCP names the tool after the last path segment, so the paths differ but the tools do not."""
        monkeypatch.setattr(BetaWorkflow, "workflow_api_endpoint", "/config/v2/alpha")

        with pytest.raises(WorkflowConflictError, match='Duplicate MCP tool name "run_alpha"'):
            validate_plugins(installed(alpha_plugin(), beta_plugin()))

    def test_one_plugin_conflicting_with_itself_is_reported_as_such(self) -> None:
        with pytest.raises(WorkflowConflictError) as raised:
            validate_plugins(
                installed(
                    plugin(
                        "alpha-plugin",
                        workflows=(AlphaWorkflow,),
                        activities=(collect_facts, collect_facts_twin),
                    )
                )
            )

        assert 'contributed twice by workflow plugin "alpha-plugin"' in str(raised.value)


class TestRequiredActivities:
    def test_a_requirement_may_be_supplied_by_another_plugin(self) -> None:
        validate_plugins(
            installed(
                plugin("alpha-plugin", workflows=(AlphaWorkflow,)),
                plugin("activity-plugin", activities=(collect_facts,)),
            )
        )

    def test_an_unsupplied_requirement_is_rejected(self) -> None:
        with pytest.raises(WorkflowRequiredActivityError) as raised:
            validate_plugins(installed(plugin("alpha-plugin", workflows=(AlphaWorkflow,))))

        assert 'requires activity "collect_facts"' in str(raised.value)
        assert "no installed workflow plugin supplies" in str(raised.value)

    def test_a_requirement_declared_by_name_is_matched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(AlphaWorkflow, "workflow_required_activities", ("collect_facts",))

        validate_plugins(installed(alpha_plugin()))

    def test_a_requirement_declared_through_the_accessor_is_matched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            AlphaWorkflow,
            "get_workflow_required_activities",
            classmethod(lambda cls: (push_config,)),
            raising=False,
        )

        validate_plugins(
            installed(plugin("alpha-plugin", workflows=(AlphaWorkflow,), activities=(push_config,)))
        )

    def test_a_renamed_activity_is_matched_by_its_temporal_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(AlphaWorkflow, "workflow_required_activities", (apply_configuration,))

        validate_plugins(
            installed(
                plugin(
                    "alpha-plugin",
                    workflows=(AlphaWorkflow,),
                    activities=(apply_configuration,),
                )
            )
        )

    def test_a_requirement_naming_the_python_function_is_not_matched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``execute_activity`` dispatches on the Temporal name, not the function name."""
        monkeypatch.setattr(AlphaWorkflow, "workflow_required_activities", ("apply_configuration",))

        with pytest.raises(WorkflowRequiredActivityError, match="apply_configuration"):
            validate_plugins(
                installed(
                    plugin(
                        "alpha-plugin",
                        workflows=(AlphaWorkflow,),
                        activities=(apply_configuration,),
                    )
                )
            )
