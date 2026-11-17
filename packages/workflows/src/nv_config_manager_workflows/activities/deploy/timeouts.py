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
"""Activity timeouts of the deploy activities, by device platform.

The diff activity runs under one minute and the apply activity under five for every
platform but IOS XR. An IOS XR diff or apply loads the whole configuration onto the
candidate with ``<copy-config>``, which took 36 s on a lab router (23 s for the whole
diff, 50 s for the whole apply) and, once, over two minutes; the activity must outlast
the client's own time budgets (``clients.device.cisco_xr.connection``) so the client ends
its step, and releases the candidate lock, before Temporal times the activity out and
retries it beside a session that may still hold that lock.

Budgets of the IOS XR client, in seconds:

- diff: connect, lock, discard, copy and diff share 180, leaving 60 of the 4 minutes
  for cleanup;
- apply: the load (same steps) 180, the commit call 120, and the post-commit login, copy
  and diff inside the 300 s rollback timer, plus 30 for the confirming commit: about 630
  at most against 12 minutes.
"""

from datetime import timedelta

from nv_config_manager_dcim.workflow_models import NetworkDeviceData, Platform

_DEFAULT_DIFF_TIMEOUT = timedelta(minutes=1)
_DEFAULT_APPLY_TIMEOUT = timedelta(minutes=5)
_IOS_XR_DIFF_TIMEOUT = timedelta(minutes=4)
_IOS_XR_APPLY_TIMEOUT = timedelta(minutes=12)


def diff_activity_timeout(device: NetworkDeviceData) -> timedelta:
    """Start-to-close timeout of the ``perform_candidate_diff`` activity for ``device``."""
    if device.platform == Platform.CISCO_IOS_XR:
        return _IOS_XR_DIFF_TIMEOUT
    return _DEFAULT_DIFF_TIMEOUT


def apply_activity_timeout(device: NetworkDeviceData) -> timedelta:
    """Start-to-close timeout of the ``apply_approved_configuration`` activity for ``device``."""
    if device.platform == Platform.CISCO_IOS_XR:
        return _IOS_XR_APPLY_TIMEOUT
    return _DEFAULT_APPLY_TIMEOUT
