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
from importlib import import_module
from pathlib import Path

from nv_config_manager.temporal.ngc import activities
from nv_config_manager.temporal.ngc.activities import REGISTERED_ACTIVITIES
from nv_config_manager_workflows.activities.ib_dcim import IB_DCIM_ACTIVITIES
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


def _load_all_activity_methods():
    """Load all workflow classes from the workflows module."""
    activity_methods = []
    activity_path = Path(inspect.getsourcefile(activities)).parent
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


def test_activity_registration():
    """Test that all activities are registered."""
    activity_methods = _load_all_activity_methods()
    for activity_method in activity_methods:
        assert activity_method in REGISTERED_ACTIVITIES, (
            f"Activity {activity_method.__name__} not registered"
        )


def test_ib_dcim_activities_are_registered_once_in_stable_order() -> None:
    """The service catalog contains the package slice once without reordering it."""
    first_index = REGISTERED_ACTIVITIES.index(IB_DCIM_ACTIVITIES[0])

    assert (
        tuple(REGISTERED_ACTIVITIES[first_index : first_index + len(IB_DCIM_ACTIVITIES)])
        == IB_DCIM_ACTIVITIES
    )
    assert all(REGISTERED_ACTIVITIES.count(item) == 1 for item in IB_DCIM_ACTIVITIES)
    assert [activity_name(item) for item in IB_DCIM_ACTIVITIES] == (
        _EXPECTED_IB_DCIM_ACTIVITY_NAMES
    )


def test_service_package_root_reexports_package_ib_dcim_activities() -> None:
    """Existing service-root imports resolve to the package function objects."""
    for activity_method in IB_DCIM_ACTIVITIES:
        assert getattr(activities, activity_method.__name__) is activity_method


def test_registered_temporal_activity_names_are_unique() -> None:
    """Modern and legacy names are present without duplicate worker handlers."""
    names = [activity_name(item) for item in REGISTERED_ACTIVITIES]
    counts = Counter(names)

    assert None not in counts
    assert {name: count for name, count in counts.items() if count > 1} == {}
    assert "create_partition_in_nautobot" in counts
    assert "record_ib_pkey_in_nautobot" in counts
