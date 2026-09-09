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
"""Redfish client."""

import json
import logging
import os
import time
from enum import StrEnum
from typing import Any

import netaddr
import requests
from pydantic import BaseModel, field_validator
from temporalio.exceptions import ApplicationError

from nv_config_manager.common.config_loader import load_config
from nv_config_manager.common.log import LogCategory, get_logger

logger = get_logger(__name__, category=LogCategory.TEMPORAL_ACTIVITY)
logger.setLevel(logging.INFO)


class RedfishVendor(StrEnum):
    """Vendors for redfish."""

    LENOVO = "Lenovo"
    BLUEFIELD = "Nvidia"
    DELL = "Dell"


class RedfishHost(BaseModel, validate_assignment=True):
    """Hosts for Redfish."""

    address: str
    port: int = 443
    vendor: RedfishVendor
    mac: str | None = None

    def __str__(self) -> str:
        """String formatting."""
        return f"{self.vendor.value}/{self.mac}/{self.address}:{self.port}"

    @field_validator("mac", mode="before")
    @classmethod
    def format_mac(cls, mac: str | None) -> str | None:
        """Ensure consistent MAC address format."""
        return str(netaddr.EUI(mac)) if mac else None


class RedfishDpuPort(BaseModel):
    """Redfish DPU Port."""

    name: str
    mac: str | None

    @field_validator("mac", mode="before")
    @classmethod
    def format_mac(cls, mac: str | None) -> str | None:
        """Ensure consistent MAC address format."""
        return str(netaddr.EUI(mac)) if mac else None


class RedfishDpu(RedfishHost, validate_assignment=True):
    """Redfish DPU."""

    ports: list[RedfishDpuPort]
    base_mac: str
    serial: str

    @field_validator("base_mac", mode="before")
    @classmethod
    def format_mac(cls, mac: str | None) -> str | None:
        """Ensure consistent MAC address format."""
        return str(netaddr.EUI(mac)) if mac else None


class RedfishNic(BaseModel, validate_assignment=True):
    """Redfish NIC."""

    name: str
    slot: str
    mac: str | None = None
    dpu: RedfishDpu | None = None

    @field_validator("mac", mode="before")
    @classmethod
    def format_mac(cls, mac: str | None) -> str | None:
        """Ensure consistent MAC address format."""
        return str(netaddr.EUI(mac)) if mac else None


class RedfishServer(RedfishHost):
    """Redfish Server."""

    serial: str
    nics: list[RedfishNic]


class RedfishConnection:
    """Redfish connection class."""

    def __init__(
        self,
        host: RedfishHost,
        username: str,
        password: str,
    ) -> None:
        """Init method."""
        self.host = host
        self.url = f"https://{host.address}:{host.port}/redfish/v1"
        self.username = username
        self.password = password

    def get_session(self) -> requests.Session:
        """Get Redfish session."""
        sess = requests.Session()
        sess.verify = False
        sess.auth = (self.username, self.password)
        return sess

    def patch(self, path: str, payload: dict[str, str], timeout: int = 10) -> requests.Response:
        """Issue a patch request to Redfish session."""
        rsp = self.get_session().patch(f"{self.url}/{path}", json=payload, timeout=timeout)
        rsp.raise_for_status()
        return rsp

    def post(self, path: str, payload: dict[str, str], timeout: int = 10) -> requests.Response:
        """Issue a post request to Redfish session."""
        rsp = self.get_session().post(f"{self.url}/{path}", json=payload, timeout=timeout)
        rsp.raise_for_status()
        return rsp

    def get(self, path: str | None = None, timeout: int = 10) -> requests.Response:
        """Issue a get request to Redfish session."""
        rsp = self.get_session().get(
            f"{self.url}/{path}" if path else f"{self.url}/", timeout=timeout
        )
        rsp.raise_for_status()
        return rsp

    def wait_for_restart(self) -> None:
        """Wait for a restart to finish."""
        for _ in range(20):
            time.sleep(30)
            try:
                rsp = self.get(path=None)
            except requests.exceptions.RequestException:
                continue
            if rsp.ok:
                return
        raise ApplicationError(f"Timed out waiting for host {self.url} to restart")

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

    def set_config_manager_password(self) -> requests.Response | None:
        """Set new password."""
        raise NotImplementedError()

    def is_host_powered_on(self) -> bool:
        """Check if chassis is powered on."""
        raise NotImplementedError()

    def power_on_chassis(self) -> requests.Response:
        """Power on chassis."""
        raise NotImplementedError()

    def factory_reset(self) -> requests.Response | None:
        """Factory Reset."""
        raise NotImplementedError()

    def get_redfish_data(self) -> requests.Response:
        """Get data about the Redfish manager."""
        raise NotImplementedError()

    def get_nic_info(self, manufacturers: list[str] | None = None) -> list[RedfishNic]:
        """Get NIC Info."""
        raise NotImplementedError()

    def get_chassis(self) -> requests.Response:
        """Get chassis data."""
        raise NotImplementedError()

    def get_serial(self) -> str:
        """Get serial number."""
        raise NotImplementedError()


