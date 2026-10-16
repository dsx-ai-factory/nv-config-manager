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
"""InfiniBand GUID transport tests for the Nautobot DCIM provider."""

from aioresponses import aioresponses
from nv_config_manager_dcim_nautobot_2x.workflow import NautobotWorkflowClient

_NAUTOBOT_URL = "https://nautobot.example.com"
_INTERFACE_ID = "interface-id"
_GUID = "0x0002c903000a0a01"


def _client() -> NautobotWorkflowClient:
    return NautobotWorkflowClient(
        {
            "server": _NAUTOBOT_URL,
            "token": "test-token",
            "verify": False,
        }
    )


async def test_get_ib_interface_guid_normalizes_nautobot_payload() -> None:
    client = _client()
    with aioresponses() as mocked:
        mocked.get(
            f"{_NAUTOBOT_URL}/api/dcim/interfaces/{_INTERFACE_ID}/",
            payload={
                "id": _INTERFACE_ID,
                "name": "HCA-5/1",
                "device": {"display": "dgx-03", "name": "dgx-03.example.com"},
                "custom_fields": {"ib_guid": _GUID},
            },
        )

        async with client:
            result = await client.get_ib_interface_guid(_INTERFACE_ID)

    assert result.interface_id == _INTERFACE_ID
    assert result.device_name == "dgx-03"
    assert result.interface_name == "HCA-5/1"
    assert result.guid == _GUID


async def test_set_ib_interface_guid_sends_custom_field_patch() -> None:
    client = _client()
    with aioresponses() as mocked:
        mocked.patch(
            f"{_NAUTOBOT_URL}/api/dcim/interfaces/{_INTERFACE_ID}/",
            payload={"id": _INTERFACE_ID},
        )

        async with client:
            await client.set_ib_interface_guid(_INTERFACE_ID, _GUID)

        patch_calls = [
            calls for (method, _), calls in mocked.requests.items() if method.lower() == "patch"
        ]

    assert len(patch_calls) == 1
    assert patch_calls[0][0].kwargs["json"] == {"custom_fields": {"ib_guid": _GUID}}
