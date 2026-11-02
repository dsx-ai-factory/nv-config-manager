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
"""Service adapters supplied to the package-owned backup scheduler."""

import asyncio
from configparser import ConfigParser
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio.contrib.opentelemetry import TracingInterceptor

from nv_config_manager.temporal import runtime


@pytest.fixture
def forbid_scheduler_io(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail if building scheduler runtimes reaches configuration or a service."""

    def fail(*args: object, **kwargs: object) -> None:
        pytest.fail("building a scheduler runtime must not perform I/O")

    monkeypatch.setattr(runtime.Client, "connect", fail)
    monkeypatch.setattr(runtime, "load_config", fail)
    monkeypatch.setattr(runtime, "create_dcim_client", fail)
    monkeypatch.setattr(runtime, "RBACConfig", fail)


@pytest.mark.usefixtures("forbid_scheduler_io")
def test_scheduler_runtime_contains_service_adapters_without_io() -> None:
    scheduler_runtime = runtime.build_scheduler_runtime()

    assert scheduler_runtime.temporal_client is runtime._scheduler_temporal_client
    assert scheduler_runtime.workflow_roles is runtime._scheduler_workflow_roles
    assert scheduler_runtime.sleep is asyncio.sleep


@pytest.mark.usefixtures("forbid_scheduler_io")
def test_builtin_scheduler_runtime_contains_service_adapters_without_io() -> None:
    builtin_runtime = runtime.build_builtin_scheduler_runtime()

    assert builtin_runtime.desired_backup_devices is runtime._desired_backup_devices


@pytest.mark.parametrize(
    ("config_sections", "expected_is_aggregate"),
    [
        pytest.param(
            {"aggregate": {"is_aggregate_environment": "true"}}, True, id="aggregate-enabled"
        ),
        pytest.param({}, False, id="aggregate-section-missing"),
    ],
)
@pytest.mark.asyncio
async def test_desired_backup_devices_uses_provider_and_aggregate_setting(
    monkeypatch: pytest.MonkeyPatch,
    config_sections: dict[str, dict[str, str]],
    expected_is_aggregate: bool,
) -> None:
    config = ConfigParser()
    config.read_dict(config_sections)
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get_backup_enabled_device_ids.return_value = {"device-1", "device-2"}
    monkeypatch.setattr(runtime, "load_config", lambda: config)
    monkeypatch.setattr(
        runtime,
        "create_dcim_client",
        lambda service_config: client if service_config is config else None,
    )

    result = await runtime._desired_backup_devices()

    assert result == {"device-1", "device-2"}
    client.__aenter__.assert_awaited_once_with()
    client.__aexit__.assert_awaited_once()
    client.get_backup_enabled_device_ids.assert_awaited_once_with(expected_is_aggregate)


@pytest.mark.asyncio
async def test_invalid_aggregate_setting_fails_before_creating_the_dcim_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ConfigParser()
    config.read_dict({"aggregate": {"is_aggregate_environment": "maybe"}})
    monkeypatch.setattr(runtime, "load_config", lambda: config)
    monkeypatch.setattr(
        runtime,
        "create_dcim_client",
        lambda service_config: pytest.fail("DCIM client created before flag validation"),
    )

    with pytest.raises(ValueError):
        await runtime._desired_backup_devices()


def test_workflow_roles_are_copied_into_an_immutable_package_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rbac = SimpleNamespace(
        get_workflow_roles=lambda name: (
            {
                "read_roles": {"reader-b", "reader-a"},
                "execute_roles": {"executor"},
            }
            if name == "BackupWorkflow"
            else None
        )
    )
    monkeypatch.setattr(runtime, "RBACConfig", lambda: rbac)

    roles = runtime._scheduler_workflow_roles("BackupWorkflow")

    assert roles is not None
    assert roles.read_roles == frozenset({"reader-a", "reader-b"})
    assert roles.execute_roles == frozenset({"executor"})
    assert runtime._scheduler_workflow_roles("MissingWorkflow") is None


def test_workflow_roles_preserve_configured_entries_with_empty_role_sets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rbac = SimpleNamespace(
        get_workflow_roles=lambda name: {"read_roles": set(), "execute_roles": set()}
    )
    monkeypatch.setattr(runtime, "RBACConfig", lambda: rbac)

    roles = runtime._scheduler_workflow_roles("BackupWorkflow")

    assert roles is not None
    assert roles.read_roles == frozenset()
    assert roles.execute_roles == frozenset()


@pytest.mark.asyncio
async def test_temporal_client_connects_anew_on_every_call_with_service_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_client = object()
    second_client = object()
    connect = AsyncMock(side_effect=[first_client, second_client])
    converter = object()
    temporal_runtime = object()
    monkeypatch.setattr(runtime.Client, "connect", connect)
    monkeypatch.setattr(runtime, "temporal_address", lambda: "temporal.example:7233")
    monkeypatch.setattr(runtime, "client_connect_options", lambda: {"namespace": "example"})
    monkeypatch.setattr(runtime, "get_data_converter", lambda: converter)
    monkeypatch.setattr(runtime, "get_runtime", lambda: temporal_runtime)

    assert await runtime._scheduler_temporal_client() is first_client
    assert await runtime._scheduler_temporal_client() is second_client

    # Connecting per call re-reads configuration and TLS material; never cache it.
    assert connect.await_count == 2
    for args, kwargs in connect.await_args_list:
        assert args == ("temporal.example:7233",)
        assert set(kwargs) == {"namespace", "data_converter", "interceptors", "runtime"}
        assert kwargs["namespace"] == "example"
        assert kwargs["data_converter"] is converter
        assert kwargs["runtime"] is temporal_runtime
        (interceptor,) = kwargs["interceptors"]
        assert isinstance(interceptor, TracingInterceptor)
        assert interceptor._always_create_workflow_spans is True
