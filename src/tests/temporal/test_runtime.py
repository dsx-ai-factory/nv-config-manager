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
from unittest.mock import Mock, call

import pytest
from nv_config_manager_dcim.api import DCIMClient
from nv_config_manager_dcim.workflow_models import NetworkDeviceData, Platform
from nv_config_manager_infrastructure.nats import NatsProducer as InfrastructureNatsProducer
from pytest_mock import MockerFixture

from nv_config_manager.temporal import runtime as service_runtime
from nv_config_manager_workflows.clients.device.base import (
    NetworkConnection as WorkflowNetworkConnection,
)
from nv_config_manager_workflows.clients.ufm import UFMClient as WorkflowUFMClient
from nv_config_manager_workflows.runtime import (
    NatsNotConfiguredError,
    NatsRuntime,
    SlackRuntime,
    get_dcim_client,
    get_device_connection,
    get_lock_backend,
    get_nats_runtime,
    get_slack_runtime,
    get_ufm_client,
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


def _device_data() -> NetworkDeviceData:
    """Build provider-neutral inventory for service device adapter tests."""
    return NetworkDeviceData(
        id="device-1",
        name="leaf-1",
        role="leaf",
        site="site-1",
        device_type="switch",
        platform=Platform.CUMULUS_LINUX,
        primary_ip4="192.0.2.1",
        primary_ip6=None,
    )


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


def test_dcim_service_adapter_uses_configured_client_factory(mocker: MockerFixture) -> None:
    """DCIM composition delegates provider selection and settings to the service adapter."""
    client = Mock(spec=DCIMClient)
    create_dcim_client = mocker.patch.object(
        service_runtime,
        "create_dcim_client",
        return_value=client,
    )

    assert service_runtime._dcim_client() is client
    create_dcim_client.assert_called_once_with()


def test_device_service_adapter_uses_configured_connection_factory(
    mocker: MockerFixture,
) -> None:
    """Device composition delegates platform and credential selection to the service adapter."""
    device_data = _device_data()
    connection = Mock(spec=WorkflowNetworkConnection)
    from_device_data = mocker.patch.object(
        service_runtime.NetworkConnection,
        "from_device_data",
        return_value=connection,
    )

    assert service_runtime._device_connection(device_data) is connection
    from_device_data.assert_called_once_with(device_data)
    assert issubclass(service_runtime.NetworkConnection, WorkflowNetworkConnection)


def test_ufm_service_adapter_uses_configured_client_factory(mocker: MockerFixture) -> None:
    """UFM composition delegates credential lookup and password rotation to the adapter."""
    assert issubclass(service_runtime.UFMClient, WorkflowUFMClient)
    client = Mock(spec=WorkflowUFMClient)
    ufm_client = mocker.patch.object(service_runtime, "UFMClient", return_value=client)

    assert service_runtime._ufm_client("ufm.example.test", "site-1") is client
    ufm_client.assert_called_once_with(host="ufm.example.test", site="site-1")


def test_root_test_environment_installs_default_runtime_providers() -> None:
    """Root tests receive the same service-backed runtime wiring as the worker."""
    runtime = get_nats_runtime()

    assert isinstance(runtime.publisher, InfrastructureNatsProducer)
    assert runtime.stream == "nv-config-manager"
    assert runtime.subject == "nv-config-manager.workflow.result"
    assert get_slack_runtime() == SlackRuntime("DUMMY", "nv-config-manager-test")
    assert get_ui_base_url() == "https://config-manager.example.com"
    assert get_lock_backend() is not None


def test_api_runtime_configuration_installs_api_providers(mocker: MockerFixture) -> None:
    """API composition installs UI and DCIM without worker-only dependencies."""
    configure_ui_base_url = mocker.patch.object(service_runtime, "configure_ui_base_url")
    configure_dcim_client = mocker.patch.object(service_runtime, "configure_dcim_client")
    configure_runtime = mocker.patch.object(service_runtime, "configure_runtime")
    token_lock_backend = mocker.patch.object(service_runtime, "token_lock_backend")

    service_runtime.configure_workflow_ui_runtime()

    configure_ui_base_url.assert_called_once_with(service_runtime._ui_base_url)
    configure_dcim_client.assert_called_once_with(service_runtime._dcim_client)
    configure_runtime.assert_not_called()
    token_lock_backend.assert_not_called()


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


def test_dcim_client_selection_remains_lazy_at_runtime_startup(
    mocker: MockerFixture,
) -> None:
    """Startup installs the DCIM factory without selecting a provider immediately."""
    client = Mock(spec=DCIMClient)
    create_dcim_client = mocker.patch.object(
        service_runtime,
        "create_dcim_client",
        return_value=client,
    )

    service_runtime.configure_workflow_runtime()

    create_dcim_client.assert_not_called()
    assert get_dcim_client() is client
    create_dcim_client.assert_called_once_with()


def test_device_and_ufm_selection_remains_lazy_at_runtime_startup(
    mocker: MockerFixture,
) -> None:
    """Startup installs factories without resolving device or UFM credentials."""
    device_data = _device_data()
    connection = Mock(spec=WorkflowNetworkConnection)
    ufm = Mock(spec=WorkflowUFMClient)
    from_device_data = mocker.patch.object(
        service_runtime.NetworkConnection,
        "from_device_data",
        return_value=connection,
    )
    ufm_client = mocker.patch.object(service_runtime, "UFMClient", return_value=ufm)

    service_runtime.configure_workflow_runtime()

    from_device_data.assert_not_called()
    ufm_client.assert_not_called()
    assert get_device_connection(device_data) is connection
    assert get_ufm_client("ufm.example.test", "site-1") is ufm
    from_device_data.assert_called_once_with(device_data)
    ufm_client.assert_called_once_with(host="ufm.example.test", site="site-1")


def test_installed_device_and_ufm_factories_resolve_each_access(
    mocker: MockerFixture,
) -> None:
    """Each lookup constructs a client through the current service adapter state."""
    device_data = _device_data()
    connections = [Mock(spec=WorkflowNetworkConnection), Mock(spec=WorkflowNetworkConnection)]
    ufm_clients = [Mock(spec=WorkflowUFMClient), Mock(spec=WorkflowUFMClient)]
    from_device_data = mocker.patch.object(
        service_runtime.NetworkConnection,
        "from_device_data",
        side_effect=connections,
    )
    ufm_client = mocker.patch.object(
        service_runtime,
        "UFMClient",
        side_effect=ufm_clients,
    )
    service_runtime.configure_workflow_runtime()

    assert get_device_connection(device_data) is connections[0]
    assert get_device_connection(device_data) is connections[1]
    assert get_ufm_client("ufm.example.test", "site-1") is ufm_clients[0]
    assert get_ufm_client("ufm.example.test", "site-1") is ufm_clients[1]
    assert from_device_data.call_args_list == [call(device_data), call(device_data)]
    assert ufm_client.call_args_list == [
        call(host="ufm.example.test", site="site-1"),
        call(host="ufm.example.test", site="site-1"),
    ]


def test_installed_dcim_factory_reads_current_service_configuration(
    mocker: MockerFixture,
) -> None:
    """Each client request resolves the service's latest DCIM settings."""
    initial = ConfigParser()
    initial.read_dict(
        {
            "dcim": {
                "provider": "nautobot-2x",
                "server": "https://dcim-v1.example",
                "token": "token-v1",
            }
        }
    )
    rotated = ConfigParser()
    rotated.read_dict(
        {
            "dcim": {
                "provider": "nautobot-2x",
                "server": "https://dcim-v2.example",
                "token": "token-v2",
            }
        }
    )
    load_config = mocker.patch(
        "nv_config_manager.common.config.load_config",
        side_effect=[initial, rotated],
    )
    clients = [Mock(spec=DCIMClient), Mock(spec=DCIMClient)]
    create_sdk_client = mocker.patch(
        "nv_config_manager.dcim.registry.create_sdk_dcim_client",
        side_effect=clients,
    )
    service_runtime.configure_workflow_runtime()

    assert get_dcim_client() is clients[0]
    assert get_dcim_client() is clients[1]
    assert load_config.call_count == 2
    assert create_sdk_client.call_args_list == [
        call(
            "nautobot-2x",
            {"server": "https://dcim-v1.example", "token": "token-v1"},
        ),
        call(
            "nautobot-2x",
            {"server": "https://dcim-v2.example", "token": "token-v2"},
        ),
    ]


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
    dcim_clients = [Mock(spec=DCIMClient), Mock(spec=DCIMClient)]
    mocker.patch.object(
        service_runtime.NatsProducer,
        "from_config",
        side_effect=publishers,
    )
    create_dcim_client = mocker.patch.object(
        service_runtime,
        "create_dcim_client",
        side_effect=dcim_clients,
    )
    service_runtime.configure_workflow_runtime()

    assert get_nats_runtime() == NatsRuntime(publishers[0], "archive-v1", "workflow.v1")
    assert get_slack_runtime() == SlackRuntime("token-v1", "channel-v1")
    assert get_ui_base_url() == "https://config-manager-v1.example"
    assert get_dcim_client() is dcim_clients[0]

    load_config.return_value = rotated

    assert get_nats_runtime() == NatsRuntime(publishers[1], "archive-v2", "workflow.v2")
    assert get_slack_runtime() == SlackRuntime("token-v2", "channel-v2")
    assert get_ui_base_url() == "https://config-manager-v2.example"
    assert get_dcim_client() is dcim_clients[1]
    assert create_dcim_client.call_count == 2


def test_missing_optional_sections_disable_resources(mocker: MockerFixture) -> None:
    """Absent service settings map to explicitly disabled runtime resources."""
    mocker.patch.object(service_runtime, "load_config", return_value=ConfigParser())
    from_config = mocker.patch.object(service_runtime.NatsProducer, "from_config")

    assert service_runtime._nats_runtime() is None
    assert service_runtime._slack_runtime() is None
    assert service_runtime._ui_base_url() is None
    from_config.assert_not_called()


@pytest.mark.parametrize("server", [None, "", "  "])
def test_missing_or_blank_nats_server_disables_resource(
    server: str | None,
    mocker: MockerFixture,
) -> None:
    """An incomplete NATS endpoint is reported through the runtime error boundary."""
    nats_settings = {
        "archive_stream": "archive",
        "archive_subject": "workflow.result",
    }
    if server is not None:
        nats_settings["server"] = server
    config = ConfigParser()
    config.read_dict({"nats": nats_settings})
    mocker.patch.object(service_runtime, "load_config", return_value=config)
    from_config = mocker.patch.object(service_runtime.NatsProducer, "from_config")
    service_runtime.configure_workflow_runtime()

    with pytest.raises(NatsNotConfiguredError, match="disabled or incomplete"):
        get_nats_runtime()
    from_config.assert_not_called()


def test_blank_slack_values_disable_notifications(mocker: MockerFixture) -> None:
    """Blank Slack credentials retain the existing notification no-op behavior."""
    mocker.patch.object(
        service_runtime,
        "load_config",
        return_value=_config(slack_token="  ", slack_channel="  "),
    )

    assert service_runtime._slack_runtime() is None
