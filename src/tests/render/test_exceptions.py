# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Tests for classifying render dependency failures."""

from unittest.mock import MagicMock

import aiohttp
import nats.errors
import nats.js.errors
import pytest

from nv_config_manager.common.client.config_store import (
    ConfigStoreException,
    ConfigStoreFileNotFound,
    ConfigStoreUnavailableError,
)
from nv_config_manager.dcim import (
    DCIMAuthorizationError,
    DCIMConnectivityError,
    DCIMError,
    DCIMInvalidDataError,
    DCIMNotFoundError,
    DCIMReadCancelledError,
)
from nv_config_manager.render.exceptions import RenderException, is_transient_dependency_error


def _response_error(status: int) -> aiohttp.ClientResponseError:
    return aiohttp.ClientResponseError(request_info=MagicMock(), history=(), status=status)


@pytest.mark.parametrize(
    "exc",
    [
        DCIMConnectivityError("unreachable"),
        DCIMReadCancelledError("cancelled"),
        ConfigStoreUnavailableError("unavailable"),
        aiohttp.ClientConnectionError("refused"),
        TimeoutError(),
        _response_error(500),
        _response_error(503),
        _response_error(429),
        nats.errors.TimeoutError(),
        nats.errors.NoRespondersError(),
        nats.errors.NoServersError(),
        nats.errors.ConnectionClosedError(),
        nats.errors.ConnectionDrainingError(),
        nats.errors.ConnectionReconnectingError(),
        nats.errors.UnexpectedEOF(),
        nats.errors.OutboundBufferLimitError(),
        nats.js.errors.NoStreamResponseError(),
        nats.js.errors.ServiceUnavailableError(code=503),
        nats.js.errors.ServerError(code=500),
    ],
)
def test_failures_that_may_succeed_later_are_transient(exc: Exception) -> None:
    assert is_transient_dependency_error(exc)


@pytest.mark.parametrize(
    "exc",
    [
        DCIMError("graphql error"),
        DCIMInvalidDataError("no primary IP"),
        DCIMNotFoundError("missing"),
        DCIMAuthorizationError("denied"),
        ConfigStoreException("bad request"),
        ConfigStoreFileNotFound("missing"),
        RenderException("template failed"),
        _response_error(400),
        _response_error(404),
        KeyError("commit_message"),
        nats.errors.BadSubjectError(),
        nats.errors.MaxPayloadError(),
        nats.errors.AuthorizationError(),
        nats.js.errors.BadRequestError(code=400),
        nats.js.errors.NotFoundError(code=404),
    ],
)
def test_data_and_request_errors_are_not_transient(exc: Exception) -> None:
    assert not is_transient_dependency_error(exc)
