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
"""Tests for configuration utilities."""

import pytest

from nv_config_manager.temporal.ngc.activities.config import build_workflow_url, get_ui_base_url
from nv_config_manager_workflows import runtime as runtime_module
from nv_config_manager_workflows.runtime import UIBaseURLNotConfiguredError


def test_get_ui_base_url_reads_startup_configuration() -> None:
    assert get_ui_base_url() == "https://temporal-ui.example.com"


def test_get_ui_base_url_fails_clearly_before_configuration(monkeypatch) -> None:
    monkeypatch.setattr(runtime_module, "_ui_base_url", runtime_module._UNSET)

    with pytest.raises(UIBaseURLNotConfiguredError, match="configure_ui_base_url"):
        get_ui_base_url()


class TestBuildWorkflowUrl:
    """Tests for build_workflow_url."""

    def test_bare_hostname(self) -> None:
        assert (
            build_workflow_url("temporal.example.com", "wf-123")
            == "https://temporal.example.com/workflows/wf-123"
        )

    def test_https_scheme(self) -> None:
        assert (
            build_workflow_url("https://temporal.example.com", "wf-123")
            == "https://temporal.example.com/workflows/wf-123"
        )

    def test_http_scheme(self) -> None:
        assert (
            build_workflow_url("http://temporal.example.com", "wf-123")
            == "http://temporal.example.com/workflows/wf-123"
        )

    def test_trailing_slash_stripped(self) -> None:
        assert (
            build_workflow_url("https://temporal.example.com/", "wf-123")
            == "https://temporal.example.com/workflows/wf-123"
        )

    def test_multiple_trailing_slashes_stripped(self) -> None:
        assert (
            build_workflow_url("https://temporal.example.com///", "wf-123")
            == "https://temporal.example.com/workflows/wf-123"
        )

    def test_bare_hostname_with_trailing_slash(self) -> None:
        assert (
            build_workflow_url("temporal.example.com/", "wf-123")
            == "https://temporal.example.com/workflows/wf-123"
        )

    def test_with_subpath(self) -> None:
        assert (
            build_workflow_url("https://temporal.example.com/ui", "wf-456")
            == "https://temporal.example.com/ui/workflows/wf-456"
        )

    @pytest.mark.parametrize("workflow_id", ["abc-123", "multi-deploy-batch_0_ff01ab", ""])
    def test_various_workflow_ids(self, workflow_id: str) -> None:
        url = build_workflow_url("https://temporal.example.com", workflow_id)
        assert url == f"https://temporal.example.com/workflows/{workflow_id}"
