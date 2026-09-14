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
"""Tests for translating service configuration into workflow runtime state."""

from configparser import ConfigParser
from typing import cast

import pytest
from pytest_mock import MockerFixture
from redis.asyncio import Redis

from nv_config_manager.temporal import runtime as service_runtime
from nv_config_manager_workflows.runtime import (
    NatsNotConfiguredError,
    UIBaseURLNotConfiguredError,
    get_nats_configuration,
    get_slack_configuration,
    get_ui_base_url,
)


def test_configure_workflow_runtime_installs_hot_reload_providers(
    mocker: MockerFixture,
) -> None:
    configure_runtime = mocker.patch.object(service_runtime, "configure_runtime")
    backend = cast(Redis, mocker.Mock(spec=Redis))

    service_runtime.configure_workflow_runtime(lock_redis=backend)

    configure_runtime.assert_called_once_with(
        lock_redis=backend,
        nats_provider=service_runtime._nats_configuration,
        slack_provider=service_runtime._slack_configuration,
        ui_base_url_provider=service_runtime._ui_base_url,
    )


def test_runtime_providers_read_current_configuration(
    mocker: MockerFixture,
) -> None:
    initial = ConfigParser()
    initial.read_dict(
        {
            "nats": {
                "archive_stream": "archive-v1",
                "archive_subject": "workflow.v1",
            },
            "slack": {"bot_token": "token-v1", "channel_name": "channel-v1"},
            "temporal": {"ui_url": "https://config-manager-v1.example"},
        }
    )
    rotated = ConfigParser()
    rotated.read_dict(
        {
            "nats": {
                "archive_stream": "archive-v2",
                "archive_subject": "workflow.v2",
            },
            "slack": {"bot_token": "token-v2", "channel_name": "channel-v2"},
            "temporal": {"ui_url": "https://config-manager-v2.example"},
        }
    )
    load_config = mocker.patch.object(service_runtime, "load_config", return_value=initial)

    service_runtime.configure_workflow_runtime(lock_redis=None)

    assert get_nats_configuration() == ("archive-v1", "workflow.v1")
    assert get_slack_configuration() == ("token-v1", "channel-v1")
    assert get_ui_base_url() == "https://config-manager-v1.example"

    load_config.return_value = rotated

    assert get_nats_configuration() == ("archive-v2", "workflow.v2")
    assert get_slack_configuration() == ("token-v2", "channel-v2")
    assert get_ui_base_url() == "https://config-manager-v2.example"


def test_runtime_providers_accept_missing_optional_sections(mocker: MockerFixture) -> None:
    mocker.patch.object(service_runtime, "load_config", return_value=ConfigParser())

    service_runtime.configure_workflow_runtime(lock_redis=None)

    with pytest.raises(NatsNotConfiguredError, match="disabled or incomplete"):
        get_nats_configuration()
    assert get_slack_configuration() == (None, None)
    with pytest.raises(UIBaseURLNotConfiguredError, match="disabled"):
        get_ui_base_url()
