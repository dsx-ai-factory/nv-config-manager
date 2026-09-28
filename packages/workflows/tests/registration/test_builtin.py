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
"""This package's own contribution to the registry."""

from nv_config_manager_workflows.activities.backup import BACKUP_ACTIVITIES
from nv_config_manager_workflows.activities.bmc import BMC_ACTIVITIES
from nv_config_manager_workflows.activities.builtin import BUILTIN_ACTIVITIES
from nv_config_manager_workflows.activities.cable_validation import (
    CABLE_VALIDATION_ACTIVITIES,
)
from nv_config_manager_workflows.activities.config import CONFIG_ACTIVITIES
from nv_config_manager_workflows.activities.dcim import DCIM_ACTIVITIES
from nv_config_manager_workflows.activities.deploy import DEPLOY_ACTIVITIES
from nv_config_manager_workflows.activities.device import DEVICE_ACTIVITIES
from nv_config_manager_workflows.activities.device_password_rotation import (
    DEVICE_PASSWORD_ROTATION_ACTIVITIES,
)
from nv_config_manager_workflows.activities.diagnostics import DIAGNOSTICS_ACTIVITIES
from nv_config_manager_workflows.activities.hardware_validation import (
    HARDWARE_VALIDATION_ACTIVITIES,
)
from nv_config_manager_workflows.activities.hello_world import HELLO_WORLD_ACTIVITIES
from nv_config_manager_workflows.activities.ib_guid_discovery import (
    IB_GUID_DISCOVERY_ACTIVITIES,
)
from nv_config_manager_workflows.activities.ib_pkey import IB_PKEY_ACTIVITIES
from nv_config_manager_workflows.activities.lock import LOCK_ACTIVITIES
from nv_config_manager_workflows.activities.nats import NATS_ACTIVITIES
from nv_config_manager_workflows.activities.nvlinkswitch_firmware import (
    NVLINKSWITCH_FIRMWARE_ACTIVITIES,
)
from nv_config_manager_workflows.activities.os import OS_ACTIVITIES
from nv_config_manager_workflows.activities.render import RENDER_ACTIVITIES
from nv_config_manager_workflows.activities.slack import SLACK_ACTIVITIES
from nv_config_manager_workflows.activities.ticketing import TICKETING_ACTIVITIES
from nv_config_manager_workflows.activities.ufm import UFM_ACTIVITIES
from nv_config_manager_workflows.registration.builtin import BUILTIN_PLUGIN_NAME, builtin_plugin
from nv_config_manager_workflows.registration.contract import activity_name
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

    def test_it_contributes_every_package_owned_activity(self) -> None:
        descriptor = builtin_plugin()

        assert descriptor.workflows == ()
        assert descriptor.activities == BUILTIN_ACTIVITIES
        assert descriptor.schedulers == ()

    def test_its_version_is_left_to_the_installed_distribution(self) -> None:
        assert builtin_plugin().version is None

    def test_it_passes_the_checks_every_plugin_passes(self) -> None:
        validate_plugins({BUILTIN_PLUGIN_NAME: builtin_plugin()})

    def test_a_registry_built_from_it_alone_reports_it(self) -> None:
        registry = WorkflowRegistry.build({BUILTIN_PLUGIN_NAME: builtin_plugin()})

        assert registry.all_workflows == []
        assert registry.all_activities == list(BUILTIN_ACTIVITIES)
        assert [info.name for info in registry.plugin_diagnostics] == [BUILTIN_PLUGIN_NAME]


def test_core_domain_catalogs_are_unique_and_complete() -> None:
    """Each core tuple owns its intended activity functions exactly once."""
    core_catalogs = (
        HELLO_WORLD_ACTIVITIES,
        CONFIG_ACTIVITIES,
        NATS_ACTIVITIES,
        SLACK_ACTIVITIES,
    )
    core_activities = tuple(activity for catalog in core_catalogs for activity in catalog)

    assert all(
        len(catalog) == len({id(activity) for activity in catalog}) for catalog in core_catalogs
    )
    assert {activity_name(activity) for activity in core_activities} == {
        "hello_world_activity",
        "hello_world_prompt_activity",
        "hello_world_reject_activity",
        "get_ui_base_url",
        "publish_nats",
        "send_slack_message",
    }


