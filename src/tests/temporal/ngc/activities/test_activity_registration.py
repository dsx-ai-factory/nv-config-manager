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
"""Ensure all activities are registered."""

import inspect
from collections import Counter
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from types import FunctionType
from typing import Any, cast

from nv_config_manager.temporal.ngc import activities
from nv_config_manager.temporal.ngc.activities import REGISTERED_ACTIVITIES
from nv_config_manager_workflows.activities.backup import BACKUP_ACTIVITIES
from nv_config_manager_workflows.activities.bmc import BMC_ACTIVITIES
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
from nv_config_manager_workflows.activities.ib_dcim import IB_DCIM_ACTIVITIES
from nv_config_manager_workflows.activities.ib_guid_discovery import (
    IB_GUID_DISCOVERY_ACTIVITIES,
)
from nv_config_manager_workflows.activities.ib_pkey import IB_PKEY_ACTIVITIES
from nv_config_manager_workflows.activities.nats import NATS_ACTIVITIES
from nv_config_manager_workflows.activities.nvlinkswitch_firmware import (
    NVLINKSWITCH_FIRMWARE_ACTIVITIES,
)
from nv_config_manager_workflows.activities.os import OS_ACTIVITIES
from nv_config_manager_workflows.activities.render import RENDER_ACTIVITIES
from nv_config_manager_workflows.activities.slack import SLACK_ACTIVITIES
from nv_config_manager_workflows.activities.ticketing import TICKETING_ACTIVITIES
from nv_config_manager_workflows.activities.ufm import UFM_ACTIVITIES
from nv_config_manager_workflows.registration import activity_name

_EXPECTED_IB_DCIM_ACTIVITY_NAMES = [
    "record_ib_pkey_in_dcim",
    "record_ib_pkey_in_nautobot",
    "create_partition_in_dcim",
    "create_partition_in_nautobot",
    "resolve_interface_guids",
    "resolve_guids_to_interfaces",
    "resolve_ib_context",
    "resolve_ib_context_for_add",
    "resolve_ib_site_for_host",
    "record_pkey_assignments",
    "fetch_pkey_assignments",
    "sync_pkey_assignments",
    "remove_pkey_assignments",
    "cleanup_empty_pkey_partition",
]
_EXPECTED_DCIM_ACTIVITY_NAMES = [
    "get_host_data_by_macs",
    "get_host_data_by_names",
    "get_host_devices",
    "get_network_devices",
    "get_host_device",
    "get_network_device",
    "get_available_route_distinguishers",
    "provision_vrf",
    "get_switch_port_by_remote_mac_address",
    "get_vrfs_by_overlay_id",
    "delete_vrf",
    "delete_overlay",
    "get_device_vrfs",
    "assign_vrf_to_device",
    "get_device_interfaces",
    "assign_vrf_to_interface",
    "reconcile_spx_overlay_assignments",
    "remove_unmapped_device_vrfs",
    "check_recorded_config_drift",
]
_EXPECTED_DEVICE_ACTIVITY_NAMES = [
    "get_device_intended_neighbors",
    "get_device_actual_neighbors",
    "get_device_mac_table",
    "get_device_arp_table",
    "validate_hostname",
    "load_neighbor_data_by_switch_port",
]
_EXPECTED_IB_PKEY_ACTIVITY_NAMES = [
    "validate_pkey_available",
    "create_pkey_on_ufm",
    "verify_pkey_created",
    "add_guids_to_pkey",
    "verify_pkey_members",
    "remove_guids_from_pkey",
    "set_pkey_members",
    "verify_pkey_members_absent",
    "fetch_pkey_members",
]
_EXPECTED_IB_GUID_DISCOVERY_ACTIVITY_NAMES = [
    "discover_ib_port_guids",
    "sync_ib_guid_on_interface",
]
_EXPECTED_BACKUP_ACTIVITY_NAMES = [
    "load_running_configuration",
    "persist_config_backup",
    "record_backup_config_manager_plugin",
]
_EXPECTED_DEPLOY_ACTIVITY_NAMES = [
    "load_intended_configuration",
    "load_partial_configuration",
    "perform_candidate_diff",
    "apply_approved_configuration",
    "validate_config_diff",
    "wait_for_tenant_render",
]
_EXPECTED_RENDER_ACTIVITY_NAMES = [
    "execute_render",
    "validate_rendered_image_change",
    "validate_rendered_password_change",
]
_EXPECTED_OS_ACTIVITY_NAMES = [
    "get_current_os",
    "get_os_image_versions",
    "update_intended_os_image",
    "execute_ztp",
    "poll_image",
    "poll_ztp_status",
    "wait_reboot",
    "get_mlnx_os_version",
    "download_mlnx_os",
    "install_mlnx_os",
    "reload_mlnx_os",
    "cleanup_mlnx_os",
]
_EXPECTED_NVLINKSWITCH_FIRMWARE_ACTIVITY_NAMES = [
    "get_running_firmware",
    "compare_running_desired",
    "update_device_context",
    "validate_render_targets",
    "validate_target_files",
    "reboot_device",
]


