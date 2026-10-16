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
"""Tests for reusable workflow configuration activities and helpers."""

import pytest

from nv_config_manager_workflows import runtime as runtime_module
from nv_config_manager_workflows.activities.config import get_ui_base_url
from nv_config_manager_workflows.runtime import (
    UIBaseURLNotConfiguredError,
    configure_ui_base_url,
)


def test_get_ui_base_url_returns_runtime_value() -> None:
    """The activity delegates to the configured package runtime provider."""
    configure_ui_base_url(lambda: "https://config-manager.example")

    assert get_ui_base_url() == "https://config-manager.example"


def test_get_ui_base_url_fails_clearly_before_runtime_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing worker startup configuration is permanent and actionable."""
    monkeypatch.setattr(runtime_module, "_ui_base_url_provider", runtime_module._UNSET)

    with pytest.raises(UIBaseURLNotConfiguredError, match="configure_ui_base_url") as raised:
        get_ui_base_url()

    assert raised.value.non_retryable is True
