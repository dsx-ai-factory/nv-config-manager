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
"""Lenovo Redfish connection implementation."""

from __future__ import annotations

import logging

import requests

from nv_config_manager_workflows.clients.redfish.base import RedfishConnection
from nv_config_manager_workflows.clients.redfish.models import RedfishHost, RedfishNic

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class LenovoRedfishConnection(RedfishConnection):
    """Lenovo Redfish connection."""

    def __init__(
        self,
        host: RedfishHost,
        username: str,
        password: str,
        config_manager_password: str,
    ) -> None:
        super().__init__(host, username, password, config_manager_password)
        self.initial_password = password

    def set_config_manager_password(self) -> requests.Response:
        """Set the service-managed password."""
        response = self.patch(
            path="AccountService/Accounts/1",
            payload={"Password": self.config_manager_password},
        )
        response.raise_for_status()
        self.password = self.config_manager_password
        return response

    def factory_reset(self) -> requests.Response:
        """Factory-reset the controller."""
        response = self.post(
            path="Managers/1/Actions/Manager.ResetToDefaults",
            payload={"ResetType": "ResetAll"},
        )
        response.raise_for_status()
        self.password = self.initial_password
        self.wait_for_restart()
        return response

    def power_on_chassis(self) -> requests.Response:
        """Power on the chassis."""
        response = self.post(
            path="Systems/1/Actions/ComputerSystem.Reset",
            payload={"ResetType": "On"},
        )
        response.raise_for_status()
        self.wait_for_power_on()
        return response

    def power_off_chassis(self) -> requests.Response:
        """Power off the chassis."""
        response = self.post(
            path="Systems/1/Actions/ComputerSystem.Reset",
            payload={"ResetType": "GracefulShutdown"},
        )
        response.raise_for_status()
        return response

    def get_redfish_data(self) -> requests.Response:
        """Return manager data."""
        response = self.get(path="Managers/1")
        response.raise_for_status()
        return response

    def is_host_powered_on(self) -> bool:
        """Return whether the chassis is powered on."""
        logger.debug("Checking power status for %s...", self.url)
        response = self.get(path="Systems/1")
        logger.debug("Host %s power state: %s", self.url, response.json().get("PowerState"))
        response.raise_for_status()
        return response.ok and response.json().get("PowerState") == "On"

    def get_network_adapters(self) -> requests.Response:
        """Return network adapters."""
        response = self.get(path="Chassis/1/NetworkAdapters")
        response.raise_for_status()
        return response

    def get_network_adapter_details(self, network_adapter: str) -> requests.Response:
        """Return network-adapter details."""
        response = self.get(path=f"Chassis/1/NetworkAdapters/{network_adapter}")
        response.raise_for_status()
        return response

    def get_port_detail(self, network_adapter: str, port_name: str) -> requests.Response:
        """Return port details."""
        response = self.get(path=f"Chassis/1/NetworkAdapters/{network_adapter}/Ports/{port_name}")
        response.raise_for_status()
        return response

    def get_nic_info(self, manufacturers: list[str] | None = None) -> list[RedfishNic]:
        """Return details about matching network interfaces."""
        results: list[RedfishNic] = []
        for adapter in self.get_network_adapters().json().get("Members", []):
            adapter_name = adapter["@odata.id"].split("/")[-1]
            adapter_data = self.get_network_adapter_details(adapter_name).json()
            if manufacturers and adapter_data.get("Manufacturer") not in manufacturers:
                continue
            for controller_data in adapter_data.get("Controllers", []):
                ports_seen: list[str] = []
                for port in controller_data["Links"].get("Ports", []):
                    port_name = port["@odata.id"].split("/")[-1]
                    if port_name in ports_seen:
                        continue
                    ports_seen.append(port_name)
                    port_data = self.get_port_detail(adapter_name, port_name).json()
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
        """Return chassis data."""
        response = self.get(path="Chassis/1")
        response.raise_for_status()
        return response

    def get_serial(self) -> str:
        """Return the serial number."""
        response = self.get(path="Systems/1")
        response.raise_for_status()
        return str(response.json()["SerialNumber"])
