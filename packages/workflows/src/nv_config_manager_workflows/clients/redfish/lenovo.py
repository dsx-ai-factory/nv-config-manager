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
"""Lenovo Redfish connection."""

import logging
from collections.abc import Callable

import requests
from nv_config_manager_logging import LogCategory, get_logger

from nv_config_manager_workflows.clients.redfish.base import RedfishConnection
from nv_config_manager_workflows.clients.redfish.models import RedfishHost, RedfishNic

logger = get_logger(__name__, category=LogCategory.TEMPORAL_ACTIVITY)
logger.setLevel(logging.INFO)


class LenovoRedfishConnection(RedfishConnection):
    """Lenovo Redfish connection class."""

    def __init__(
        self,
        host: RedfishHost,
        username: str,
        password: str,
        config_manager_password: Callable[[], str],
    ) -> None:
        """Init method."""
        super().__init__(
            host=host,
            username=username,
            password=password,
        )
        self.initial_password = password
        self.config_manager_password = config_manager_password

    def set_config_manager_password(self) -> requests.Response:
        """Set new password."""
        password = self.config_manager_password()
        rsp = self.patch(
            path="AccountService/Accounts/1",
            payload={"Password": password},
        )
        rsp.raise_for_status()
        self.password = password
        return rsp

    def factory_reset(self) -> requests.Response:
        """Factory Reset."""
        rsp = self.post(
            path="Managers/1/Actions/Manager.ResetToDefaults",
            payload={"ResetType": "ResetAll"},
        )
        rsp.raise_for_status()
        self.password = self.initial_password
        self.wait_for_restart()
        return rsp

    def power_on_chassis(self) -> requests.Response:
        """Power on chassis."""
        rsp = self.post(
            path="Systems/1/Actions/ComputerSystem.Reset",
            payload={"ResetType": "On"},
        )
        rsp.raise_for_status()
        self.wait_for_power_on()
        return rsp

    def power_off_chassis(self) -> requests.Response:
        """Power off chassis."""
        rsp = self.post(
            path="Systems/1/Actions/ComputerSystem.Reset",
            payload={"ResetType": "GracefulShutdown"},
        )
        rsp.raise_for_status()
        return rsp

    def get_redfish_data(self) -> requests.Response:
        """Get data about the Redfish manager."""
        rsp = self.get(path="Managers/1")
        rsp.raise_for_status()
        return rsp

    def is_host_powered_on(self) -> bool:
        """Check if chassis is powered on."""
        logger.debug("Checking power status for %s...", self.url)
        rsp = self.get(path="Systems/1")
        logger.debug("Host %s power state: %s", self.url, rsp.json().get("PowerState"))
        rsp.raise_for_status()
        return rsp.ok and rsp.json().get("PowerState") == "On"

    def get_network_adapters(self) -> requests.Response:
        """Get network adapters."""
        rsp = self.get(path="Chassis/1/NetworkAdapters")
        rsp.raise_for_status()
        return rsp

    def get_network_adapter_details(self, network_adapter: str) -> requests.Response:
        """Get network adapter details."""
        rsp = self.get(path=f"Chassis/1/NetworkAdapters/{network_adapter}")
        rsp.raise_for_status()
        return rsp

    def get_port_detail(self, network_adapter: str, port_name: str) -> requests.Response:
        """Get port details."""
        rsp = self.get(path=(f"Chassis/1/NetworkAdapters/{network_adapter}/Ports/{port_name}"))
        rsp.raise_for_status()
        return rsp

    def get_nic_info(self, manufacturers: list[str] | None = None) -> list[RedfishNic]:
        """Get details about all network interface cards.

        Optionally specify a NIC manufacturer to limit results.
        """
        results = []
        for adapter in self.get_network_adapters().json().get("Members", []):
            adapter_name = adapter["@odata.id"].split("/")[-1]
            adapter_data = self.get_network_adapter_details(adapter_name).json()
            if not manufacturers or adapter_data.get("Manufacturer") in manufacturers:
                for controller_data in adapter_data.get("Controllers", []):
                    ports_seen = []
                    for port in controller_data["Links"].get("Ports", []):
                        port_name = port["@odata.id"].split("/")[-1]
                        if port_name in ports_seen:
                            # BMCs sometimes report duplicate ports
                            continue
                        ports_seen.append(port_name)
                        port_data = self.get_port_detail(
                            adapter_name,
                            port_name,
                        ).json()
                        results.append(
                            RedfishNic(
                                name=port_name,
                                slot=adapter_name,
                                mac=(
                                    port_data["Oem"]["Lenovo"].get("PhysicalPortMacAddress")
                                    if port_data.get("Oem") and port_data["Oem"].get("Lenovo")
                                    else None
                                ),
                            )
                        )

        return results

    def get_chassis(self) -> requests.Response:
        """Get chassis data."""
        rsp = self.get(path="Chassis/1")
        rsp.raise_for_status()
        return rsp

    def get_serial(self) -> str:
        """Get serial."""
        rsp = self.get(path="Systems/1")
        rsp.raise_for_status()
        return str(rsp.json()["SerialNumber"])