def test_builtin_catalog_contains_121_unique_named_activity_objects() -> None:
    """Every package domain contributes exactly one collision-free catalog entry."""
    domain_activities = (
        *CONFIG_ACTIVITIES,
        *DCIM_ACTIVITIES,
        *DEVICE_ACTIVITIES,
        *HELLO_WORLD_ACTIVITIES,
        *IB_GUID_DISCOVERY_ACTIVITIES,
        *IB_PKEY_ACTIVITIES,
        *LOCK_ACTIVITIES,
        *NATS_ACTIVITIES,
        *SLACK_ACTIVITIES,
        *UFM_ACTIVITIES,
        *BACKUP_ACTIVITIES,
        *DEPLOY_ACTIVITIES,
        *RENDER_ACTIVITIES,
        *OS_ACTIVITIES,
        *NVLINKSWITCH_FIRMWARE_ACTIVITIES,
        *CABLE_VALIDATION_ACTIVITIES,
        *BMC_ACTIVITIES,
        *HARDWARE_VALIDATION_ACTIVITIES,
        *DEVICE_PASSWORD_ROTATION_ACTIVITIES,
        *DIAGNOSTICS_ACTIVITIES,
        *TICKETING_ACTIVITIES,
    )

    assert set(BUILTIN_ACTIVITIES) == set(domain_activities)
    assert len(BUILTIN_ACTIVITIES) == 121
    assert len({id(activity) for activity in BUILTIN_ACTIVITIES}) == len(BUILTIN_ACTIVITIES)
    names = [activity_name(activity) for activity in BUILTIN_ACTIVITIES]
    assert None not in names
    assert len(set(names)) == len(names)


def test_deployment_domain_catalogs_are_immutable_and_collision_free() -> None:
    """Deployment leaf modules expose tuples ready for activity ownership."""
    catalogs = (
        BACKUP_ACTIVITIES,
        DEPLOY_ACTIVITIES,
        RENDER_ACTIVITIES,
        OS_ACTIVITIES,
        NVLINKSWITCH_FIRMWARE_ACTIVITIES,
    )
    activities = tuple(item for catalog in catalogs for item in catalog)

    assert all(isinstance(catalog, tuple) for catalog in catalogs)
    assert len(activities) == len({id(item) for item in activities})
    assert {activity_name(item) for item in (*BACKUP_ACTIVITIES, *DEPLOY_ACTIVITIES)} == {
        "apply_approved_configuration",
        "load_intended_configuration",
        "load_partial_configuration",
        "load_running_configuration",
        "perform_candidate_diff",
        "persist_config_backup",
        "record_backup_config_manager_plugin",
        "validate_config_diff",
        "wait_for_tenant_render",
    }
    assert {activity_name(item) for item in RENDER_ACTIVITIES} == {
        "execute_render",
    }
    assert {activity_name(item) for item in OS_ACTIVITIES} == {
        "cleanup_mlnx_os",
        "download_mlnx_os",
        "execute_ztp",
        "get_current_os",
        "get_mlnx_os_version",
        "get_os_image_versions",
        "install_mlnx_os",
        "poll_image",
        "poll_ztp_status",
        "reload_mlnx_os",
        "update_intended_os_image",
        "validate_rendered_image_change",
        "wait_reboot",
    }
    assert {activity_name(item) for item in NVLINKSWITCH_FIRMWARE_ACTIVITIES} == {
        "compare_running_desired",
        "get_running_firmware",
        "reboot_device",
        "update_device_context",
        "validate_render_targets",
        "validate_target_files",
    }


def test_device_operation_catalogs_are_immutable_and_collision_free() -> None:
    """Device-operation catalogs contain all 26 activities exactly once."""
    catalogs = (
        CABLE_VALIDATION_ACTIVITIES,
        BMC_ACTIVITIES,
        HARDWARE_VALIDATION_ACTIVITIES,
        DEVICE_PASSWORD_ROTATION_ACTIVITIES,
    )
    activities = tuple(item for catalog in catalogs for item in catalog)

    assert all(isinstance(catalog, tuple) for catalog in catalogs)
    assert len(activities) == 26
    assert len(activities) == len({id(item) for item in activities})
    assert {activity_name(item) for item in activities} == {
        "create_consolidated_excel_export",
        "create_excel_export",
        "decorate_result",
        "discover_redfish_hosts",
        "factory_reset_bmc",
        "format_device_validation_result",
        "format_password_rotation_results",
        "format_results",
        "get_dpu_details",
        "get_password_mappings",
        "get_platform",
        "get_platform_environment_fan",
        "get_platform_environment_led",
        "get_platform_environment_psu",
        "get_platform_environment_voltage",
        "get_platform_inventory",
        "get_server_details",
        "populate_redfish_macs",
        "power_on_host",
        "set_redfish_password",
        "update_cable_statuses",
        "update_dpu_data",
        "validate_device_neighbors",
        "validate_password_diff",
        "validate_platform_support",
        "validate_rendered_password_change",
    }


def test_diagnostics_domain_catalogs_are_immutable_and_collision_free() -> None:
    """Diagnostics catalogs contain the final six built-in activities exactly once."""
    activities = (*DIAGNOSTICS_ACTIVITIES, *TICKETING_ACTIVITIES)

    assert isinstance(DIAGNOSTICS_ACTIVITIES, tuple)
    assert isinstance(TICKETING_ACTIVITIES, tuple)
    assert len(activities) == 6
    assert len(activities) == len({id(item) for item in activities})
    assert {activity_name(item) for item in activities} == {
        "add_ticket_comment",
        "collect_tech_support_bundle",
        "run_diagnostic_commands",
        "upload_attachment",
        "upload_tech_support_from_redis",
        "validate_ticket",
    }


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
