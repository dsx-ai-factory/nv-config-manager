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
"""Bluefield Redfish connection."""

import logging
import time
from collections.abc import Callable

import netaddr
import requests
from nv_config_manager_logging import LogCategory, get_logger
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.clients.redfish.base import RedfishConnection
from nv_config_manager_workflows.clients.redfish.models import RedfishHost, RedfishNic

logger = get_logger(__name__, category=LogCategory.TEMPORAL_ACTIVITY)
logger.setLevel(logging.INFO)


class Bluefield3RedfishConnection(RedfishConnection):
    """Bluefield2 Redfish Connection."""

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
        """Set Redfish password."""
        password = self.config_manager_password()
        rsp = self.patch(
            path="AccountService/Accounts/root",
            payload={"Password": password},
        )
        rsp.raise_for_status()
        self.password = password
        return rsp

    def factory_reset(self) -> requests.Response | None:
        """Factory reset BMC."""
        try:
            rsp = self.post(
                path="Managers/Bluefield_BMC/Actions/Manager.ResetToDefaults",
                payload={"ResetToDefaultsType": "ResetAll"},
            )
            rsp.raise_for_status()
        except requests.exceptions.RequestException:
            # Bluefield cards often reset before responding
            rsp = None
        self.wait_for_restart()
        self.password = self.initial_password
        return rsp

    def power_on_chassis(self) -> requests.Response:
        """Power on chassis."""
        rsp = self.post(
            path="Systems/Bluefield/Actions/ComputerSystem.Reset",
            payload={"ResetType": "On"},
        )
        rsp.raise_for_status()
        self.wait_for_power_on()
        return rsp

    def power_off_chassis(self) -> requests.Response:
        """Power off chassis."""
        rsp = self.post(
            path="Systems/Bluefield/Actions/ComputerSystem.Reset",
            payload={"ResetType": "GracefulShutdown"},
        )
        rsp.raise_for_status()
        return rsp

    def wait_for_power_on(self) -> None:
        """Wait for a restart to finish."""
        logger.info("Waiting for %s to power on...", self.url)
        for _ in range(20):
            time.sleep(30)
            try:
                if self.is_host_powered_on():
                    return
            except requests.exceptions.RequestException:
                pass
        raise ApplicationError(f"Timed out waiting for host {self.url} to power on")

    def get_redfish_data(self) -> requests.Response:
        """Get data about the Redfish manager."""
        rsp = self.get(path="Managers/Bluefield_BMC", timeout=30)
        rsp.raise_for_status()
        return rsp

    def is_host_powered_on(self) -> bool:
        """Check if chassis is powered on."""
        rsp = self.get(path="Systems/Bluefield")
        rsp.raise_for_status()
        return rsp.ok and rsp.json().get("PowerState") == "On"

    def get_nic_info(self, manufacturers: list[str] | None = None) -> list[RedfishNic]:
        """Get NIC Info."""
        raise NotImplementedError()

    def get_network_device_functions(self) -> requests.Response:
        """Get network device functions."""
        rsp = self.get(
            path="Chassis/Card1/NetworkAdapters/NvidiaNetworkAdapter/NetworkDeviceFunctions"
        )
        rsp.raise_for_status()
        return rsp

    def get_network_device_function_details(
        self, network_device_function: str
    ) -> requests.Response:
        """Get network device function details."""
        rsp = self.get(
            path="Chassis/Card1/NetworkAdapters/NvidiaNetworkAdapter/"
            f"NetworkDeviceFunctions/{network_device_function}"
        )
        rsp.raise_for_status()
        return rsp

    def get_chassis(self) -> requests.Response:
        """Get chassis data."""
        rsp = self.get(path="Chassis/Card1")
        rsp.raise_for_status()
        return rsp

    def get_base_mac(self) -> str:
        """Get Base MAC."""
        rsp = self.get(path="UpdateService/FirmwareInventory/DPU_SYS_IMAGE")
        rsp.raise_for_status()
        # Remove constant bits in the middle to get MAC, e.g. 58a2:e103:0072:dda0
        version = rsp.json()["Version"].strip()
        return str(netaddr.EUI(version[:7] + version[12:]))

    def get_serial(self) -> str:
        """Get serial."""
        rsp = self.get(path="Systems/Bluefield")
        rsp.raise_for_status()
        return str(rsp.json()["SerialNumber"]).strip()
