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
"""Tests for the reusable DCIM activity lifecycle boundary."""

from __future__ import annotations

from typing import cast

import pytest
from nv_config_manager_dcim.api import DCIMClient
from nv_config_manager_dcim.errors import DCIMError
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.activities.dcim import dcim_client_session
from nv_config_manager_workflows.runtime import (
    DCIMNotConfiguredError,
    configure_dcim_client,
)


class StubDCIMClient:
    """Minimal async context manager with observable lifecycle behavior."""

    def __init__(
        self,
        *,
        enter_error: BaseException | None = None,
        exit_error: BaseException | None = None,
    ) -> None:
        self.enter_error = enter_error
        self.exit_error = exit_error
        self.entered = False
        self.exited = False
        self.exit_exception: BaseException | None = None

    async def __aenter__(self) -> StubDCIMClient:
        self.entered = True
        if self.enter_error is not None:
            raise self.enter_error
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        self.exited = True
        self.exit_exception = exc_value
        if self.exit_error is not None:
            raise self.exit_error


class PermanentDCIMError(DCIMError):
    """Provider failure that Temporal must not retry."""

    non_retryable = True


def _configure_client(client: StubDCIMClient) -> None:
    configure_dcim_client(lambda: cast(DCIMClient, client))


async def test_session_enters_yields_and_exits_the_runtime_client() -> None:
    """Successful operations use the provider client's complete async lifecycle."""
    client = StubDCIMClient()
    _configure_client(client)

    async with dcim_client_session() as yielded:
        assert yielded is client
        assert client.entered is True
        assert client.exited is False

    assert client.exited is True
    assert client.exit_exception is None


@pytest.mark.parametrize(
    ("provider_error", "expected_non_retryable"),
    [
        (DCIMError("provider unavailable"), False),
        (PermanentDCIMError("invalid provider data"), True),
    ],
)
async def test_session_translates_provider_operation_errors(
    provider_error: DCIMError,
    expected_non_retryable: bool,
) -> None:
    """Provider errors preserve their message and retryability at the Temporal boundary."""
    client = StubDCIMClient()
    _configure_client(client)

    with pytest.raises(ApplicationError) as exc_info:
        async with dcim_client_session():
            raise provider_error

    assert exc_info.value.message == str(provider_error)
    assert exc_info.value.non_retryable is expected_non_retryable
    assert exc_info.value.__cause__ is provider_error
    assert client.exited is True
    assert client.exit_exception is provider_error


@pytest.mark.parametrize("failure_point", ["enter", "exit"])
async def test_session_translates_provider_lifecycle_errors(failure_point: str) -> None:
    """Client entry and cleanup share the same Temporal error contract."""
    provider_error = PermanentDCIMError(f"{failure_point} failed")
    client = StubDCIMClient(
        enter_error=provider_error if failure_point == "enter" else None,
        exit_error=provider_error if failure_point == "exit" else None,
    )
    _configure_client(client)

    with pytest.raises(ApplicationError) as exc_info:
        async with dcim_client_session():
            pass

    assert exc_info.value.message == f"{failure_point} failed"
    assert exc_info.value.non_retryable is True
    assert exc_info.value.__cause__ is provider_error


async def test_session_preserves_client_factory_errors() -> None:
    """Match the service boundary by leaving failures before client creation unchanged."""
    provider_error = PermanentDCIMError("factory failed")

    def client_provider() -> DCIMClient:
        raise provider_error

    configure_dcim_client(client_provider)

    with pytest.raises(PermanentDCIMError) as exc_info:
        async with dcim_client_session():
            pass

    assert exc_info.value is provider_error


async def test_session_does_not_translate_non_provider_errors() -> None:
    """Programming failures remain their original exception while still closing the client."""
    client = StubDCIMClient()
    failure = ValueError("bad activity input")
    _configure_client(client)

    with pytest.raises(ValueError) as exc_info:
        async with dcim_client_session():
            raise failure

    assert exc_info.value is failure
    assert client.exited is True
    assert client.exit_exception is failure


async def test_session_preserves_named_unconfigured_runtime_error(
    unconfigured_workflow_runtime: None,
) -> None:
    """Missing startup wiring remains a concrete non-retryable runtime failure."""
    with pytest.raises(DCIMNotConfiguredError, match="configure_dcim_client") as exc_info:
        async with dcim_client_session():
            pass

    assert exc_info.value.non_retryable is True
