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

import dataclasses
from types import MappingProxyType
from typing import Any

import pytest
from pydantic import BaseModel
from temporalio import activity, workflow

from nv_config_manager_workflows.metadata import WorkflowMetadataMixin
from nv_config_manager_workflows.registration import registry as registry_module
from nv_config_manager_workflows.registration.descriptor import (
    UNKNOWN_PLUGIN_VERSION,
    WorkflowPluginDescriptor,
)
from nv_config_manager_workflows.registration.errors import WorkflowConflictError
from nv_config_manager_workflows.registration.registry import PluginInfo, WorkflowRegistry
from nv_config_manager_workflows.stage import StageMixin


class DeviceInput(BaseModel):
    device: str


@activity.defn
async def collect_facts() -> None: ...


@activity.defn
async def push_config() -> None: ...


class BackupScheduler:
    async def run(self) -> None: ...


class InventoryScheduler:
    async def run(self) -> None: ...


@workflow.defn
class AlphaWorkflow(WorkflowMetadataMixin, StageMixin):
    workflow_name = "Alpha"
    workflow_description = "Complete metadata, exposed over the API and MCP"
    workflow_input_class = DeviceInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/config/alpha"
    workflow_mcp_enabled = True

    @workflow.run
    async def run(self, workflow_input: BaseModel) -> None: ...

    @classmethod
    def get_workflow_cli_name(cls) -> str:
        return "alpha"


@workflow.defn
class BetaWorkflow(WorkflowMetadataMixin, StageMixin):
    workflow_name = "Beta"
    workflow_description = "Complete metadata, but not offered as an MCP tool"
    workflow_input_class = DeviceInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/config/beta"

    @workflow.run
    async def run(self, workflow_input: BaseModel) -> None: ...

    @classmethod
    def get_workflow_cli_name(cls) -> str:
        return "beta"


@workflow.defn
class InternalWorkflow(WorkflowMetadataMixin, StageMixin):
    """Declares no metadata: the worker runs it, nothing else offers it."""

    @workflow.run
    async def run(self, workflow_input: BaseModel) -> None: ...


@workflow.defn
class ApiDisabledWorkflow(WorkflowMetadataMixin, StageMixin):
    """Complete API metadata, but intentionally unavailable for direct invocation."""

    workflow_name = "API Disabled"
    workflow_description = "Invoked only by another workflow"
    workflow_input_class = DeviceInput
    workflow_api_endpoint = "/internal/api-disabled"

    @workflow.run
    async def run(self, workflow_input: BaseModel) -> None: ...


def plugin(
    name: str,
    *,
    version: str | None = None,
    workflows: tuple[type, ...] = (),
    activities: tuple[Any, ...] = (),
    schedulers: tuple[type, ...] = (),
) -> WorkflowPluginDescriptor:
    return WorkflowPluginDescriptor(
        name=name,
        version=version,
        workflows=workflows,
        activities=activities,
        schedulers=schedulers,
    )


def installed(*descriptors: WorkflowPluginDescriptor) -> dict[str, WorkflowPluginDescriptor]:
    return {descriptor.name: descriptor for descriptor in descriptors}


class TestEmptyRegistry:
    def test_a_registry_starts_out_empty(self) -> None:
        registry = WorkflowRegistry()

        assert registry.all_workflows == []
        assert registry.all_activities == []
        assert registry.all_schedulers == []
        assert registry.api_workflows == []
        assert registry.mcp_workflows == []
        assert registry.plugin_diagnostics == []

    def test_a_clean_install_with_no_plugins_builds_an_empty_registry(self) -> None:
        registry = WorkflowRegistry.build({})

        assert registry.all_workflows == []
        assert registry.plugin_diagnostics == []

    def test_a_plugin_contributing_nothing_still_reports_itself(self) -> None:
        registry = WorkflowRegistry.build(installed(plugin("empty-plugin", version="1.0.0")))

        assert registry.all_workflows == []
        assert registry.plugin_diagnostics == [
            PluginInfo(
                name="empty-plugin",
                version="1.0.0",
                workflow_count=0,
                activity_count=0,
                scheduler_count=0,
            )
        ]


