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

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
from temporalio import activity, workflow

from nv_config_manager_workflows.registration import discovery
from nv_config_manager_workflows.registration.descriptor import (
    UNKNOWN_PLUGIN_VERSION,
    WorkflowPluginDescriptor,
)
from nv_config_manager_workflows.registration.discovery import (
    WORKFLOW_PLUGIN_ENTRY_POINT_GROUP,
    discover_workflow_plugins,
)
from nv_config_manager_workflows.registration.errors import (
    WorkflowPluginDiscoveryError,
    WorkflowPluginDuplicateError,
)


@workflow.defn
class ExampleWorkflow:
    @workflow.run
    async def run(self) -> None: ...


@activity.defn
async def example_activity() -> None: ...


@dataclass(frozen=True)
class FakeDistribution:
    """The installed distribution an entry point was registered by."""

    name: str = "example-plugin"
    version: Any = "4.2.0"
    location: str = "/usr/lib/python3.13/site-packages"

    def locate_file(self, path: str) -> str:
        return f"{self.location}/{path}"


@dataclass(frozen=True)
class FakeEntryPoint:
    """One line of a distribution's entry-point table."""

    name: str
    value: str = "example_plugin.registration:plugin"
    returns: Any = None
    raises: Exception | None = None
    dist: FakeDistribution | None = field(default_factory=FakeDistribution)

    def load(self) -> Any:
        if self.raises is not None:
            raise self.raises
        return self.returns


def descriptor(name: str = "example-plugin", **overrides: Any) -> WorkflowPluginDescriptor:
    return WorkflowPluginDescriptor(name=name, **overrides)


