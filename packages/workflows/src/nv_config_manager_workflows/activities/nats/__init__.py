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
"""Activities for publishing workflow results to NATS."""

from nv_config_manager_workflows.activities.nats.activities import publish_nats
from nv_config_manager_workflows.activities.nats.models import (
    ARCHIVE_SUBJECT,
    PublishNatsInput,
)

NATS_ACTIVITIES = (publish_nats,)

__all__ = [
    "ARCHIVE_SUBJECT",
    "NATS_ACTIVITIES",
    "PublishNatsInput",
    "publish_nats",
]
