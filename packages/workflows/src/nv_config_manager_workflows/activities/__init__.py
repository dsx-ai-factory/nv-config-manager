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
"""Reusable activities shared across workflow families."""

from collections.abc import Callable
from typing import Any

from nv_config_manager_workflows.activities.lock import (
    acquire_workflow_lock,
    release_workflow_lock,
    renew_workflow_lock,
)
from nv_config_manager_workflows.activities.tech_support import (
    TECH_SUPPORT_BUNDLE_TTL,
    tech_support_bundle_key,
)

REGISTERED_COMMON_ACTIVITIES: list[Callable[..., Any]] = [
    acquire_workflow_lock,
    renew_workflow_lock,
    release_workflow_lock,
]

__all__ = [
    "REGISTERED_COMMON_ACTIVITIES",
    "TECH_SUPPORT_BUNDLE_TTL",
    "acquire_workflow_lock",
    "release_workflow_lock",
    "renew_workflow_lock",
    "tech_support_bundle_key",
]
