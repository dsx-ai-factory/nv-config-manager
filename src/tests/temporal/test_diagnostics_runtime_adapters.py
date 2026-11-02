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
"""Tests for service-owned diagnostics runtime composition."""

from configparser import ConfigParser
from unittest.mock import Mock, call

from pytest_mock import MockerFixture

from nv_config_manager.temporal import runtime as service_runtime
from nv_config_manager_workflows.runtime import (
    get_api_base_url,
    get_redis_client,
    get_ticketing_provider,
)


def test_redis_adapter_translates_current_settings_on_every_call(
    mocker: MockerFixture,
) -> None:
    """Redis construction is lazy and observes reloaded service configuration."""
    configs = [ConfigParser(), ConfigParser()]
    clients = [Mock(), Mock()]
    load_config = mocker.patch.object(service_runtime, "load_config", side_effect=configs)
    settings = mocker.patch.object(
        service_runtime,
        "redis_settings",
        side_effect=[{"host": "redis-one"}, {"host": "redis-two"}],
    )
    constructor = mocker.patch.object(service_runtime, "RedisClient", side_effect=clients)

    assert service_runtime._redis_client() is clients[0]
    assert service_runtime._redis_client() is clients[1]
    assert load_config.call_count == 2
    assert settings.call_args_list == [call(configs[0]), call(configs[1])]
    assert constructor.call_args_list == [call(host="redis-one"), call(host="redis-two")]


def test_ticketing_adapter_resolves_current_credentials_on_every_call(
    mocker: MockerFixture,
) -> None:
    """Ticket providers receive fresh explicit settings and the selected platform."""
    configs = [ConfigParser(), ConfigParser()]
    providers = [Mock(), Mock()]
    load_config = mocker.patch.object(service_runtime, "load_config", side_effect=configs)
    settings = mocker.patch.object(
        service_runtime,
        "ticketing_client_settings",
        side_effect=[{"base_url": "one", "api_token": "a"}, {"base_url": "two", "api_token": "b"}],
    )
    factory = mocker.patch.object(
        service_runtime,
        "build_ticketing_provider",
        side_effect=providers,
    )

    assert service_runtime._ticketing_provider("jira") is providers[0]
    assert service_runtime._ticketing_provider("jira") is providers[1]
    assert load_config.call_count == 2
    assert settings.call_args_list == [
        call(configs[0], platform="jira"),
        call(configs[1], platform="jira"),
    ]
    assert factory.call_args_list == [
        call("jira", {"base_url": "one", "api_token": "a"}),
        call("jira", {"base_url": "two", "api_token": "b"}),
    ]


def test_external_api_url_is_reload_aware_and_distinct_from_ui_url(
    mocker: MockerFixture,
) -> None:
    """The diagnostics URL reads temporal.api_url and preserves blank disablement."""
    first = ConfigParser()
    first.read_dict({"temporal": {"api_url": "https://api.example.test/", "ui_url": "ui"}})
    second = ConfigParser()
    second.read_dict({"temporal": {"api_url": "", "ui_url": "still-ui"}})
    mocker.patch.object(service_runtime, "load_config", side_effect=[first, second])

    assert service_runtime._api_base_url() == "https://api.example.test"
    assert service_runtime._api_base_url() == ""


def test_configure_workflow_runtime_installs_diagnostics_providers_without_eager_io(
    mocker: MockerFixture,
) -> None:
    """Worker startup stores factories; resource construction occurs on getter calls."""
    redis = Mock()
    ticketing = Mock()
    redis_factory = mocker.patch.object(service_runtime, "_redis_client", return_value=redis)
    ticketing_factory = mocker.patch.object(
        service_runtime, "_ticketing_provider", return_value=ticketing
    )
    api_url = mocker.patch.object(
        service_runtime, "_api_base_url", return_value="https://api.example.test"
    )

    service_runtime.configure_workflow_runtime()
    redis_factory.assert_not_called()
    ticketing_factory.assert_not_called()
    api_url.assert_not_called()

    assert get_redis_client() is redis
    assert get_ticketing_provider("jira") is ticketing
    assert get_api_base_url() == "https://api.example.test"
    redis_factory.assert_called_once_with()
    ticketing_factory.assert_called_once_with("jira")
    api_url.assert_called_once_with()
