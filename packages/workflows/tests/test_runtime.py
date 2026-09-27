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
"""Tests for process-local workflow runtime dependencies."""

from typing import Any, cast

import pytest
from nv_config_manager_dcim.api import DCIMClient
from nv_config_manager_dcim.workflow_models import NetworkDeviceData, Platform
from temporalio.api.failure.v1 import Failure
from temporalio.converter import DefaultFailureConverter, DefaultPayloadConverter

from nv_config_manager_workflows.clients.device.base import NetworkConnection
from nv_config_manager_workflows.clients.ufm import UFMClient
from nv_config_manager_workflows.runtime import (
    DCIMNotConfiguredError,
    DeviceConnectionNotConfiguredError,
    LockNotConfiguredError,
    NatsNotConfiguredError,
    NatsRuntime,
    RuntimeConfigurationError,
    SlackNotConfiguredError,
    SlackRuntime,
    UFMClientNotConfiguredError,
    UIBaseURLNotConfiguredError,
    configure_dcim_client,
    configure_device_connection,
    configure_lock_backend,
    configure_nats,
    configure_runtime,
    configure_slack,
    configure_ufm_client,
    configure_ui_base_url,
    get_dcim_client,
    get_device_connection,
    get_lock_backend,
    get_nats_runtime,
    get_slack_runtime,
    get_ufm_client,
    get_ui_base_url,
)


class StubNatsPublisher:
    """Minimal NATS publisher satisfying the workflow runtime protocol."""

    server = "nats://nats.example.test:4222"

    async def publish(self, subject: str, message: str, stream: str | None = None) -> None:
        """Accept a publish call without performing network I/O."""


class StubLockBackend:
    """Minimal distributed lock backend for runtime configuration tests."""

    async def acquire(
        self,
        name: str,
        token: str,
        *,
        timeout: int,
        blocking_timeout: float | None = None,
        blocking: bool = True,
    ) -> bool:
        """Accept a lock acquisition without external I/O."""
        return True

    async def renew(self, name: str, token: str, *, timeout: int) -> bool:
        """Accept a lock renewal without external I/O."""
        return True

    async def release(self, name: str, token: str) -> bool:
        """Accept a lock release without external I/O."""
        return True


def stub_dcim_client() -> DCIMClient:
    """Return an identity-only stand-in for the provider-neutral client protocol."""
    return cast(DCIMClient, object())


def stub_device_data() -> NetworkDeviceData:
    """Return provider-neutral inventory for a device provider lookup."""
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


def stub_device_connection() -> NetworkConnection:
    """Return an identity-only stand-in for a package device connection."""
    return cast(NetworkConnection, object())


def stub_ufm_client() -> UFMClient:
    """Return an identity-only stand-in for a package UFM client."""
    return cast(UFMClient, object())


async def test_package_test_environment_configures_safe_defaults() -> None:
    """Package and plugin activity tests start with isolated no-I/O dependencies."""
    nats = get_nats_runtime()

    assert nats.stream == "test-workflow-events"
    assert nats.subject == "test.workflow.result"
    assert get_slack_runtime() is None
    assert get_ui_base_url() == "https://workflow-ui.test"
    assert await get_lock_backend().acquire("resource", "token", timeout=30) is True
    assert get_dcim_client() is get_dcim_client()
    assert get_device_connection(stub_device_data()) is get_device_connection(stub_device_data())
    assert get_ufm_client("ufm.example.test", "site-1") is get_ufm_client(
        "ufm.example.test", "site-1"
    )


@pytest.mark.parametrize(
    "error_class",
    [
        NatsNotConfiguredError,
        SlackNotConfiguredError,
        UIBaseURLNotConfiguredError,
        LockNotConfiguredError,
        DCIMNotConfiguredError,
        DeviceConnectionNotConfiguredError,
        UFMClientNotConfiguredError,
    ],
)
def test_configuration_error_name_survives_temporal_serialization(
    error_class: type[RuntimeConfigurationError],
) -> None:
    """Temporal history retains the concrete missing-resource failure type."""
    failure = Failure()

    DefaultFailureConverter().to_failure(
        error_class("missing runtime resource"),
        DefaultPayloadConverter.default,
        failure,
    )

    assert failure.application_failure_info.type == error_class.__name__
    assert failure.application_failure_info.non_retryable is True


