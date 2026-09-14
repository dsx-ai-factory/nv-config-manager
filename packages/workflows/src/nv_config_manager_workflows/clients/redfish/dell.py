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
"""Dell Redfish connection implementation."""

from __future__ import annotations

import logging

import requests
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.clients.redfish.base import RedfishConnection
from nv_config_manager_workflows.clients.redfish.models import RedfishNic

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class DellRedfishConnection(RedfishConnection):
    """Dell Redfish connection."""

    def set_config_manager_password(self) -> None:
        """Reject password changes for Dell controllers."""
        raise ApplicationError(f"BMC password should not be changed for Dell: {self.url}")

    def factory_reset(self) -> None:
        """Reject factory resets for Dell controllers."""
        raise ApplicationError(f"BMC factory reset should not be performed Dell: {self.url}")

    def power_on_chassis(self) -> requests.Response:
        """Power on the chassis."""
        response = self.post(
            path="Systems/System.Embedded.1/Actions/ComputerSystem.Reset",
            payload={"ResetType": "On"},
        )
        response.raise_for_status()
        self.wait_for_power_on()
        return response

    def power_off_chassis(self) -> requests.Response:
        """Power off the chassis."""
        response = self.post(
            path="Systems/System.Embedded.1/Actions/ComputerSystem.Reset",
            payload={"ResetType": "GracefulShutdown"},
        )
        response.raise_for_status()
        return response

    def get_redfish_data(self) -> requests.Response:
        """Return manager data."""
        response = self.get(path="Managers/iDRAC.Embedded.1")
        response.raise_for_status()
        return response

    def is_host_powered_on(self) -> bool:
        """Return whether the chassis is powered on."""
        logger.debug("Checking power status for %s...", self.url)
        response = self.get(path="Systems/System.Embedded.1")
        logger.debug("Host %s power state: %s", self.url, response.json().get("PowerState"))
        response.raise_for_status()
        return response.ok and response.json().get("PowerState") == "On"

    def get_network_adapters(self) -> requests.Response:
        """Return network adapters."""
        response = self.get(path="Chassis/System.Embedded.1/NetworkAdapters")
        response.raise_for_status()
        return response

    def get_network_adapter_details(self, network_adapter: str) -> requests.Response:
        """Return network-adapter details."""
        response = self.get(path=f"Chassis/System.Embedded.1/NetworkAdapters/{network_adapter}")
        response.raise_for_status()
        return response

    def get_network_device_function_detail(
        self,
        network_adapter: str,
        network_device_functon: str,
    ) -> requests.Response:
        """Return network-device-function details."""
        response = self.get(
            path=(
                f"Chassis/System.Embedded.1/NetworkAdapters/{network_adapter}"
                f"/NetworkDeviceFunctions/{network_device_functon}"
            )
        )
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
                network_functions_seen: list[str] = []
                for network_function in controller_data["Links"].get(
                    "NetworkDeviceFunctions",
                    [],
                ):
                    network_function_name = network_function["@odata.id"].split("/")[-1]
                    if network_function_name in network_functions_seen:
                        continue
                    network_functions_seen.append(network_function_name)
                    function_data = self.get_network_device_function_detail(
                        adapter_name,
                        network_function_name,
                    ).json()
                    results.append(
                        RedfishNic(
                            name=network_function_name,
                            slot=adapter_name,
                            mac=(
                                function_data["Ethernet"].get("MACAddress")
                                if function_data.get("Ethernet")
                                else None
                            ),
                        )
                    )
        return results

    def get_chassis(self) -> requests.Response:
        """Return chassis data."""
        response = self.get(path="Chassis/System.Embedded.1")
        response.raise_for_status()
        return response

    def get_serial(self) -> str:
        """Return the serial number."""
        response = self.get(path="Systems/System.Embedded.1")
        response.raise_for_status()
        return str(response.json()["SerialNumber"])
