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
"""Tests for the shared tech-support bundle storage contract."""

from datetime import timedelta

import pytest

from nv_config_manager_workflows.tech_support import (
    TECH_SUPPORT_BUNDLE_KEY_PREFIX,
    TECH_SUPPORT_BUNDLE_TTL,
    tech_support_key,
)


@pytest.mark.parametrize(
    ("workflow_id", "device_name", "expected"),
    [
        ("workflow-1", "switch-1", "tech_support:workflow-1:switch-1"),
        ("", "", "tech_support::"),
        ("workflow: 42", "switch: 01", "tech_support:workflow: 42:switch: 01"),
    ],
)
def test_tech_support_key_preserves_existing_format(
    workflow_id: str,
    device_name: str,
    expected: str,
) -> None:
    """Key construction adds no escaping, normalization, or validation."""
    assert tech_support_key(workflow_id, device_name) == expected


def test_tech_support_bundle_storage_constants_are_stable() -> None:
    """Redis entries retain their existing namespace and 24-hour lifetime."""
    assert TECH_SUPPORT_BUNDLE_KEY_PREFIX == "tech_support"
    assert TECH_SUPPORT_BUNDLE_TTL == timedelta(hours=24)
