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
"""Hardware Validation Device Connection Tests.

Tests for platform environment endpoints using real API response data.
"""

import pytest
import responses
from requests.exceptions import RetryError
from temporalio import workflow

from tests.temporal.clients.device.hardware_validation_data import (
    API_ERROR_RESPONSE,
    FAN_RESPONSE,
    INVENTORY_RESPONSE,
    LED_RESPONSE,
    PLATFORM_RESPONSE,
    TEMPERATURE_RESPONSE,
    TEST_DEVICE,
    VOLTAGE_RESPONSE,
)

with workflow.unsafe.imports_passed_through():
    from nv_config_manager.temporal.client.device import (
        CumulusConnection,
        NetworkDeviceException,
    )


@pytest.fixture
def cumulus_connection():
    """Create a CumulusConnection for testing."""
    return CumulusConnection(TEST_DEVICE.primary_ip4)


@responses.activate
def test_get_platform(cumulus_connection):
    """Test platform endpoint calls correct API and returns data."""
    responses.add(
        responses.GET,
        f"https://{TEST_DEVICE.primary_ip4}:8765/nvue_v1/platform",
        json=PLATFORM_RESPONSE,
        status=200,
    )

    result = cumulus_connection.get_platform()

    assert isinstance(result, dict)
    assert result
    assert len(responses.calls) == 1
    assert "nvue_v1/platform" in responses.calls[0].request.url


@responses.activate
def test_get_platform_environment_fan(cumulus_connection):
    """Test platform fan endpoint calls correct API and returns data."""
    responses.add(
        responses.GET,
        f"https://{TEST_DEVICE.primary_ip4}:8765/nvue_v1/platform/environment/fan",
        json=FAN_RESPONSE,
        status=200,
    )

    result = cumulus_connection.get_platform_environment_fan()

    assert isinstance(result, dict)
    assert result
    assert len(responses.calls) == 1
    assert "platform/environment/fan" in responses.calls[0].request.url


@responses.activate
def test_get_platform_environment_led(cumulus_connection):
    """Test platform LED endpoint calls correct API and returns data."""
    responses.add(
        responses.GET,
        f"https://{TEST_DEVICE.primary_ip4}:8765/nvue_v1/platform/environment/led",
        json=LED_RESPONSE,
        status=200,
    )

    result = cumulus_connection.get_platform_environment_led()

    assert isinstance(result, dict)
    assert result
    assert len(responses.calls) == 1
    assert "platform/environment/led" in responses.calls[0].request.url


@responses.activate
def test_get_platform_environment_psu(cumulus_connection):
    """Test platform PSU endpoint calls correct API and returns data."""
    responses.add(
        responses.GET,
        f"https://{TEST_DEVICE.primary_ip4}:8765/nvue_v1/platform/environment/psu",
        json=TEMPERATURE_RESPONSE,
        status=200,
    )

    result = cumulus_connection.get_platform_environment_psu()

    assert isinstance(result, dict)
    assert result
    assert len(responses.calls) == 1
    assert "platform/environment/psu" in responses.calls[0].request.url


@responses.activate
def test_get_platform_environment_voltage(cumulus_connection):
    """Test platform voltage endpoint calls correct API and returns data."""
    responses.add(
        responses.GET,
        f"https://{TEST_DEVICE.primary_ip4}:8765/nvue_v1/platform/environment/voltage",
        json=VOLTAGE_RESPONSE,
        status=200,
    )

    result = cumulus_connection.get_platform_environment_voltage()

    assert isinstance(result, dict)
    assert result
    assert len(responses.calls) == 1
    assert "platform/environment/voltage" in responses.calls[0].request.url


@responses.activate
def test_get_platform_inventory(cumulus_connection):
    """Test platform inventory endpoint calls correct API and returns data."""
    responses.add(
        responses.GET,
        f"https://{TEST_DEVICE.primary_ip4}:8765/nvue_v1/platform/inventory",
        json=INVENTORY_RESPONSE,
        status=200,
    )

    result = cumulus_connection.get_platform_inventory()

    assert isinstance(result, dict)
    assert result
    assert len(responses.calls) == 1
    assert "platform/inventory" in responses.calls[0].request.url


@responses.activate
def test_platform_error_handling(cumulus_connection):
    """Test error handling for platform endpoints."""
    responses.add(
        responses.GET,
        f"https://{TEST_DEVICE.primary_ip4}:8765/nvue_v1/platform",
        json=API_ERROR_RESPONSE,
        status=500,
    )

    with pytest.raises(RetryError):
        cumulus_connection.get_platform()


@responses.activate
def test_platform_fan_error_handling(cumulus_connection):
    """Test error handling for fan endpoint."""
    responses.add(
        responses.GET,
        f"https://{TEST_DEVICE.primary_ip4}:8765/nvue_v1/platform/environment/fan",
        status=404,
    )

    with pytest.raises(NetworkDeviceException):
        cumulus_connection.get_platform_environment_fan()