@pytest.fixture
def install(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Return a callable that publishes entry points to an entry-point group."""

    def install_entry_points(
        *entry_points: FakeEntryPoint,
        group: str = WORKFLOW_PLUGIN_ENTRY_POINT_GROUP,
    ) -> None:
        published_group = group

        def fake_entry_points(*, group: str) -> tuple[FakeEntryPoint, ...]:
            return entry_points if group == published_group else ()

        monkeypatch.setattr(discovery, "entry_points", fake_entry_points)

    return install_entry_points


class TestDiscoveredPlugins:
    def test_the_entry_point_group_is_the_one_plugins_register_under(self) -> None:
        """Renaming this orphans the packaging metadata of every published plugin."""
        assert WORKFLOW_PLUGIN_ENTRY_POINT_GROUP == "nv_config_manager.workflows"

    def test_an_environment_with_no_plugins_discovers_nothing(
        self, install: Callable[..., None]
    ) -> None:
        install()

        assert discover_workflow_plugins() == {}

    def test_only_the_workflow_plugin_group_is_scanned(self, install: Callable[..., None]) -> None:
        install(FakeEntryPoint(name="example-plugin"), group="console_scripts")

        assert discover_workflow_plugins() == {}

    def test_an_entry_point_may_point_at_a_descriptor(self, install: Callable[..., None]) -> None:
        install(FakeEntryPoint(name="example-plugin", returns=descriptor()))

        discovered = discover_workflow_plugins()

        assert list(discovered) == ["example-plugin"]
        assert discovered["example-plugin"].name == "example-plugin"

    def test_an_entry_point_may_point_at_a_factory(self, install: Callable[..., None]) -> None:
        install(FakeEntryPoint(name="example-plugin", returns=lambda: descriptor()))

        assert list(discover_workflow_plugins()) == ["example-plugin"]

    def test_plugins_are_keyed_and_ordered_by_entry_point_name(
        self, install: Callable[..., None]
    ) -> None:
        install(
            FakeEntryPoint(name="zulu-plugin", returns=descriptor("zulu-plugin")),
            FakeEntryPoint(name="alpha-plugin", returns=descriptor("alpha-plugin")),
        )

        assert list(discover_workflow_plugins()) == ["alpha-plugin", "zulu-plugin"]

    def test_the_declared_catalog_survives_discovery(self, install: Callable[..., None]) -> None:
        install(
            FakeEntryPoint(
                name="example-plugin",
                returns=descriptor(workflows=(ExampleWorkflow,), activities=(example_activity,)),
            )
        )

        discovered = discover_workflow_plugins()["example-plugin"]

        assert discovered.workflows == (ExampleWorkflow,)
        assert discovered.activities == (example_activity,)


class TestReportedVersion:
    def test_an_unset_version_is_filled_in_from_the_installed_distribution(
        self, install: Callable[..., None]
    ) -> None:
        install(
            FakeEntryPoint(
                name="example-plugin",
                returns=descriptor(),
                dist=FakeDistribution(version="1.2.3"),
            )
        )

        assert discover_workflow_plugins()["example-plugin"].version == "1.2.3"

    def test_a_declared_version_is_reported_instead(self, install: Callable[..., None]) -> None:
        install(
            FakeEntryPoint(
                name="example-plugin",
                returns=descriptor(version="7.0.0-rc1"),
                dist=FakeDistribution(version="1.2.3"),
            )
        )

        assert discover_workflow_plugins()["example-plugin"].version == "7.0.0-rc1"

    @pytest.mark.parametrize("reported", [None, "", 42], ids=["missing", "blank", "not-a-string"])
    def test_a_distribution_reporting_no_usable_version_falls_back(
        self, install: Callable[..., None], reported: Any
    ) -> None:
        install(
            FakeEntryPoint(
                name="example-plugin",
                returns=descriptor(),
                dist=FakeDistribution(version=reported),
            )
        )

        assert discover_workflow_plugins()["example-plugin"].version == UNKNOWN_PLUGIN_VERSION

    def test_an_entry_point_without_a_distribution_falls_back(
        self, install: Callable[..., None]
    ) -> None:
        install(FakeEntryPoint(name="example-plugin", returns=descriptor(), dist=None))

        assert discover_workflow_plugins()["example-plugin"].version == UNKNOWN_PLUGIN_VERSION

    def test_filling_the_version_in_preserves_the_rest_of_the_descriptor(
        self, install: Callable[..., None]
    ) -> None:
        install(
            FakeEntryPoint(
                name="example-plugin",
                returns=descriptor(workflows=(ExampleWorkflow,), metadata={"vendor": "nvidia"}),
            )
        )

        discovered = discover_workflow_plugins()["example-plugin"]

        assert discovered.version == "4.2.0"
        assert discovered.workflows == (ExampleWorkflow,)
        assert discovered.metadata == {"vendor": "nvidia"}


class TestRejectedPlugins:
    def test_an_import_failure_is_reported_against_the_plugin(
        self, install: Callable[..., None]
    ) -> None:
        install(
            FakeEntryPoint(
                name="example-plugin",
                value="example_plugin.registration:plugin",
                raises=ModuleNotFoundError("No module named 'example_plugin'"),
            )
        )

        with pytest.raises(WorkflowPluginDiscoveryError) as raised:
            discover_workflow_plugins()

        assert '"example-plugin" (example_plugin.registration:plugin) failed to load' in str(
            raised.value
        )
        assert isinstance(raised.value.__cause__, ModuleNotFoundError)

    def test_a_failing_factory_is_reported_against_the_plugin(
        self, install: Callable[..., None]
    ) -> None:
        def explode() -> WorkflowPluginDescriptor:
            raise RuntimeError("the plugin read config that is not there yet")

        install(FakeEntryPoint(name="example-plugin", returns=explode))

        with pytest.raises(WorkflowPluginDiscoveryError, match="failed to load"):
            discover_workflow_plugins()

    def test_registering_the_descriptor_class_instead_of_an_instance_is_reported(
        self, install: Callable[..., None]
    ) -> None:
        """Calling it yields a validation error, which is still a load failure."""
        install(FakeEntryPoint(name="example-plugin", returns=WorkflowPluginDescriptor))

        with pytest.raises(WorkflowPluginDiscoveryError, match="failed to load"):
            discover_workflow_plugins()

    def test_something_other_than_a_descriptor_is_rejected_by_type(
        self, install: Callable[..., None]
    ) -> None:
        install(FakeEntryPoint(name="example-plugin", returns={"workflows": []}))

        with pytest.raises(WorkflowPluginDiscoveryError) as raised:
            discover_workflow_plugins()

        assert "does not return WorkflowPluginDescriptor, got dict" in str(raised.value)

    def test_a_descriptor_naming_itself_something_else_is_rejected(
        self, install: Callable[..., None]
    ) -> None:
        """The entry-point table an operator reads has to match the diagnostics."""
        install(FakeEntryPoint(name="example-plugin", returns=descriptor("other-plugin")))

        with pytest.raises(WorkflowPluginDiscoveryError) as raised:
            discover_workflow_plugins()

        assert 'declares name "other-plugin"' in str(raised.value)

    def test_two_distributions_registering_one_name_are_rejected(
        self, install: Callable[..., None]
    ) -> None:
        install(
            FakeEntryPoint(name="example-plugin", returns=descriptor()),
            FakeEntryPoint(
                name="example-plugin",
                value="vendor_plugin.registration:plugin",
                returns=descriptor(),
                dist=FakeDistribution(name="vendor-plugin", version="9.9.9"),
            ),
        )

        with pytest.raises(WorkflowPluginDuplicateError) as raised:
            discover_workflow_plugins()

        assert 'Workflow plugin "example-plugin" is registered more than once' in str(raised.value)
        assert "vendor_plugin.registration:plugin" in str(raised.value)
        assert "distribution vendor-plugin 9.9.9" in str(raised.value)
        assert "/usr/lib/python3.13/site-packages" in str(raised.value)

    def test_a_duplicate_without_a_distribution_is_still_identified(
        self, install: Callable[..., None]
    ) -> None:
        install(
            FakeEntryPoint(name="example-plugin", returns=descriptor(), dist=None),
            FakeEntryPoint(
                name="example-plugin",
                value="vendor_plugin.registration:plugin",
                returns=descriptor(),
                dist=None,
            ),
        )

        with pytest.raises(WorkflowPluginDuplicateError) as raised:
            discover_workflow_plugins()

        assert "the duplicate registration is vendor_plugin.registration:plugin" in str(
            raised.value
        )


class TestRealEnvironment:
    def test_discovery_reads_the_installed_environment_without_failing(self) -> None:
        """Exercises the real ``importlib.metadata`` call the fakes above replace."""
        for name, discovered in discover_workflow_plugins().items():
            assert name == discovered.name
            assert discovered.version
