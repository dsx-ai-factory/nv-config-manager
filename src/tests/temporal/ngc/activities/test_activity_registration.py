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
"""Verify the NGC activity compatibility surface."""

from collections.abc import Callable
from typing import Any

from nv_config_manager.temporal.ngc import activities
from nv_config_manager.temporal.ngc.activities import REGISTERED_ACTIVITIES
from nv_config_manager_workflows.activities import builtin
from nv_config_manager_workflows.activities.hello_world import HELLO_WORLD_ACTIVITIES
from nv_config_manager_workflows.activities.lock import LOCK_ACTIVITIES

_NGC_CATALOG_NAMES = (
    "BACKUP_ACTIVITIES",
    "BMC_ACTIVITIES",
    "CABLE_VALIDATION_ACTIVITIES",
    "CONFIG_ACTIVITIES",
    "DCIM_ACTIVITIES",
    "DEPLOY_ACTIVITIES",
    "DEVICE_ACTIVITIES",
    "DEVICE_PASSWORD_ROTATION_ACTIVITIES",
    "DIAGNOSTICS_ACTIVITIES",
    "HARDWARE_VALIDATION_ACTIVITIES",
    "IB_GUID_DISCOVERY_ACTIVITIES",
    "IB_PKEY_ACTIVITIES",
    "NATS_ACTIVITIES",
    "NVLINKSWITCH_FIRMWARE_ACTIVITIES",
    "OS_ACTIVITIES",
    "RENDER_ACTIVITIES",
    "SLACK_ACTIVITIES",
    "TICKETING_ACTIVITIES",
    "UFM_ACTIVITIES",
)


def _expected_ngc_activities() -> tuple[Callable[..., Any], ...]:
    """Exclude activity domains registered by other service workers."""
    excluded = {*HELLO_WORLD_ACTIVITIES, *LOCK_ACTIVITIES}
    return tuple(activity for activity in builtin.BUILTIN_ACTIVITIES if activity not in excluded)


def test_legacy_catalog_matches_the_canonical_ngc_activity_set() -> None:
    """The compatibility snapshot contains every NGC activity exactly once."""
    expected = _expected_ngc_activities()

    assert set(REGISTERED_ACTIVITIES) == set(expected)
    assert len(REGISTERED_ACTIVITIES) == len(expected)


def test_service_root_reexports_canonical_ngc_activity_objects() -> None:
    """Legacy root imports keep resolving to the package-owned objects."""
    for catalog_name in _NGC_CATALOG_NAMES:
        assert getattr(activities, catalog_name) is getattr(builtin, catalog_name)

    for activity in _expected_ngc_activities():
        assert getattr(activities, activity.__name__) is activity