class TestMergedCatalogs:
    def test_catalogs_are_ordered_by_plugin_name_then_by_declaration(self) -> None:
        registry = WorkflowRegistry.build(
            installed(
                plugin(
                    "zulu-plugin",
                    workflows=(InternalWorkflow,),
                    activities=(push_config,),
                    schedulers=(InventoryScheduler,),
                ),
                plugin(
                    "alpha-plugin",
                    workflows=(AlphaWorkflow, BetaWorkflow),
                    activities=(collect_facts,),
                    schedulers=(BackupScheduler,),
                ),
            )
        )

        assert registry.all_workflows == [AlphaWorkflow, BetaWorkflow, InternalWorkflow]
        assert registry.all_activities == [collect_facts, push_config]
        assert registry.all_schedulers == [BackupScheduler, InventoryScheduler]

    def test_a_workflow_two_plugins_both_contribute_is_registered_once(self) -> None:
        registry = WorkflowRegistry.build(
            installed(
                plugin("alpha-plugin", workflows=(AlphaWorkflow,), activities=(collect_facts,)),
                plugin(
                    "downstream-plugin",
                    workflows=(AlphaWorkflow,),
                    activities=(collect_facts,),
                    schedulers=(BackupScheduler,),
                ),
                plugin("scheduler-plugin", schedulers=(BackupScheduler,)),
            )
        )

        assert registry.all_workflows == [AlphaWorkflow]
        assert registry.all_activities == [collect_facts]
        assert registry.all_schedulers == [BackupScheduler]

    def test_the_api_offers_only_workflows_that_opted_in(self) -> None:
        registry = WorkflowRegistry.build(
            installed(
                plugin(
                    "alpha-plugin",
                    workflows=(
                        AlphaWorkflow,
                        BetaWorkflow,
                        ApiDisabledWorkflow,
                        InternalWorkflow,
                    ),
                )
            )
        )

        assert registry.api_workflows == [AlphaWorkflow, BetaWorkflow]
        assert ApiDisabledWorkflow in registry.all_workflows

    def test_mcp_offers_only_workflows_that_opted_in(self) -> None:
        registry = WorkflowRegistry.build(
            installed(
                plugin("alpha-plugin", workflows=(AlphaWorkflow, BetaWorkflow, InternalWorkflow))
            )
        )

        assert registry.mcp_workflows == [AlphaWorkflow]


class TestPluginDiagnostics:
    def test_every_plugin_is_reported_in_name_order(self) -> None:
        registry = WorkflowRegistry.build(
            installed(
                plugin("zulu-plugin", version="2.0.0"), plugin("alpha-plugin", version="1.0.0")
            )
        )

        assert [info.name for info in registry.plugin_diagnostics] == [
            "alpha-plugin",
            "zulu-plugin",
        ]

    def test_a_plugin_of_unreported_version_is_named_as_such(self) -> None:
        """Discovery normally fills the version in; nothing hides it if it did not."""
        registry = WorkflowRegistry.build(installed(plugin("alpha-plugin")))

        assert registry.plugin_diagnostics[0].version == UNKNOWN_PLUGIN_VERSION

    def test_counts_describe_what_each_plugin_declared(self) -> None:
        registry = WorkflowRegistry.build(
            installed(
                plugin(
                    "alpha-plugin",
                    workflows=(AlphaWorkflow,),
                    activities=(collect_facts,),
                    schedulers=(BackupScheduler,),
                ),
                plugin(
                    "downstream-plugin",
                    workflows=(AlphaWorkflow, InternalWorkflow),
                    activities=(collect_facts,),
                    schedulers=(BackupScheduler, InventoryScheduler),
                ),
            )
        )

        assert [
            (info.workflow_count, info.activity_count, info.scheduler_count)
            for info in registry.plugin_diagnostics
        ] == [
            (1, 1, 1),
            (2, 1, 2),
        ]

    def test_diagnostics_cannot_be_edited_after_the_registry_is_built(self) -> None:
        info = PluginInfo(
            name="alpha-plugin",
            version="1.0.0",
            workflow_count=1,
            activity_count=1,
            scheduler_count=0,
        )

        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(info, "version", "2.0.0")


class TestBuildInputs:
    def test_building_without_a_mapping_discovers_the_installed_plugins(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            registry_module,
            "discover_workflow_plugins",
            lambda: installed(plugin("alpha-plugin", workflows=(AlphaWorkflow,))),
        )

        assert WorkflowRegistry.build().all_workflows == [AlphaWorkflow]

    def test_any_mapping_may_be_passed(self) -> None:
        """The registry copies what it is given rather than reordering it in place."""
        declared = installed(plugin("alpha-plugin", workflows=(AlphaWorkflow,)))

        registry = WorkflowRegistry.build(MappingProxyType(declared))

        assert registry.all_workflows == [AlphaWorkflow]

    def test_a_rejected_plugin_set_fails_the_build(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(BetaWorkflow, "workflow_api_endpoint", "/config/alpha")

        with pytest.raises(WorkflowConflictError):
            WorkflowRegistry.build(
                installed(
                    plugin("alpha-plugin", workflows=(AlphaWorkflow,)),
                    plugin("beta-plugin", workflows=(BetaWorkflow,)),
                )
            )