def test_unconfigured_resources_raise_named_non_retryable_errors(
    unconfigured_workflow_runtime: None,
) -> None:
    """Resources used before worker startup fail clearly and permanently."""
    with pytest.raises(NatsNotConfiguredError, match="configure_nats") as nats_error:
        get_nats_runtime()
    with pytest.raises(SlackNotConfiguredError, match="configure_slack") as slack_error:
        get_slack_runtime()
    with pytest.raises(UIBaseURLNotConfiguredError, match="configure_ui_base_url") as ui_error:
        get_ui_base_url()
    with pytest.raises(LockNotConfiguredError, match="configure_lock_backend") as lock_error:
        get_lock_backend()
    with pytest.raises(DCIMNotConfiguredError, match="configure_dcim_client") as dcim_error:
        get_dcim_client()
    with pytest.raises(
        DeviceConnectionNotConfiguredError, match="configure_device_connection"
    ) as device_error:
        get_device_connection(stub_device_data())
    with pytest.raises(UFMClientNotConfiguredError, match="configure_ufm_client") as ufm_error:
        get_ufm_client("ufm.example.test")

    assert nats_error.value.non_retryable is True
    assert slack_error.value.non_retryable is True
    assert ui_error.value.non_retryable is True
    assert lock_error.value.non_retryable is True
    assert dcim_error.value.non_retryable is True
    assert device_error.value.non_retryable is True
    assert ufm_error.value.non_retryable is True


async def test_explicit_none_resources_are_distinct_from_unconfigured() -> None:
    """None records an intentional disabled or no-op state, not an omitted startup call."""
    configure_nats(None)
    configure_slack(None)
    configure_ui_base_url(None)
    configure_lock_backend(None)
    configure_dcim_client(None)
    configure_device_connection(None)
    configure_ufm_client(None)

    with pytest.raises(NatsNotConfiguredError, match="disabled"):
        get_nats_runtime()
    assert get_slack_runtime() is None
    with pytest.raises(UIBaseURLNotConfiguredError, match="disabled"):
        get_ui_base_url()
    with pytest.raises(DCIMNotConfiguredError, match="disabled"):
        get_dcim_client()
    with pytest.raises(DeviceConnectionNotConfiguredError, match="disabled"):
        get_device_connection(stub_device_data())
    with pytest.raises(UFMClientNotConfiguredError, match="disabled"):
        get_ufm_client("ufm.example.test")
    lock = get_lock_backend()
    assert await lock.acquire("resource", "owner", timeout=30)
    assert await lock.renew("resource", "owner", timeout=30)
    assert await lock.release("resource", "owner")


def test_individual_configuration_is_idempotent() -> None:
    """Applying the same providers twice leaves the same runtime configuration."""
    nats = NatsRuntime(StubNatsPublisher(), "archive", "workflow.result")
    slack = SlackRuntime("token", "channel")
    lock = StubLockBackend()
    dcim_client = stub_dcim_client()
    device_connection = stub_device_connection()
    ufm_client = stub_ufm_client()

    for _ in range(2):
        configure_nats(lambda: nats)
        configure_slack(lambda: slack)
        configure_ui_base_url(lambda: "https://config-manager.example")
        configure_lock_backend(lambda: lock)
        configure_dcim_client(lambda: dcim_client)
        configure_device_connection(lambda _device_data: device_connection)
        configure_ufm_client(lambda _host, _site: ufm_client)

    assert get_nats_runtime() is nats
    assert get_slack_runtime() is slack
    assert get_ui_base_url() == "https://config-manager.example"
    assert get_lock_backend() is lock
    assert get_dcim_client() is dcim_client
    assert get_device_connection(stub_device_data()) is device_connection
    assert get_ufm_client("ufm.example.test", "site-1") is ufm_client


def test_configure_runtime_applies_every_provider_and_is_safe_twice() -> None:
    """The aggregate entry point configures all currently supported resources."""
    nats = NatsRuntime(StubNatsPublisher(), "archive", "workflow.result")
    slack = SlackRuntime("token", "channel")
    lock = StubLockBackend()
    dcim_client = stub_dcim_client()
    device_connection = stub_device_connection()
    ufm_client = stub_ufm_client()

    for _ in range(2):
        configure_runtime(
            nats_provider=lambda: nats,
            slack_provider=lambda: slack,
            ui_base_url_provider=lambda: "https://config-manager.example",
            lock_backend_provider=lambda: lock,
            dcim_client_provider=lambda: dcim_client,
            device_connection_provider=lambda _device_data: device_connection,
            ufm_client_provider=lambda _host, _site: ufm_client,
        )

    assert get_nats_runtime() is nats
    assert get_slack_runtime() is slack
    assert get_ui_base_url() == "https://config-manager.example"
    assert get_lock_backend() is lock
    assert get_dcim_client() is dcim_client
    assert get_device_connection(stub_device_data()) is device_connection
    assert get_ufm_client("ufm.example.test", "site-1") is ufm_client


def test_configure_runtime_retains_original_four_provider_signature() -> None:
    """Older callers continue with newer client providers explicitly disabled."""
    configure_runtime(
        nats_provider=None,
        slack_provider=None,
        ui_base_url_provider=None,
        lock_backend_provider=None,
    )

    with pytest.raises(DCIMNotConfiguredError, match="disabled"):
        get_dcim_client()
    with pytest.raises(DeviceConnectionNotConfiguredError, match="disabled"):
        get_device_connection(stub_device_data())
    with pytest.raises(UFMClientNotConfiguredError, match="disabled"):
        get_ufm_client("ufm.example.test")