def _load_all_activity_methods() -> list[FunctionType]:
    """Load all workflow classes from the workflows module."""
    activity_methods: list[FunctionType] = []
    activity_source = inspect.getsourcefile(activities)
    assert activity_source is not None
    activity_path = Path(activity_source).parent
    for path in activity_path.glob("*.py"):
        if path.stem == "__init__":
            continue
        module = import_module(f"{activities.__name__}.{path.stem}")

        for _, obj in inspect.getmembers(module, inspect.isfunction):
            # Risky if temporal SDK changes, but not finding a better method
            # for identifying functions with @activity.defn decorator
            if hasattr(obj, "__temporal_activity_definition"):
                activity_methods.append(obj)

    return activity_methods


def test_activity_registration() -> None:
    """Test that all activities are registered."""
    activity_methods = _load_all_activity_methods()
    for activity_method in activity_methods:
        assert activity_method in REGISTERED_ACTIVITIES, (
            f"Activity {activity_method.__name__} not registered"
        )


def test_service_root_reexports_package_activity_catalogs() -> None:
    """Removing package activities from registration does not remove compatibility imports."""
    catalogs = {
        "BACKUP_ACTIVITIES": BACKUP_ACTIVITIES,
        "CONFIG_ACTIVITIES": CONFIG_ACTIVITIES,
        "DEVICE_ACTIVITIES": DEVICE_ACTIVITIES,
        "NATS_ACTIVITIES": NATS_ACTIVITIES,
        "OS_ACTIVITIES": OS_ACTIVITIES,
        "NVLINKSWITCH_FIRMWARE_ACTIVITIES": NVLINKSWITCH_FIRMWARE_ACTIVITIES,
        "RENDER_ACTIVITIES": RENDER_ACTIVITIES,
        "SLACK_ACTIVITIES": SLACK_ACTIVITIES,
        "DCIM_ACTIVITIES": DCIM_ACTIVITIES,
        "DEPLOY_ACTIVITIES": DEPLOY_ACTIVITIES,
        "UFM_ACTIVITIES": UFM_ACTIVITIES,
        "IB_PKEY_ACTIVITIES": IB_PKEY_ACTIVITIES,
        "IB_DCIM_ACTIVITIES": IB_DCIM_ACTIVITIES,
        "IB_GUID_DISCOVERY_ACTIVITIES": IB_GUID_DISCOVERY_ACTIVITIES,
        "CABLE_VALIDATION_ACTIVITIES": CABLE_VALIDATION_ACTIVITIES,
        "BMC_ACTIVITIES": BMC_ACTIVITIES,
        "HARDWARE_VALIDATION_ACTIVITIES": HARDWARE_VALIDATION_ACTIVITIES,
        "DEVICE_PASSWORD_ROTATION_ACTIVITIES": DEVICE_PASSWORD_ROTATION_ACTIVITIES,
        "DIAGNOSTICS_ACTIVITIES": DIAGNOSTICS_ACTIVITIES,
        "TICKETING_ACTIVITIES": TICKETING_ACTIVITIES,
    }

    for name, catalog in catalogs.items():
        assert getattr(activities, name) is catalog


def test_ib_dcim_activities_are_registered_once() -> None:
    """The service catalog contains every package IB/DCIM activity exactly once."""
    assert all(REGISTERED_ACTIVITIES.count(item) == 1 for item in IB_DCIM_ACTIVITIES)


