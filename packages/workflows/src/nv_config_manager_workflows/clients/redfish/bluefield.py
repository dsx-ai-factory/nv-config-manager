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
"""NVIDIA BlueField Redfish connection implementation."""

from __future__ import annotations

import netaddr
import requests

from nv_config_manager_workflows.clients.redfish.base import RedfishConnection
from nv_config_manager_workflows.clients.redfish.models import RedfishHost, RedfishNic


class Bluefield3RedfishConnection(RedfishConnection):
    """BlueField-3 Redfish connection."""

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
            path="AccountService/Accounts/root",
            payload={"Password": self.config_manager_password},
        )
        response.raise_for_status()
        self.password = self.config_manager_password
        return response

    def factory_reset(self) -> requests.Response | None:
        """Factory-reset the controller."""
        try:
            response = self.post(
                path="Managers/Bluefield_BMC/Actions/Manager.ResetToDefaults",
                payload={"ResetToDefaultsType": "ResetAll"},
            )
            response.raise_for_status()
        except requests.exceptions.RequestException:
            response = None
        self.wait_for_restart()
        self.password = self.initial_password
        return response

    def power_on_chassis(self) -> requests.Response:
        """Power on the chassis."""
        response = self.post(
            path="Systems/Bluefield/Actions/ComputerSystem.Reset",
            payload={"ResetType": "On"},
        )
        response.raise_for_status()
        self.wait_for_power_on()
        return response

    def power_off_chassis(self) -> requests.Response:
        """Power off the chassis."""
        response = self.post(
            path="Systems/Bluefield/Actions/ComputerSystem.Reset",
            payload={"ResetType": "GracefulShutdown"},
        )
        response.raise_for_status()
        return response

    def get_redfish_data(self) -> requests.Response:
        """Return manager data."""
        response = self.get(path="Managers/Bluefield_BMC", timeout=30)
        response.raise_for_status()
        return response

    def is_host_powered_on(self) -> bool:
        """Return whether the chassis is powered on."""
        response = self.get(path="Systems/Bluefield")
        response.raise_for_status()
        return response.ok and response.json().get("PowerState") == "On"

    def get_nic_info(self, manufacturers: list[str] | None = None) -> list[RedfishNic]:
        """BlueField NIC discovery is not implemented."""
        raise NotImplementedError

    def get_network_device_functions(self) -> requests.Response:
        """Return network-device functions."""
        response = self.get(
            path="Chassis/Card1/NetworkAdapters/NvidiaNetworkAdapter/NetworkDeviceFunctions"
        )
        response.raise_for_status()
        return response

    def get_network_device_function_details(
        self,
        network_device_function: str,
    ) -> requests.Response:
        """Return network-device-function details."""
        response = self.get(
            path=(
                "Chassis/Card1/NetworkAdapters/NvidiaNetworkAdapter/"
                f"NetworkDeviceFunctions/{network_device_function}"
            )
        )
        response.raise_for_status()
        return response

    def get_chassis(self) -> requests.Response:
        """Return chassis data."""
        response = self.get(path="Chassis/Card1")
        response.raise_for_status()
        return response

    def get_base_mac(self) -> str:
        """Return the DPU base MAC."""
        response = self.get(path="UpdateService/FirmwareInventory/DPU_SYS_IMAGE")
        response.raise_for_status()
        version = response.json()["Version"].strip()
        return str(netaddr.EUI(version[:7] + version[12:]))

    def get_serial(self) -> str:
        """Return the serial number."""
        response = self.get(path="Systems/Bluefield")
        response.raise_for_status()
        return str(response.json()["SerialNumber"]).strip()