def test_providers_supply_current_values() -> None:
    """Getters invoke providers on each access so backing configuration can reload."""
    nats_values = [NatsRuntime(StubNatsPublisher(), "archive-v1", "workflow.v1")]
    slack_values = [SlackRuntime("token-v1", "channel-v1")]
    ui_values = ["https://config-manager-v1.example"]
    lock_values = [StubLockBackend()]
    dcim_values = [stub_dcim_client()]
    device_values = [stub_device_connection()]
    ufm_values = [stub_ufm_client()]
    configure_runtime(
        nats_provider=lambda: nats_values[0],
        slack_provider=lambda: slack_values[0],
        ui_base_url_provider=lambda: ui_values[0],
        lock_backend_provider=lambda: lock_values[0],
        dcim_client_provider=lambda: dcim_values[0],
        device_connection_provider=lambda _device_data: device_values[0],
        ufm_client_provider=lambda _host, _site: ufm_values[0],
    )

    assert get_nats_runtime() is nats_values[0]
    assert get_slack_runtime() is slack_values[0]
    assert get_ui_base_url() == ui_values[0]
    assert get_lock_backend() is lock_values[0]
    assert get_dcim_client() is dcim_values[0]
    assert get_device_connection(stub_device_data()) is device_values[0]
    assert get_ufm_client("ufm.example.test", "site-1") is ufm_values[0]

    nats_values[0] = NatsRuntime(StubNatsPublisher(), "archive-v2", "workflow.v2")
    slack_values[0] = SlackRuntime("token-v2", "channel-v2")
    ui_values[0] = "https://config-manager-v2.example"
    lock_values[0] = StubLockBackend()
    dcim_values[0] = stub_dcim_client()
    device_values[0] = stub_device_connection()
    ufm_values[0] = stub_ufm_client()

    assert get_nats_runtime() is nats_values[0]
    assert get_slack_runtime() is slack_values[0]
    assert get_ui_base_url() == ui_values[0]
    assert get_lock_backend() is lock_values[0]
    assert get_dcim_client() is dcim_values[0]
    assert get_device_connection(stub_device_data()) is device_values[0]
    assert get_ufm_client("ufm.example.test", "site-1") is ufm_values[0]


def test_device_and_ufm_providers_receive_lookup_arguments() -> None:
    """Runtime getters pass inventory, host, and site through without transformation."""
    device_data = stub_device_data()
    device_calls = []
    ufm_calls = []
    device_connection = stub_device_connection()
    ufm_client = stub_ufm_client()

    def device_provider(requested_device: NetworkDeviceData) -> NetworkConnection:
        device_calls.append(requested_device)
        return device_connection

    def ufm_provider(host: str, site: str | None) -> UFMClient:
        ufm_calls.append((host, site))
        return ufm_client

    configure_device_connection(device_provider)
    configure_ufm_client(ufm_provider)

    assert get_device_connection(device_data) is device_connection
    assert get_ufm_client("ufm.example.test", "site-1") is ufm_client
    assert device_calls == [device_data]
    assert ufm_calls == [("ufm.example.test", "site-1")]


def test_device_and_ufm_provider_can_report_resource_as_disabled() -> None:
    """Reload-aware client providers may become incomplete after startup."""
    configure_device_connection(lambda _device_data: None)
    configure_ufm_client(lambda _host, _site: None)

    with pytest.raises(DeviceConnectionNotConfiguredError, match="disabled or incomplete"):
        get_device_connection(stub_device_data())
    with pytest.raises(UFMClientNotConfiguredError, match="disabled or incomplete"):
        get_ufm_client("ufm.example.test")


@pytest.mark.parametrize(
    ("provider_name", "provider", "error"),
    [
        ("nats_provider", lambda: None, NatsNotConfiguredError),
        ("ui_base_url_provider", lambda: None, UIBaseURLNotConfiguredError),
        ("dcim_client_provider", lambda: None, DCIMNotConfiguredError),
    ],
)
def test_provider_can_report_a_resource_as_disabled(
    provider_name: str,
    provider: Any,
    error: type[Exception],
) -> None:
    """A reload-aware provider can disable a resource after startup."""
    configure_runtime(
        nats_provider=provider if provider_name == "nats_provider" else None,
        slack_provider=None,
        ui_base_url_provider=provider if provider_name == "ui_base_url_provider" else None,
        lock_backend_provider=None,
        dcim_client_provider=provider if provider_name == "dcim_client_provider" else None,
    )

    getters = {
        "nats_provider": get_nats_runtime,
        "ui_base_url_provider": get_ui_base_url,
        "dcim_client_provider": get_dcim_client,
    }
    with pytest.raises(error, match="disabled"):
        getters[provider_name]()


def test_nats_runtime_allows_activity_to_supply_subject() -> None:
    """A publisher and stream are usable when an activity supplies its own subject."""
    runtime = NatsRuntime(StubNatsPublisher(), "archive", "")
    configure_nats(lambda: runtime)

    assert get_nats_runtime() is runtime
