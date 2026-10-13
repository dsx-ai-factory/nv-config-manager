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
"""Scheduler identity grammar shared by registry validation and schedule-ID helpers."""

import re
from typing import Final

SCHEDULER_IDENTITY_SEGMENT_PATTERN: Final = re.compile(r"[a-z][a-z0-9_-]*")
SCHEDULER_IDENTITY_PATTERN: Final = re.compile(r"[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+")
# Built-in backup schedule IDs are "backup-<device-id>"; plugin names may not use this prefix.
BACKUP_SCHEDULE_PREFIX: Final = "backup-"

__all__ = [
    "BACKUP_SCHEDULE_PREFIX",
    "SCHEDULER_IDENTITY_PATTERN",
    "SCHEDULER_IDENTITY_SEGMENT_PATTERN",
]