def test_backup_and_deploy_activities_are_registered_once() -> None:
    """The service catalog contains both moved configuration slices exactly once."""
    assert all(REGISTERED_ACTIVITIES.count(item) == 1 for item in BACKUP_ACTIVITIES)
    assert all(REGISTERED_ACTIVITIES.count(item) == 1 for item in DEPLOY_ACTIVITIES)
    assert [activity_name(item) for item in BACKUP_ACTIVITIES] == _EXPECTED_BACKUP_ACTIVITY_NAMES
    assert [activity_name(item) for item in DEPLOY_ACTIVITIES] == _EXPECTED_DEPLOY_ACTIVITY_NAMES


def test_render_activities_are_registered_once() -> None:
    """The service catalog contains the moved Render slice exactly once."""
    assert all(REGISTERED_ACTIVITIES.count(item) == 1 for item in RENDER_ACTIVITIES)
    assert [activity_name(item) for item in RENDER_ACTIVITIES] == _EXPECTED_RENDER_ACTIVITY_NAMES


def test_os_activities_are_registered_once() -> None:
    """The service catalog contains the moved OS slice exactly once."""
    assert all(REGISTERED_ACTIVITIES.count(item) == 1 for item in OS_ACTIVITIES)
    assert [activity_name(item) for item in OS_ACTIVITIES] == _EXPECTED_OS_ACTIVITY_NAMES


def test_nvlinkswitch_firmware_activities_are_registered_once() -> None:
    """The service catalog contains the moved NVLink firmware slice exactly once."""
    assert all(REGISTERED_ACTIVITIES.count(item) == 1 for item in NVLINKSWITCH_FIRMWARE_ACTIVITIES)
    assert [activity_name(item) for item in NVLINKSWITCH_FIRMWARE_ACTIVITIES] == (
        _EXPECTED_NVLINKSWITCH_FIRMWARE_ACTIVITY_NAMES
    )


def test_device_operation_activities_are_registered_once() -> None:
    """The service catalog contains all package-owned device activities exactly once."""
    device_operation_activities = (
        *CABLE_VALIDATION_ACTIVITIES,
        *BMC_ACTIVITIES,
        *HARDWARE_VALIDATION_ACTIVITIES,
        *DEVICE_PASSWORD_ROTATION_ACTIVITIES,
    )

    assert len(device_operation_activities) == 25
    assert all(REGISTERED_ACTIVITIES.count(item) == 1 for item in device_operation_activities)


def test_diagnostics_activities_are_registered_once() -> None:
    """The service compatibility view contains all final package activities once."""
    diagnostics_activities = (*DIAGNOSTICS_ACTIVITIES, *TICKETING_ACTIVITIES)

    assert len(diagnostics_activities) == 6
    assert all(REGISTERED_ACTIVITIES.count(item) == 1 for item in diagnostics_activities)


def test_dcim_activities_are_registered_once() -> None:
    """The service catalog contains every package DCIM activity exactly once."""
    assert all(REGISTERED_ACTIVITIES.count(item) == 1 for item in DCIM_ACTIVITIES)
    assert [activity_name(item) for item in DCIM_ACTIVITIES] == _EXPECTED_DCIM_ACTIVITY_NAMES
    assert all(REGISTERED_ACTIVITIES.count(item) == 1 for item in IB_DCIM_ACTIVITIES)
    assert [activity_name(item) for item in IB_DCIM_ACTIVITIES] == (
        _EXPECTED_IB_DCIM_ACTIVITY_NAMES
    )


def test_device_activities_are_registered_once() -> None:
    """The service catalog contains every package device activity exactly once."""
    assert all(REGISTERED_ACTIVITIES.count(item) == 1 for item in DEVICE_ACTIVITIES)
    assert [activity_name(item) for item in DEVICE_ACTIVITIES] == _EXPECTED_DEVICE_ACTIVITY_NAMES


def test_ufm_activities_are_registered_once() -> None:
    """The service catalog contains the package UFM slice exactly once."""
    assert all(REGISTERED_ACTIVITIES.count(item) == 1 for item in UFM_ACTIVITIES)
    assert [activity_name(item) for item in UFM_ACTIVITIES] == ["get_ib_ports"]


def test_ib_pkey_activities_are_registered_once() -> None:
    """The service catalog contains every package PKey activity exactly once."""
    assert all(REGISTERED_ACTIVITIES.count(item) == 1 for item in IB_PKEY_ACTIVITIES)
    assert [activity_name(item) for item in IB_PKEY_ACTIVITIES] == (
        _EXPECTED_IB_PKEY_ACTIVITY_NAMES
    )