class LenovoRedfishConnection(RedfishConnection):
    """Lenovo Redfish connection class."""

    def __init__(self, host: RedfishHost, username: str, password: str) -> None:
        """Init method."""
        super().__init__(
            host=host,
            username=username,
            password=password,
        )
        self.initial_password = password

    def set_config_manager_password(self) -> requests.Response:
        """Set new password."""
        password = load_config()["redfish"]["lenovo_config_manager_password"]
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


class DellRedfishConnection(RedfishConnection):
    """Dell Redfish connection class."""

    def __init__(self, host: RedfishHost, username: str, password: str) -> None:
        """Init method."""
        super().__init__(
            host=host,
            username=username,
            password=password,
        )

    def set_config_manager_password(self) -> None:
        """Set new password."""
        raise ApplicationError(f"BMC password should not be changed for Dell: {self.url}")

    def factory_reset(self) -> None:
        """Factory Reset."""
        raise ApplicationError(f"BMC factory reset should not be performed Dell: {self.url}")

    def power_on_chassis(self) -> requests.Response:
        """Power on chassis."""
        rsp = self.post(
            path="Systems/System.Embedded.1/Actions/ComputerSystem.Reset",
            payload={"ResetType": "On"},
        )
        rsp.raise_for_status()
        self.wait_for_power_on()
        return rsp

    def power_off_chassis(self) -> requests.Response:
        """Power off chassis."""
        rsp = self.post(
            path="Systems/System.Embedded.1/Actions/ComputerSystem.Reset",
            payload={"ResetType": "GracefulShutdown"},
        )
        rsp.raise_for_status()
        return rsp

    def get_redfish_data(self) -> requests.Response:
        """Get data about the Redfish manager."""
        rsp = self.get(path="Managers/iDRAC.Embedded.1")
        rsp.raise_for_status()
        return rsp

    def is_host_powered_on(self) -> bool:
        """Check if chassis is powered on."""
        logger.debug("Checking power status for %s...", self.url)
        rsp = self.get(path="Systems/System.Embedded.1")
        logger.debug("Host %s power state: %s", self.url, rsp.json().get("PowerState"))
        rsp.raise_for_status()
        return rsp.ok and rsp.json().get("PowerState") == "On"

    def get_network_adapters(self) -> requests.Response:
        """Get network adapters."""
        rsp = self.get(path="Chassis/System.Embedded.1/NetworkAdapters")
        rsp.raise_for_status()

        return rsp

    def get_network_adapter_details(self, network_adapter: str) -> requests.Response:
        """Get network adapter details."""
        rsp = self.get(path=f"Chassis/System.Embedded.1/NetworkAdapters/{network_adapter}")
        rsp.raise_for_status()
        return rsp

    def get_network_device_function_detail(
        self, network_adapter: str, network_device_functon: str
    ) -> requests.Response:
        """Get network device function details."""
        rsp = self.get(
            path=(
                f"Chassis/System.Embedded.1/NetworkAdapters/{network_adapter}"
                f"/NetworkDeviceFunctions/{network_device_functon}"
            )
        )
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
                    network_functions_seen = []
                    for network_function in controller_data["Links"].get(
                        "NetworkDeviceFunctions", []
                    ):
                        network_function_name = network_function["@odata.id"].split("/")[-1]
                        if network_function_name in network_functions_seen:
                            # Some BMCs report duplicate functions
                            continue
                        network_functions_seen.append(network_function_name)
                        network_function_data = self.get_network_device_function_detail(
                            adapter_name,
                            network_function_name,
                        ).json()
                        results.append(
                            RedfishNic(
                                name=network_function_name,
                                slot=adapter_name,
                                mac=(
                                    network_function_data["Ethernet"].get("MACAddress")
                                    if network_function_data.get("Ethernet")
                                    else None
                                ),
                            )
                        )
        return results

    def get_chassis(self) -> requests.Response:
        """Get chassis data."""
        rsp = self.get(path="Chassis/System.Embedded.1")
        rsp.raise_for_status()
        return rsp

    def get_serial(self) -> str:
        """Get serial."""
        rsp = self.get(path="Systems/System.Embedded.1")
        rsp.raise_for_status()
        return str(rsp.json()["SerialNumber"])


