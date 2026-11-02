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
"""Activities that manage a workflow's distributed lock."""

from nv_config_manager_workflows.activities.lock.activities import (
    acquire_workflow_lock,
    release_workflow_lock,
    renew_workflow_lock,
)
from nv_config_manager_workflows.activities.lock.models import (
    AcquireWorkflowLockInput,
    ReleaseWorkflowLockInput,
    RenewWorkflowLockInput,
)

LOCK_ACTIVITIES = (
    acquire_workflow_lock,
    renew_workflow_lock,
    release_workflow_lock,
)

__all__ = [
    "LOCK_ACTIVITIES",
    "AcquireWorkflowLockInput",
    "ReleaseWorkflowLockInput",
    "RenewWorkflowLockInput",
    "acquire_workflow_lock",
    "release_workflow_lock",
    "renew_workflow_lock",
]
