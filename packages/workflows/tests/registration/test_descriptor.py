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

import pytest
from pydantic import ValidationError
from temporalio import activity, workflow

from nv_config_manager_workflows.registration.descriptor import (
    UNKNOWN_PLUGIN_VERSION,
    WorkflowPluginDescriptor,
)


@workflow.defn
class ExampleWorkflow:
    @workflow.run
    async def run(self) -> None: ...


@activity.defn
async def example_activity() -> None: ...


class ExampleScheduler:
    async def run(self) -> None: ...


class MissingRunScheduler: ...


class TestDeclaredIdentity:
    def test_a_plugin_may_contribute_nothing_but_a_name(self) -> None:
        descriptor = WorkflowPluginDescriptor(name="example")

        assert descriptor.name == "example"
        assert descriptor.workflows == ()
        assert descriptor.activities == ()
        assert descriptor.schedulers == ()
        assert descriptor.metadata == {}

    def test_name_is_required(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowPluginDescriptor.model_validate({})

    def test_blank_name_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowPluginDescriptor(name="")

    def test_unset_version_is_left_for_discovery_to_fill_in(self) -> None:
        assert WorkflowPluginDescriptor(name="example").version is None

    def test_declared_version_is_kept(self) -> None:
        assert WorkflowPluginDescriptor(name="example", version="4.2.0").version == "4.2.0"

    def test_blank_version_is_rejected(self) -> None:
        """An empty string would report as a version rather than as unset."""
        with pytest.raises(ValidationError):
            WorkflowPluginDescriptor(name="example", version="")

    def test_unknown_version_is_the_documented_placeholder(self) -> None:
        assert UNKNOWN_PLUGIN_VERSION == "unknown"


class TestCatalogs:
    def test_any_sequence_is_accepted_and_stored_as_a_tuple(self) -> None:
        descriptor = WorkflowPluginDescriptor(
            name="example",
            workflows=[ExampleWorkflow],
            activities=[example_activity],
            schedulers=[ExampleScheduler],
        )

        assert isinstance(descriptor.workflows, tuple)
        assert isinstance(descriptor.activities, tuple)
        assert isinstance(descriptor.schedulers, tuple)
        assert descriptor.workflows == (ExampleWorkflow,)
        assert descriptor.activities == (example_activity,)
        assert descriptor.schedulers == (ExampleScheduler,)

    def test_the_plugin_cannot_reach_the_catalog_it_passed(self) -> None:
        declared = [ExampleWorkflow]
        descriptor = WorkflowPluginDescriptor(name="example", workflows=declared)

        declared.clear()

        assert descriptor.workflows == (ExampleWorkflow,)

    def test_a_workflow_must_be_a_class(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowPluginDescriptor.model_validate(
                {"name": "example", "workflows": [ExampleWorkflow()]}
            )

    def test_an_activity_must_be_callable(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowPluginDescriptor.model_validate(
                {"name": "example", "activities": ["collect_facts"]}
            )

    @pytest.mark.parametrize("scheduler", [ExampleScheduler(), MissingRunScheduler, object()])
    def test_a_scheduler_must_be_a_class_with_run(self, scheduler: object) -> None:
        with pytest.raises(ValidationError):
            WorkflowPluginDescriptor.model_validate({"name": "example", "schedulers": [scheduler]})

    def test_the_plugin_cannot_mutate_its_scheduler_catalog(self) -> None:
        declared = [ExampleScheduler]
        descriptor = WorkflowPluginDescriptor(name="example", schedulers=declared)

        declared.clear()

        assert descriptor.schedulers == (ExampleScheduler,)

    def test_metadata_defaults_are_not_shared_between_descriptors(self) -> None:
        first = WorkflowPluginDescriptor(name="first")
        first.metadata["operator_note"] = "set by the first plugin"

        assert WorkflowPluginDescriptor(name="second").metadata == {}


class TestImmutability:
    def test_fields_cannot_be_reassigned(self) -> None:
        descriptor = WorkflowPluginDescriptor(name="example")

        with pytest.raises(ValidationError):
            descriptor.name = "renamed"

    def test_descriptor_is_not_hashable_because_metadata_is_a_mapping(self) -> None:
        with pytest.raises(TypeError):
            hash(WorkflowPluginDescriptor(name="example"))

    def test_copying_leaves_the_original_alone(self) -> None:
        """Discovery fills in the version this way once the descriptor is built."""
        descriptor = WorkflowPluginDescriptor(name="example", workflows=(ExampleWorkflow,))

        filled = descriptor.model_copy(update={"version": "4.2.0"})

        assert filled.version == "4.2.0"
        assert filled.workflows == (ExampleWorkflow,)
        assert descriptor.version is None
