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
"""Unit tests for reusable UFM activities."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

from nv_config_manager_workflows.activities.ufm import (
    GetUFMPortsInput,
    get_ib_ports,
)
from nv_config_manager_workflows.activities.ufm.helpers import _generate_ports_csv
from nv_config_manager_workflows.clients.ufm import UFMClient
from nv_config_manager_workflows.runtime import configure_ufm_client


def _ufm_client() -> MagicMock:
    client = MagicMock(spec=UFMClient)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.get_ports = AsyncMock()
    configure_ufm_client(lambda _host, _site: cast(UFMClient, client))
    return client


async def test_get_ib_ports_uses_runtime_client_and_preserves_csv_shape() -> None:
    client = _ufm_client()
    ports = [
        {
            "system_name": "ufm-1",
            "port": "1",
            "label": "leaf-1",
            "description": "uplink",
            "physical_state": "Active",
            "logical_state": "Active",
            "peer_node_name": "leaf-1",
            "peer_port": "1",
            "peer_node_description": "switch",
            "guid": "guid-1",
        }
    ]
    client.get_ports.return_value = ports

    output = await get_ib_ports(
        GetUFMPortsInput(host="ufm.example.test", site="site-1", unhealthy=True)
    )

    assert output.ports == ports
    assert output.display == "UFM ports retrieved successfully."
    assert output.csv_data == (
        "system_name,port,label,description,physical_state,logical_state,"
        "peer_node_name,peer_port,peer_node_description,guid\r\n"
        "ufm-1,1,leaf-1,uplink,Active,Active,leaf-1,1,switch,guid-1\r\n"
    )
    client.get_ports.assert_awaited_once_with(unhealthy_only=True)
    client.__aenter__.assert_awaited_once()
    client.__aexit__.assert_awaited_once()


def test_generate_ports_csv_preserves_empty_output() -> None:
    assert _generate_ports_csv([]) == ""