def test_ib_guid_discovery_activities_are_registered_once() -> None:
    """The service catalog contains the package IB GUID slice exactly once."""
    assert all(REGISTERED_ACTIVITIES.count(item) == 1 for item in IB_GUID_DISCOVERY_ACTIVITIES)
    assert [activity_name(item) for item in IB_GUID_DISCOVERY_ACTIVITIES] == (
        _EXPECTED_IB_GUID_DISCOVERY_ACTIVITY_NAMES
    )


def test_service_package_root_reexports_package_ib_dcim_activities() -> None:
    """Existing service-root imports resolve to the package function objects."""
    for activity_method in IB_DCIM_ACTIVITIES:
        assert getattr(activities, activity_method.__name__) is activity_method


def test_service_package_root_reexports_package_backup_and_deploy_activities() -> None:
    """Existing service-root imports resolve to the moved configuration activities."""
    assert activities.BACKUP_ACTIVITIES is BACKUP_ACTIVITIES
    assert activities.DEPLOY_ACTIVITIES is DEPLOY_ACTIVITIES
    for activity_method in (*BACKUP_ACTIVITIES, *DEPLOY_ACTIVITIES):
        assert getattr(activities, activity_method.__name__) is activity_method


def test_service_package_root_reexports_package_render_activities() -> None:
    """Existing service-root imports resolve to package Render activities."""
    assert activities.RENDER_ACTIVITIES is RENDER_ACTIVITIES
    for activity_method in RENDER_ACTIVITIES:
        assert getattr(activities, activity_method.__name__) is activity_method


def test_service_package_root_reexports_package_os_activities() -> None:
    """Existing service-root imports resolve to package OS activities."""
    assert activities.OS_ACTIVITIES is OS_ACTIVITIES
    for activity_method in OS_ACTIVITIES:
        assert getattr(activities, activity_method.__name__) is activity_method


def test_service_package_root_reexports_package_dcim_activities() -> None:
    """Existing service-root DCIM imports resolve to package function objects."""
    assert activities.DCIM_ACTIVITIES is DCIM_ACTIVITIES
    for activity_method in DCIM_ACTIVITIES:
        assert getattr(activities, activity_method.__name__) is activity_method


def test_service_package_root_reexports_package_device_activities() -> None:
    """Existing service-root device imports resolve to package function objects."""
    assert activities.DEVICE_ACTIVITIES is DEVICE_ACTIVITIES
    for activity_method in DEVICE_ACTIVITIES:
        assert getattr(activities, activity_method.__name__) is activity_method


def test_service_package_root_reexports_package_ufm_activities() -> None:
    """Existing service-root UFM imports resolve to package function objects."""
    assert activities.UFM_ACTIVITIES is UFM_ACTIVITIES
    for activity_method in UFM_ACTIVITIES:
        assert getattr(activities, activity_method.__name__) is activity_method


def test_service_package_root_reexports_package_ib_pkey_activities() -> None:
    """Existing service-root PKey imports resolve to package function objects."""
    assert activities.IB_PKEY_ACTIVITIES is IB_PKEY_ACTIVITIES
    for activity_method in IB_PKEY_ACTIVITIES:
        assert getattr(activities, activity_method.__name__) is activity_method


def test_service_package_root_reexports_package_ib_guid_discovery_activities() -> None:
    """Existing service-root IB GUID imports resolve to package function objects."""
    assert activities.IB_GUID_DISCOVERY_ACTIVITIES is IB_GUID_DISCOVERY_ACTIVITIES
    for activity_method in IB_GUID_DISCOVERY_ACTIVITIES:
        assert getattr(activities, activity_method.__name__) is activity_method


def test_registered_temporal_activity_names_are_unique() -> None:
    """Modern and legacy names are present without duplicate worker handlers."""
    names = [activity_name(cast(Callable[..., Any], item)) for item in REGISTERED_ACTIVITIES]
    counts = Counter(names)

    assert None not in counts
    assert {name: count for name, count in counts.items() if count > 1} == {}
    assert "create_partition_in_nautobot" in counts
    assert "record_ib_pkey_in_nautobot" in counts
