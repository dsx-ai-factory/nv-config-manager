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
"""Deprecated service import path for the reusable NATS activity."""

# isort: off
from nv_config_manager_workflows.activities.nats import (
    ARCHIVE_SUBJECT as ARCHIVE_SUBJECT,
    NATS_ACTIVITIES as NATS_ACTIVITIES,
    PublishNatsInput as PublishNatsInput,
    publish_nats as publish_nats,
)
# isort: on

__all__ = [
    "ARCHIVE_SUBJECT",
    "NATS_ACTIVITIES",
    "PublishNatsInput",
    "publish_nats",
]
