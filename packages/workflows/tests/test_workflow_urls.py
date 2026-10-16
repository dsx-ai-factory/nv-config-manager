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
"""Tests for workflow URL builders."""

import pytest

from nv_config_manager_workflows.activities.config import (
    build_workflow_url as legacy_build_workflow_url,
)
from nv_config_manager_workflows.workflow_urls import build_workflow_url


@pytest.mark.parametrize(
    ("ui_base_url", "workflow_id", "expected"),
    [
        ("temporal.example.com", "wf-123", "https://temporal.example.com/workflows/wf-123"),
        (
            "https://temporal.example.com",
            "wf-123",
            "https://temporal.example.com/workflows/wf-123",
        ),
        (
            "http://temporal.example.com",
            "wf-123",
            "http://temporal.example.com/workflows/wf-123",
        ),
        (
            "https://temporal.example.com///",
            "wf-123",
            "https://temporal.example.com/workflows/wf-123",
        ),
        (
            "temporal.example.com/",
            "wf-123",
            "https://temporal.example.com/workflows/wf-123",
        ),
        (
            "https://temporal.example.com/ui",
            "wf-456",
            "https://temporal.example.com/ui/workflows/wf-456",
        ),
        (
            "temporal.example.com///",
            "workflow: 42",
            "https://temporal.example.com/workflows/workflow: 42",
        ),
        ("https://temporal.example.com", "", "https://temporal.example.com/workflows/"),
    ],
)
def test_build_workflow_url(
    ui_base_url: str,
    workflow_id: str,
    expected: str,
) -> None:
    """URL construction preserves schemes, subpaths, and workflow identifiers."""
    assert build_workflow_url(ui_base_url, workflow_id) == expected


def test_config_activity_module_preserves_compatibility_export() -> None:
    """The former activity-module import resolves to the canonical helper."""
    assert legacy_build_workflow_url is build_workflow_url
