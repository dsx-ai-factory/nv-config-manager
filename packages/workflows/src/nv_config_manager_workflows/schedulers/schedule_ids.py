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
"""Temporal schedule-ID helpers for workflow schedulers.

Each scheduler owns the ``<identity>:<key>`` schedule-ID namespace, where
``identity`` is the scheduler's registered identity and ``key`` identifies one
schedule within it. Schedulers should build IDs with :func:`schedule_id` and
only list, update, or delete schedules for which :func:`owns_schedule_id`
returns ``True``.
"""

from typing import Final

from nv_config_manager_workflows.scheduler_identity import SCHEDULER_IDENTITY_PATTERN

SCHEDULE_ID_SEPARATOR: Final = ":"


def schedule_id(identity: str, key: str) -> str:
    """Return the Temporal schedule ID ``<identity>:<key>`` owned by one scheduler."""
    if SCHEDULER_IDENTITY_PATTERN.fullmatch(identity) is None:
        raise ValueError(
            f"Scheduler identity {identity!r} must match "
            f"{SCHEDULER_IDENTITY_PATTERN.pattern!r} (for example 'acme.cleanup')"
        )
    if not key:
        raise ValueError(f"Schedule key for scheduler {identity!r} must not be empty")
    if "/" in key:
        raise ValueError(
            f"Schedule key {key!r} for scheduler {identity!r} must not contain '/': "
            "Temporal's HTTP API cannot address schedule IDs containing '/'"
        )
    return f"{identity}{SCHEDULE_ID_SEPARATOR}{key}"


def owns_schedule_id(identity: str, schedule_id: str) -> bool:
    """Return whether ``schedule_id`` is in ``identity``'s ``<identity>:`` namespace."""
    prefix = f"{identity}{SCHEDULE_ID_SEPARATOR}"
    return schedule_id.startswith(prefix) and len(schedule_id) > len(prefix)


__all__ = ["SCHEDULE_ID_SEPARATOR", "owns_schedule_id", "schedule_id"]
