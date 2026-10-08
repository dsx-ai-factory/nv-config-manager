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
"""How the contract reads Temporal identity out of the SDK's definitions.

The contract reads ``temporalio``'s definition types, which carry a leading
underscore. These tests pin that coupling: if the SDK stops reporting a workflow
type or an activity name the way it does today, the failure lands here instead of
in the registry silently treating every plugin class as undecorated.
"""

from collections.abc import Sequence

from temporalio import activity, workflow
from temporalio.common import RawValue

from nv_config_manager_workflows.metadata import WorkflowMetadataMixin
from nv_config_manager_workflows.registration.contract import (
    activity_has_definition,
    activity_is_dynamic,
    activity_name,
    required_activity_name,
    workflow_has_definition,
    workflow_is_dynamic,
    workflow_type_name,
)


@workflow.defn
class DeclaredWorkflow(WorkflowMetadataMixin):
    @workflow.run
    async def run(self) -> None: ...


@workflow.defn(name="RenamedType")
class RenamedWorkflow(WorkflowMetadataMixin):
    @workflow.run
    async def run(self) -> None: ...


@workflow.defn(dynamic=True)
class DynamicWorkflow(WorkflowMetadataMixin):
    @workflow.run
    async def run(self, args: Sequence[RawValue]) -> None: ...


class UndecoratedWorkflow(WorkflowMetadataMixin):
    @workflow.run
    async def run(self) -> None: ...


class InheritingWorkflow(DeclaredWorkflow):
    """Subclasses do not inherit the parent's Temporal type."""


@activity.defn
async def declared_activity() -> None: ...


@activity.defn(name="renamed_activity")
async def renamed_activity() -> None: ...


@activity.defn(dynamic=True)
async def dynamic_activity(args: Sequence[RawValue]) -> None: ...


async def undecorated_activity() -> None: ...


class ActivityHolder:
    """Activities are often methods so they can share connections or config."""

    @activity.defn
    async def held_activity(self) -> None: ...


class TestWorkflowIdentity:
    def test_declared_type_is_the_class_name_by_default(self) -> None:
        assert workflow_type_name(DeclaredWorkflow) == "DeclaredWorkflow"
        assert workflow_has_definition(DeclaredWorkflow)
        assert not workflow_is_dynamic(DeclaredWorkflow)

    def test_declared_type_honors_an_explicit_name(self) -> None:
        assert workflow_type_name(RenamedWorkflow) == "RenamedType"

    def test_dynamic_workflow_has_a_definition_but_no_type(self) -> None:
        assert workflow_has_definition(DynamicWorkflow)
        assert workflow_is_dynamic(DynamicWorkflow)
        assert workflow_type_name(DynamicWorkflow) is None

    def test_undecorated_class_is_neither_declared_nor_dynamic(self) -> None:
        assert not workflow_has_definition(UndecoratedWorkflow)
        assert not workflow_is_dynamic(UndecoratedWorkflow)
        assert workflow_type_name(UndecoratedWorkflow) is None

    def test_subclass_does_not_inherit_the_parent_type(self) -> None:
        """Two classes sharing one Temporal type would be a false conflict."""
        assert not workflow_has_definition(InheritingWorkflow)
        assert workflow_type_name(InheritingWorkflow) is None


class TestActivityIdentity:
    def test_declared_name_is_the_function_name_by_default(self) -> None:
        assert activity_name(declared_activity) == "declared_activity"
        assert activity_has_definition(declared_activity)
        assert not activity_is_dynamic(declared_activity)

    def test_declared_name_honors_an_explicit_name(self) -> None:
        assert activity_name(renamed_activity) == "renamed_activity"

    def test_dynamic_activity_has_a_definition_but_no_name(self) -> None:
        assert activity_has_definition(dynamic_activity)
        assert activity_is_dynamic(dynamic_activity)
        assert activity_name(dynamic_activity) is None

    def test_undecorated_callable_is_neither_declared_nor_dynamic(self) -> None:
        assert not activity_has_definition(undecorated_activity)
        assert not activity_is_dynamic(undecorated_activity)
        assert activity_name(undecorated_activity) is None

    def test_bound_method_resolves_to_its_declared_name(self) -> None:
        assert activity_name(ActivityHolder().held_activity) == "held_activity"


class TestRequiredActivityEntries:
    def test_activity_function_resolves_to_its_temporal_name(self) -> None:
        assert required_activity_name(renamed_activity) == "renamed_activity"

    def test_plain_name_is_accepted(self) -> None:
        assert required_activity_name("some_activity") == "some_activity"

    def test_undecorated_callable_is_rejected(self) -> None:
        """execute_activity would reject it, so the declaration is unusable."""
        assert required_activity_name(undecorated_activity) is None

    def test_unusable_entries_are_rejected(self) -> None:
        assert required_activity_name("") is None
        assert required_activity_name("   ") is None
        assert required_activity_name(None) is None
        assert required_activity_name(42) is None