class Bluefield3RedfishConnection(RedfishConnection):
    """Bluefield2 Redfish Connection."""

    def __init__(self, host: RedfishHost, username: str, password: str) -> None:
        """Init method."""
        super().__init__(
            host=host,
            username=username,
            password=password,
        )
        self.initial_password = password

    def set_config_manager_password(self) -> requests.Response:
        """Set Redfish password."""
        password = load_config()["redfish"]["bluefield_config_manager_password"]
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


def get_bmc_creds() -> Any:
    """Get the BMC Cred file."""
    path = os.environ.get("BMC_CREDS_PATH", "/etc/vault/bmc-creds.json")
    with open(path, encoding="utf-8") as creds_file:
        return json.load(creds_file)


def get_default_connection(redfish_host: RedfishHost) -> RedfishConnection:
    """Get the default connection object for a Redfish connection."""
    config = load_config()
    creds = get_bmc_creds().get(redfish_host.mac)

    if redfish_host.vendor == RedfishVendor.LENOVO:
        if creds:
            username = creds["default_user"]
            password = creds["default_password"]
        else:
            logger.info(
                "No default creds found for redfish host %s, trying fallback values",
                redfish_host,
            )
            username = config["redfish"]["lenovo_default_user"]
            password = config["redfish"]["lenovo_default_password"]
        return LenovoRedfishConnection(
            host=redfish_host,
            username=username,
            password=password,
        )
    if redfish_host.vendor == RedfishVendor.BLUEFIELD:
        if creds:
            username = creds["default_user"]
            password = creds["default_password"]
        else:
            logger.info(
                "No default creds found for redfish host %s, trying fallback values",
                redfish_host,
            )
            username = config["redfish"]["bluefield_default_user"]
            password = config["redfish"]["bluefield_default_password"]
        return Bluefield3RedfishConnection(
            host=redfish_host,
            username=username,
            password=password,
        )
    if redfish_host.vendor == RedfishVendor.DELL:
        if creds:
            username = creds["default_user"]
            password = creds["default_password"]
        else:
            raise ApplicationError(f"No password found for host {redfish_host}")
        return DellRedfishConnection(
            host=redfish_host,
            username=username,
            password=password,
        )
    raise NotImplementedError(f"No Redfish connection implemented for vendor {redfish_host}")


def get_config_manager_connection(redfish_host: RedfishHost) -> RedfishConnection:
    """Get the NVIDIA Config Manager connection object for a Redfish connection."""
    config = load_config()
    creds = get_bmc_creds().get(redfish_host.mac)

    if redfish_host.vendor == RedfishVendor.LENOVO:
        if creds:
            username = creds["default_user"]
            password = creds["config_manager_password"]
        else:
            logger.info(
                "No NVIDIA Config Manager creds found for redfish host %s, trying fallback values",
                redfish_host,
            )
            username = config["redfish"]["lenovo_default_user"]
            password = config["redfish"]["lenovo_config_manager_password"]
        return LenovoRedfishConnection(
            host=redfish_host,
            username=username,
            password=password,
        )
    if redfish_host.vendor == RedfishVendor.BLUEFIELD:
        if creds:
            username = creds["default_user"]
            password = creds["config_manager_password"]
        else:
            logger.info(
                "No NVIDIA Config Manager creds found for redfish host %s, trying fallback values",
                redfish_host,
            )
            username = config["redfish"]["bluefield_default_user"]
            password = config["redfish"]["bluefield_config_manager_password"]
        return Bluefield3RedfishConnection(
            host=redfish_host,
            username=username,
            password=password,
        )
    if redfish_host.vendor == RedfishVendor.DELL:
        # Use default creds for Dell since they don't require a password change
        if creds:
            username = creds["default_user"]
            password = creds["default_password"]
        else:
            raise ApplicationError(f"No password found for host {redfish_host}")
        return DellRedfishConnection(
            host=redfish_host,
            username=username,
            password=password,
        )
    raise NotImplementedError(f"No Redfish connection implemented for vendor {redfish_host}")
