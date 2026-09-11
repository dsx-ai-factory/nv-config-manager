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
"""Tests for process-local workflow runtime configuration."""

from typing import cast
from unittest.mock import Mock

import pytest
from redis.asyncio import Redis

from nv_config_manager_workflows import lock as lock_module
from nv_config_manager_workflows import runtime as runtime_module
from nv_config_manager_workflows.runtime import (
    NatsNotConfiguredError,
    SlackNotConfiguredError,
    UIBaseURLNotConfiguredError,
    configure_nats,
    configure_runtime,
    configure_slack,
    configure_ui_base_url,
    get_nats_configuration,
    get_slack_configuration,
    get_ui_base_url,
)


@pytest.fixture(autouse=True)
def unconfigured_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test with no workflow resources configured."""
    monkeypatch.setattr(runtime_module, "_nats", runtime_module._UNSET)
    monkeypatch.setattr(runtime_module, "_slack", runtime_module._UNSET)
    monkeypatch.setattr(runtime_module, "_ui_base_url", runtime_module._UNSET)
    monkeypatch.setattr(lock_module, "_lock_redis", lock_module._UNSET)


def test_unconfigured_resources_raise_named_errors() -> None:
    with pytest.raises(NatsNotConfiguredError, match="configure_nats") as nats_error:
        get_nats_configuration()
    with pytest.raises(SlackNotConfiguredError, match="configure_slack") as slack_error:
        get_slack_configuration()
    with pytest.raises(UIBaseURLNotConfiguredError, match="configure_ui_base_url") as ui_error:
        get_ui_base_url()

    assert nats_error.value.non_retryable is True
    assert slack_error.value.non_retryable is True
    assert ui_error.value.non_retryable is True


def test_individual_configuration_is_idempotent() -> None:
    def nats_provider() -> tuple[str, str]:
        return "archive", "workflow.result"

    def slack_provider() -> tuple[str, str]:
        return "token", "channel"

    def ui_base_url_provider() -> str:
        return "https://config-manager.example"

    for _ in range(2):
        configure_nats(nats_provider)
        configure_slack(slack_provider)
        configure_ui_base_url(ui_base_url_provider)

    assert get_nats_configuration() == ("archive", "workflow.result")
    assert get_slack_configuration() == ("token", "channel")
    assert get_ui_base_url() == "https://config-manager.example"


def test_configurers_accept_none() -> None:
    configure_nats(None)
    configure_slack(None)
    configure_ui_base_url(None)

    with pytest.raises(NatsNotConfiguredError, match="disabled"):
        get_nats_configuration()
    assert get_slack_configuration() == (None, None)
    with pytest.raises(UIBaseURLNotConfiguredError, match="disabled"):
        get_ui_base_url()


def test_configure_runtime_applies_every_resource_and_is_safe_twice() -> None:
    backend = cast(Redis, Mock(spec=Redis))

    def nats_provider() -> tuple[str, str]:
        return "archive", "workflow.result"

    def slack_provider() -> tuple[str, str]:
        return "token", "channel"

    def ui_base_url_provider() -> str:
        return "https://config-manager.example"

    for _ in range(2):
        configure_runtime(
            lock_redis=backend,
            nats_provider=nats_provider,
            slack_provider=slack_provider,
            ui_base_url_provider=ui_base_url_provider,
        )

    assert lock_module._lock_redis is backend
    assert get_nats_configuration() == ("archive", "workflow.result")
    assert get_slack_configuration() == ("token", "channel")
    assert get_ui_base_url() == "https://config-manager.example"


def test_configure_runtime_providers_supply_current_values() -> None:
    nats = [("archive-v1", "workflow.v1")]
    slack = [("token-v1", "channel-v1")]
    ui_base_url = ["https://config-manager-v1.example"]
    configure_runtime(
        lock_redis=None,
        nats_provider=lambda: nats[0],
        slack_provider=lambda: slack[0],
        ui_base_url_provider=lambda: ui_base_url[0],
    )

    assert get_nats_configuration() == ("archive-v1", "workflow.v1")
    assert get_slack_configuration() == ("token-v1", "channel-v1")
    assert get_ui_base_url() == "https://config-manager-v1.example"

    nats[0] = ("archive-v2", "workflow.v2")
    slack[0] = ("token-v2", "channel-v2")
    ui_base_url[0] = "https://config-manager-v2.example"

    assert get_nats_configuration() == ("archive-v2", "workflow.v2")
    assert get_slack_configuration() == ("token-v2", "channel-v2")
    assert get_ui_base_url() == "https://config-manager-v2.example"
