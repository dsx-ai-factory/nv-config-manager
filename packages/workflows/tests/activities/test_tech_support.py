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
"""Tests for the shared tech-support Redis storage contract."""

from datetime import timedelta

from nv_config_manager_workflows.activities.tech_support import (
    TECH_SUPPORT_BUNDLE_TTL,
    tech_support_bundle_key,
)


def test_tech_support_bundle_key_preserves_existing_format() -> None:
    assert tech_support_bundle_key("workflow-id", "switch-01") == (
        "tech_support:workflow-id:switch-01"
    )


def test_tech_support_bundle_ttl_preserves_existing_duration() -> None:
    assert TECH_SUPPORT_BUNDLE_TTL == timedelta(hours=24)
