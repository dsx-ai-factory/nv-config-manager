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
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from nv_config_manager_dcim_nautobot_2x.provider import NautobotDCIMClient as NautobotClient


@pytest.mark.asyncio
async def test_ztp_device_data(mock_device_data):
    with patch(
        "nv_config_manager_dcim_nautobot_2x.provider.NautobotDCIMClient.graphql_query",
        new_callable=AsyncMock,
        return_value=mock_device_data,
    ):
        nb = NautobotClient(nautobot_url="https://nautobot.example", token="token")
        async with nb:
            device_data = await nb.get_ztp_device(str(uuid4()))

        assert device_data.device_id == "80ce0a9a-d3c8-5b8e-b755-e9c16d92237b"
        assert device_data.name == "rno1-m04-c10-spine1-hss-tan-lab1"
        assert device_data.addresses == ["10.180.166.13", "10.180.166.130"]
        assert device_data.platform_name == "Cumulus Linux"
        assert device_data.firmware_version == "5.7.0"
        assert device_data.config_store_instance == "https://config-manager.example.com/"
