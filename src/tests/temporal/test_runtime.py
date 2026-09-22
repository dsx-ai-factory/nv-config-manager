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
"""Tests for service-owned workflow runtime composition."""

from configparser import ConfigParser
from unittest.mock import Mock

from nv_config_manager_infrastructure.nats import NatsProducer as InfrastructureNatsProducer
from pytest_mock import MockerFixture

from nv_config_manager.temporal import runtime as service_runtime
from nv_config_manager_workflows.runtime import (
    NatsRuntime,
    SlackRuntime,
    get_lock_backend,
    get_nats_runtime,
    get_slack_runtime,
    get_ui_base_url,
)


def _config(
    *,
    stream: str = "archive",
    subject: str = "workflow.result",
    slack_token: str = "token",
    slack_channel: str = "channel",
    ui_url: str = "https://config-manager.example",
) -> ConfigParser:
    """Build representative service configuration for runtime tests."""
    config = ConfigParser()
    config.read_dict(
        {
            "nats": {
                "server": "nats://nats.example.test:4222",
                "archive_stream": stream,
                "archive_subject": subject,
            },
            "slack": {"bot_token": slack_token, "channel_name": slack_channel},
            "temporal": {"ui_url": ui_url},
        }
    )
    return config


def test_nats_service_adapter_uses_infrastructure_producer(mocker: MockerFixture) -> None:
    """NATS composition reuses the infrastructure implementation and current INI."""
    config = _config()
    publisher = Mock(spec=InfrastructureNatsProducer)
    mocker.patch.object(service_runtime, "load_config", return_value=config)
    from_config = mocker.patch.object(
        service_runtime.NatsProducer,
        "from_config",
        return_value=publisher,
    )

    runtime = service_runtime._nats_runtime()

    assert runtime == NatsRuntime(
        publisher=publisher,
        stream="archive",
        subject="workflow.result",
    )
    from_config.assert_called_once_with(config)
    assert issubclass(service_runtime.NatsProducer, InfrastructureNatsProducer)


def test_root_test_environment_installs_default_runtime_providers() -> None:
    """Root tests receive the same service-backed runtime wiring as the worker."""
    runtime = get_nats_runtime()

    assert isinstance(runtime.publisher, InfrastructureNatsProducer)
    assert runtime.stream == "nv-config-manager"
    assert runtime.subject == "nv-config-manager.workflow.result"
    assert get_slack_runtime() == SlackRuntime("DUMMY", "nv-config-manager-test")
    assert get_ui_base_url() == "https://config-manager.example.com"
    assert get_lock_backend() is not None


def test_lock_backend_selection_remains_lazy_at_runtime_startup(
    mocker: MockerFixture,
) -> None:
    """Startup installs the lock provider without selecting Redis or local mode."""
    backend = mocker.Mock()
    token_lock_backend = mocker.patch.object(
        service_runtime,
        "token_lock_backend",
        return_value=backend,
    )

    service_runtime.configure_workflow_runtime()

    token_lock_backend.assert_not_called()
    assert get_lock_backend() is backend
    token_lock_backend.assert_called_once_with()


def test_service_providers_read_current_configuration(mocker: MockerFixture) -> None:
    """Installed providers resolve the current config each time they are accessed."""
    initial = _config(
        stream="archive-v1",
        subject="workflow.v1",
        slack_token="token-v1",
        slack_channel="channel-v1",
        ui_url="https://config-manager-v1.example",
    )
    rotated = _config(
        stream="archive-v2",
        subject="workflow.v2",
        slack_token="token-v2",
        slack_channel="channel-v2",
        ui_url="https://config-manager-v2.example",
    )
    load_config = mocker.patch.object(service_runtime, "load_config", return_value=initial)
    publishers = [Mock(spec=InfrastructureNatsProducer), Mock(spec=InfrastructureNatsProducer)]
    mocker.patch.object(
        service_runtime.NatsProducer,
        "from_config",
        side_effect=publishers,
    )
    service_runtime.configure_workflow_runtime()

    assert get_nats_runtime() == NatsRuntime(publishers[0], "archive-v1", "workflow.v1")
    assert get_slack_runtime() == SlackRuntime("token-v1", "channel-v1")
    assert get_ui_base_url() == "https://config-manager-v1.example"

    load_config.return_value = rotated

    assert get_nats_runtime() == NatsRuntime(publishers[1], "archive-v2", "workflow.v2")
    assert get_slack_runtime() == SlackRuntime("token-v2", "channel-v2")
    assert get_ui_base_url() == "https://config-manager-v2.example"


def test_missing_optional_sections_disable_resources(mocker: MockerFixture) -> None:
    """Absent service settings map to explicitly disabled runtime resources."""
    mocker.patch.object(service_runtime, "load_config", return_value=ConfigParser())
    from_config = mocker.patch.object(service_runtime.NatsProducer, "from_config")

    assert service_runtime._nats_runtime() is None
    assert service_runtime._slack_runtime() is None
    assert service_runtime._ui_base_url() is None
    from_config.assert_not_called()


def test_blank_slack_values_disable_notifications(mocker: MockerFixture) -> None:
    """Blank Slack credentials retain the existing notification no-op behavior."""
    mocker.patch.object(
        service_runtime,
        "load_config",
        return_value=_config(slack_token="  ", slack_channel="  "),
    )

    assert service_runtime._slack_runtime() is None
