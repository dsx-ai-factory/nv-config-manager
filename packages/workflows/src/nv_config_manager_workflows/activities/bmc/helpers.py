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
"""Reusable helpers for BMC activities."""

import logging

import aiohttp
import aiohttp.client_exceptions
from nv_config_manager_logging import LogCategory, get_logger

from nv_config_manager_workflows.clients.device.models import DeviceArpTable

logger = get_logger(__name__, category=LogCategory.TEMPORAL_ACTIVITY)
logger.setLevel(logging.INFO)


def combine_arp_tables(tables: list[DeviceArpTable]) -> DeviceArpTable:
    """Combine multiple device ARP tables into one."""
    result = DeviceArpTable()
    for table in tables:
        for address, macs in table.ip_to_mac.items():
            if address not in result.ip_to_mac:
                result.ip_to_mac[address] = []
            for mac in macs:
                if mac not in result.ip_to_mac[address]:
                    result.ip_to_mac[address].append(mac)
    return result


async def http_get(session: aiohttp.ClientSession, url: str) -> aiohttp.ClientResponse | None:
    """Fetch and eagerly decode a Redfish discovery response."""
    try:
        response = await session.get(url, ssl=False)
        await response.json()
    except TimeoutError as error:
        logger.debug("Timeout to %s: %s", url, str(error))
        return None
    except aiohttp.client_exceptions.ClientConnectionError as error:
        logger.debug("Error connecting to %s: %s", url, str(error))
        return None
    logger.debug("Established connection to %s", url)
    return response if response.ok else None


__all__ = ["combine_arp_tables", "http_get"]
