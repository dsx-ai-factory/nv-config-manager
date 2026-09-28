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
"""Test UFM activity."""

from configparser import ConfigParser
from unittest.mock import patch

import aiohttp
import pytest
from aioresponses import aioresponses

from nv_config_manager.temporal.client.ufm import UFMAuthError, UFMClientError
from nv_config_manager.temporal.common.secrets import clear_secrets_cache
from nv_config_manager.temporal.ngc.activities.ufm import (
    GetUFMPortsInput,
    get_ib_ports,
)

UFM_HEALTHY_PORTS = [
    {
        "number": "1",
        "label": "Port 1",
        "physical_state": "Link Up",
        "logical_state": "Active",
        "system_name": "System1",
        "node_description": "Node 1",
        "peer_node_name": "Peer1",
        "peer_node_description": "Peer Node 1",
    }
]

UFM_UNHEALTHY_PORTS = [
    {
        "number": "1",
        "label": "Port 1",
        "physical_state": "Link Down",
        "logical_state": "Inactive",
        "system_name": "System1",
        "node_description": "Node 1",
        "peer_node_name": "Peer1",
        "peer_node_description": "Peer Node 1",
    }
]


def _create_config(sections: dict[str, dict[str, str]]) -> ConfigParser:
    """Create a ConfigParser from a dict of sections."""
    config = ConfigParser()
    for section, values in sections.items():
        config.add_section(section)
        for key, value in values.items():
            config.set(section, key, value)
    return config


@pytest.fixture(autouse=True)
def reset_secrets_cache():
    """Clear the secrets config cache before and after each test."""
    clear_secrets_cache()
    yield
    clear_secrets_cache()


@pytest.mark.asyncio
@patch("nv_config_manager.common.config.loader.load_config")
async def test_get_ib_ports_success_healthy_ports(mock_config):
    """Test get_ib_ports with healthy ports."""
    mock_config.return_value = _create_config(
        {"ufm": {"ufm_api_user": "user", "ufm_api_token_r1": "pass"}}
    )
    with aioresponses() as m:
        m.get(
            "https://test-host/ufmRest/resources/ports",
            status=200,
            payload=UFM_HEALTHY_PORTS,
        )

        input_data = GetUFMPortsInput(
            host="test-host",
            unhealthy=False,
        )

        result = await get_ib_ports(input_data)

        assert len(result.ports) == 1
        assert result.ports[0]["port"] == "1"
        assert result.ports[0]["physical_state"] == "Link Up"
        assert result.ports[0]["logical_state"] == "Active"
        assert (
            "system_name,port,label,description,physical_state,logical_state,peer_node_name,peer_port,peer_node_description"
            in result.csv_data
        )
        assert result.display == "UFM ports retrieved successfully."


@pytest.mark.asyncio
@patch("nv_config_manager.common.config.loader.load_config")
async def test_get_ib_ports_success_unhealthy_ports(mock_config):
    """Test get_ib_ports with unhealthy ports."""
    mock_config.return_value = _create_config(
        {"ufm": {"ufm_api_user": "user", "ufm_api_token_r1": "pass"}}
    )
    with aioresponses() as m:
        m.get(
            "https://test-host/ufmRest/resources/ports",
            status=200,
            payload=UFM_UNHEALTHY_PORTS,
        )

        input_data = GetUFMPortsInput(
            host="test-host",
            unhealthy=True,
        )

        result = await get_ib_ports(input_data)

        assert len(result.ports) == 1
        assert result.ports[0]["port"] == "1"
        assert result.ports[0]["physical_state"] == "Link Down"
        assert result.ports[0]["logical_state"] == "Inactive"
        assert (
            "system_name,port,label,description,physical_state,logical_state,peer_node_name,peer_port,peer_node_description"
            in result.csv_data
        )
        assert result.display == "UFM ports retrieved successfully."


