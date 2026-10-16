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
"""Tests for the process-local Redfish workflow runtime dependency."""

from typing import cast

import pytest
from temporalio.api.failure.v1 import Failure
from temporalio.converter import DefaultFailureConverter, DefaultPayloadConverter

from nv_config_manager_workflows.clients.redfish.base import RedfishConnection
from nv_config_manager_workflows.clients.redfish.models import RedfishHost, RedfishVendor
from nv_config_manager_workflows.runtime import (
    RedfishConnectionNotConfiguredError,
    RedfishCredentialRole,
    configure_redfish_connection,
    configure_runtime,
    get_redfish_connection,
)


def _host() -> RedfishHost:
    """Return representative package-owned Redfish host data."""
    return RedfishHost(
        address="192.0.2.20",
        port=8443,
        vendor=RedfishVendor.LENOVO,
        mac="aa:bb:cc:dd:ee:ff",
    )


def test_redfish_configuration_error_survives_temporal_serialization() -> None:
    """Temporal history retains the concrete permanent runtime failure type."""
    failure = Failure()

    DefaultFailureConverter().to_failure(
        RedfishConnectionNotConfiguredError("missing Redfish connection"),
        DefaultPayloadConverter.default,
        failure,
    )

    assert failure.application_failure_info.type == "RedfishConnectionNotConfiguredError"
    assert failure.application_failure_info.non_retryable is True


def test_unconfigured_redfish_provider_raises_named_error(
    unconfigured_workflow_runtime: None,
) -> None:
    """Omitted worker configuration fails before any connection can be selected."""
    with pytest.raises(
        RedfishConnectionNotConfiguredError,
        match="configure_redfish_connection",
    ) as exc_info:
        get_redfish_connection(_host(), "default")

    assert exc_info.value.type == "RedfishConnectionNotConfiguredError"
    assert exc_info.value.non_retryable is True


def test_disabled_and_incomplete_redfish_providers_raise_named_errors() -> None:
    """Explicit disablement and unavailable current settings remain distinguishable."""
    configure_redfish_connection(None)
    with pytest.raises(RedfishConnectionNotConfiguredError, match="disabled$"):
        get_redfish_connection(_host(), "default")

    configure_redfish_connection(lambda _host, _credential_role: None)
    with pytest.raises(
        RedfishConnectionNotConfiguredError,
        match="disabled or incomplete",
    ):
        get_redfish_connection(_host(), "config_manager")


def test_unsupported_redfish_role_is_rejected_before_provider_lookup() -> None:
    """An invalid caller argument never reaches a configured provider."""
    calls: list[tuple[RedfishHost, RedfishCredentialRole]] = []

    def provider(
        host: RedfishHost,
        role: RedfishCredentialRole,
    ) -> RedfishConnection:
        calls.append((host, role))
        return cast(RedfishConnection, object())

    configure_redfish_connection(provider)

    with pytest.raises(
        ValueError,
        match="^Unsupported Redfish credential role: 'unsupported'$",
    ):
        get_redfish_connection(
            _host(),
            cast(RedfishCredentialRole, "unsupported"),
        )

    assert calls == []


def test_redfish_provider_receives_both_roles_and_is_replaceable() -> None:
    """Each getter resolves the current provider with the host and explicit role."""
    host = _host()
    first = cast(RedfishConnection, object())
    second = cast(RedfishConnection, object())
    calls: list[tuple[RedfishHost, RedfishCredentialRole]] = []

    def first_provider(
        host_arg: RedfishHost,
        role: RedfishCredentialRole,
    ) -> RedfishConnection:
        calls.append((host_arg, role))
        return first

    def second_provider(
        host_arg: RedfishHost,
        role: RedfishCredentialRole,
    ) -> RedfishConnection:
        calls.append((host_arg, role))
        return second

    configure_redfish_connection(first_provider)
    assert get_redfish_connection(host, "default") is first
    assert get_redfish_connection(host, "config_manager") is first

    configure_redfish_connection(second_provider)
    assert get_redfish_connection(host, "config_manager") is second
    assert calls == [
        (host, "default"),
        (host, "config_manager"),
        (host, "config_manager"),
    ]


def test_configure_runtime_applies_redfish_provider() -> None:
    """The aggregate runtime entry point installs the Redfish dependency."""
    connection = cast(RedfishConnection, object())
    host = _host()

    configure_runtime(
        nats_provider=None,
        slack_provider=None,
        ui_base_url_provider=None,
        lock_backend_provider=None,
        redfish_connection_provider=lambda _host, _credential_role: connection,
    )

    assert get_redfish_connection(host, "default") is connection
