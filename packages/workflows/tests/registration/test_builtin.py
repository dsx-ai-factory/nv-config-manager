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
"""This package's own contribution to the registry.

The built-in catalog is still empty while the workflows themselves are being
moved into this package, so what these tests pin is that it is a plugin like any
other: it reaches the registry through its own entry point, satisfies the same
descriptor contract, and passes the same checks the installed plugins do.
"""

from nv_config_manager_workflows.registration.builtin import BUILTIN_PLUGIN_NAME, builtin_plugin
from nv_config_manager_workflows.registration.descriptor import (
    UNKNOWN_PLUGIN_VERSION,
    WorkflowPluginDescriptor,
)
from nv_config_manager_workflows.registration.discovery import discover_workflow_plugins
from nv_config_manager_workflows.registration.registry import WorkflowRegistry
from nv_config_manager_workflows.registration.validation import validate_plugins


class TestBuiltinPlugin:
    def test_it_is_registered_under_the_documented_name(self) -> None:
        assert BUILTIN_PLUGIN_NAME == "builtin"
        assert builtin_plugin().name == BUILTIN_PLUGIN_NAME

    def test_it_is_an_ordinary_plugin_descriptor(self) -> None:
        assert isinstance(builtin_plugin(), WorkflowPluginDescriptor)

    def test_it_contributes_nothing_yet(self) -> None:
        descriptor = builtin_plugin()

        assert descriptor.workflows == ()
        assert descriptor.activities == ()
        assert descriptor.schedulers == ()

    def test_its_version_is_left_to_the_installed_distribution(self) -> None:
        assert builtin_plugin().version is None

    def test_it_passes_the_checks_every_plugin_passes(self) -> None:
        validate_plugins({BUILTIN_PLUGIN_NAME: builtin_plugin()})

    def test_a_registry_built_from_it_alone_reports_it(self) -> None:
        registry = WorkflowRegistry.build({BUILTIN_PLUGIN_NAME: builtin_plugin()})

        assert registry.all_workflows == []
        assert [info.name for info in registry.plugin_diagnostics] == [BUILTIN_PLUGIN_NAME]


class TestBuiltinDiscovery:
    """The built-in catalog is not merged in by the registry, so it has to be found.

    An installed environment whose packaging metadata lost the entry point would
    otherwise start a worker with no workflows at all and say nothing about it.
    """

    def test_it_is_discovered_from_the_installed_metadata(self) -> None:
        assert BUILTIN_PLUGIN_NAME in discover_workflow_plugins()

    def test_discovery_reports_the_installed_distribution_version(self) -> None:
        """The descriptor declares no version, so discovery has to resolve one."""
        discovered = discover_workflow_plugins()[BUILTIN_PLUGIN_NAME]

        assert discovered.version not in (None, UNKNOWN_PLUGIN_VERSION)

    def test_a_registry_built_from_the_environment_includes_it(self) -> None:
        registry = WorkflowRegistry.build()

        assert BUILTIN_PLUGIN_NAME in {info.name for info in registry.plugin_diagnostics}