@pytest.mark.asyncio
@patch("nv_config_manager.common.config.loader.load_config")
async def test_get_ib_ports_failure(mock_config):
    """Test get_ib_ports with failed API call."""
    mock_config.return_value = _create_config(
        {"ufm": {"ufm_api_user": "user", "ufm_api_token_r1": "pass"}}
    )
    with aioresponses() as m:
        # Simulate a connection error
        m.get(
            "https://test-host/ufmRest/resources/ports",
            exception=aiohttp.ClientError("Connection refused"),
        )

        input_data = GetUFMPortsInput(
            host="test-host",
            unhealthy=False,
        )

        with pytest.raises(UFMClientError) as exc_info:
            await get_ib_ports(input_data)
        assert "Connection refused" in str(exc_info.value)


@pytest.mark.asyncio
@patch("nv_config_manager.common.config.loader.load_config")
async def test_get_ib_ports_http_error(mock_config):
    """Test get_ib_ports with HTTP error."""
    mock_config.return_value = _create_config(
        {"ufm": {"ufm_api_user": "user", "ufm_api_token_r1": "pass"}}
    )
    with aioresponses() as m:
        m.get(
            "https://test-host/ufmRest/resources/ports",
            status=500,
            body="Internal Server Error",
            repeat=True,
        )

        input_data = GetUFMPortsInput(
            host="test-host",
            unhealthy=False,
        )

        with pytest.raises(UFMClientError) as exc_info:
            await get_ib_ports(input_data)
        assert "500" in str(exc_info.value)


@pytest.mark.asyncio
@patch("nv_config_manager.common.config.loader.load_config")
async def test_get_ib_ports_auth_failure_all_passwords(mock_config):
    """Test get_ib_ports raises UFMAuthError when all passwords fail."""
    mock_config.return_value = _create_config(
        {"ufm": {"ufm_api_user": "user", "ufm_api_token_r1": "bad_pass"}}
    )
    with aioresponses() as m:
        m.get(
            "https://test-host/ufmRest/resources/ports",
            status=401,
            repeat=True,
        )

        input_data = GetUFMPortsInput(
            host="test-host",
            unhealthy=False,
        )

        with pytest.raises(UFMAuthError) as exc_info:
            await get_ib_ports(input_data)
        assert "password attempts failed" in str(exc_info.value)


@pytest.mark.asyncio
@patch("nv_config_manager.common.config.loader.load_config")
async def test_get_ib_ports_password_rotation_success(mock_config):
    """Test get_ib_ports succeeds on second password after first fails."""
    mock_config.return_value = _create_config(
        {
            "ufm": {
                "ufm_api_user": "user",
                "ufm_api_token_r1": "old_pass",
                "ufm_api_token_r2": "new_pass",
            }
        }
    )
    with aioresponses() as m:
        # First password fails with 401
        m.get(
            "https://test-host/ufmRest/resources/ports",
            status=401,
        )
        # Second password succeeds
        m.get(
            "https://test-host/ufmRest/resources/ports",
            status=200,
            payload=UFM_HEALTHY_PORTS,
        )

        input_data = GetUFMPortsInput(
            host="test-host",
            unhealthy=False,
        )

        result = await get_ib_ports(input_data)

        assert len(result.ports) == 1
        assert result.display == "UFM ports retrieved successfully."


@pytest.mark.asyncio
@patch("nv_config_manager.common.config.loader.load_config")
async def test_get_ib_ports_fallback_to_global_credentials(mock_config):
    """Test get_ib_ports falls back to global [ufm] when site section missing."""
    mock_config.return_value = _create_config(
        {
            "ufm": {"ufm_api_user": "global_user", "ufm_api_token_r1": "global_pass"},
            # No site.nonexistent-site section
        }
    )
    with aioresponses() as m:
        m.get(
            "https://test-host/ufmRest/resources/ports",
            status=200,
            payload=UFM_HEALTHY_PORTS,
        )

        input_data = GetUFMPortsInput(
            host="test-host",
            unhealthy=False,
            site="Nonexistent Site",  # Section doesn't exist, should fall back
        )

        result = await get_ib_ports(input_data)

        assert len(result.ports) == 1
        assert result.display == "UFM ports retrieved successfully."
