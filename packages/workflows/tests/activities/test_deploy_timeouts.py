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
"""Tests for the per-platform activity timeouts of the diff and apply activities."""

from datetime import timedelta

import pytest
from nv_config_manager_dcim.workflow_models import NetworkDeviceData, Platform

from nv_config_manager_workflows.activities.deploy import (
    apply_activity_timeout,
    diff_activity_timeout,
)
from nv_config_manager_workflows.clients.device.cisco_xr.connection import CiscoIosXrConnection


def _device(platform: Platform) -> NetworkDeviceData:
    return NetworkDeviceData.model_construct(platform=platform)


@pytest.mark.parametrize("platform", [p for p in Platform if p != Platform.CISCO_IOS_XR])
def test_other_platforms_keep_the_default_timeouts(platform: Platform) -> None:
    assert diff_activity_timeout(_device(platform)) == timedelta(minutes=1)
    assert apply_activity_timeout(_device(platform)) == timedelta(minutes=5)


def test_ios_xr_diff_and_apply_outlast_the_clients_time_budgets() -> None:
    """The client must end its step, and unlock the candidate, before Temporal times out."""
    device = _device(Platform.CISCO_IOS_XR)
    cleanup_margin = 30.0
    client = CiscoIosXrConnection

    assert (
        client._DIFF_BUDGET_SECONDS + cleanup_margin
        <= diff_activity_timeout(device).total_seconds()
    )
    # connect and load, the commit call, then the checks inside the rollback timer
    apply_worst_case = (
        client._LOAD_BUDGET_SECONDS
        + client._COMMIT_TIMEOUT_SECONDS
        + client._COMMIT_CONFIRM_SECONDS
        + 30.0  # the confirming commit
    )
    assert apply_worst_case + cleanup_margin <= apply_activity_timeout(device).total_seconds()
