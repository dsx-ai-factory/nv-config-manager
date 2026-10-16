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
"""Stable Redis contracts for workflow tech-support bundles."""

from datetime import timedelta
from typing import Final

TECH_SUPPORT_BUNDLE_KEY_PREFIX: Final = "tech_support"
TECH_SUPPORT_BUNDLE_TTL: Final = timedelta(hours=24)


def tech_support_key(workflow_id: str, device_name: str) -> str:
    """Build the storage key for a workflow's device support bundle."""
    return f"{TECH_SUPPORT_BUNDLE_KEY_PREFIX}:{workflow_id}:{device_name}"
