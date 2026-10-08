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
"""Security regression tests for the Nautobot provider client."""

import logging

import pytest
from aioresponses import aioresponses
from nv_config_manager_dcim_nautobot_2x.client import NautobotClient
from nv_config_manager_dcim_nautobot_2x.workflow import NautobotWorkflowClient


@pytest.mark.parametrize(
    "path",
    [
        "../admin/",
        "dcim/devices/../../users/",
        "/admin/",
        "//attacker.example/admin/",
        "https://attacker.example/admin/",
        "dcim/%2e%2e/users/",
        "dcim/devices/id?depth=1",
        "dcim/devices/id\nforged-log-entry/",
    ],
)
def test_rest_url_rejects_untrusted_path_components(path):
    """REST requests cannot escape or reinterpret the configured API root."""
    client = NautobotClient("https://nautobot.example", token="token")

    with pytest.raises(ValueError, match="safe relative path"):
        client._rest_url(path)


def test_rest_url_accepts_provider_routes():
    """Expected Nautobot routes remain relative to the configured API root."""
    client = NautobotClient("https://nautobot.example", token="token")

    assert (
        client._rest_url("plugins/overlays/overlay-assignments/record_1/")
        == "https://nautobot.example/api/plugins/overlays/overlay-assignments/record_1/"
    )


@pytest.mark.asyncio
async def test_patch_rejects_an_unsafe_path_before_opening_a_session():
    """The vulnerable request sink rejects traversal before making a connection."""
    client = NautobotClient("https://nautobot.example", token="token")

    with pytest.raises(ValueError, match="safe relative path"):
        await client.patch("dcim/devices/../../users/", data={})

    assert client._session is None


@pytest.mark.asyncio
async def test_backup_config_rejects_unsafe_device_id_before_opening_a_session():
    """Backup lookups validate device IDs before making a connection."""
    client = NautobotWorkflowClient(
        {"server": "https://nautobot.example", "token": "token", "verify": True}
    )

    with pytest.raises(ValueError, match="safe relative path"):
        await client.load_config_manager_plugin_backup_config("../../users")

    assert client._session is None


@pytest.mark.asyncio
async def test_graphql_logs_exclude_query_and_variable_data(caplog):
    """GraphQL diagnostics do not allow caller data to forge log entries."""
    client = NautobotWorkflowClient(
        {"server": "https://nautobot.example", "token": "token", "verify": True}
    )
    query = "query { devices { id } }\nforged-query-entry"
    variables = {"device": "device-id\nforged-variable-entry"}

    with aioresponses() as mocked:
        mocked.post("https://nautobot.example/api/graphql/", payload={"data": {}})
        with caplog.at_level(logging.DEBUG):
            async with client:
                await client.graphql_query(query, variables)

    assert "Sending GraphQL query to Nautobot" in caplog.text
    assert "forged-query-entry" not in caplog.text
    assert "forged-variable-entry" not in caplog.text
