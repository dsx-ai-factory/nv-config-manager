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
"""Base Redfish connection behavior."""

from __future__ import annotations

import logging
import time
from typing import TypedDict

import requests
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.clients.redfish.models import RedfishHost, RedfishNic

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class RedfishClientSettings(TypedDict):
    """Explicit settings required to construct a Redfish connection."""

    username: str
    password: str
    config_manager_password: str


class RedfishConnection:
    """Base Redfish connection."""

    def __init__(
        self,
        host: RedfishHost,
        username: str,
        password: str,
        config_manager_password: str,
    ) -> None:
        self.host = host
        self.url = f"https://{host.address}:{host.port}/redfish/v1"
        self.username = username
        self.password = password
        self.config_manager_password = config_manager_password

    def get_session(self) -> requests.Session:
        """Return an authenticated Redfish HTTP session."""
        session = requests.Session()
        session.verify = False
        session.auth = (self.username, self.password)
        return session

    def patch(self, path: str, payload: dict[str, str], timeout: int = 10) -> requests.Response:
        """Issue a PATCH request."""
        response = self.get_session().patch(f"{self.url}/{path}", json=payload, timeout=timeout)
        response.raise_for_status()
        return response

    def post(self, path: str, payload: dict[str, str], timeout: int = 10) -> requests.Response:
        """Issue a POST request."""
        response = self.get_session().post(f"{self.url}/{path}", json=payload, timeout=timeout)
        response.raise_for_status()
        return response

    def get(self, path: str | None = None, timeout: int = 10) -> requests.Response:
        """Issue a GET request."""
        response = self.get_session().get(
            f"{self.url}/{path}" if path else f"{self.url}/",
            timeout=timeout,
        )
        response.raise_for_status()
        return response

    def wait_for_restart(self) -> None:
        """Wait for a restart to finish."""
        for _ in range(20):
            time.sleep(30)
            try:
                response = self.get(path=None)
            except requests.exceptions.RequestException:
                continue
            if response.ok:
                return
        raise ApplicationError(f"Timed out waiting for host {self.url} to restart")

    def wait_for_power_on(self) -> None:
        """Wait for the host to power on."""
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
        """Set the service-managed password."""
        raise NotImplementedError

    def is_host_powered_on(self) -> bool:
        """Return whether the chassis is powered on."""
        raise NotImplementedError

    def power_on_chassis(self) -> requests.Response:
        """Power on the chassis."""
        raise NotImplementedError

    def factory_reset(self) -> requests.Response | None:
        """Factory-reset the controller."""
        raise NotImplementedError

    def get_redfish_data(self) -> requests.Response:
        """Return controller data."""
        raise NotImplementedError

    def get_nic_info(self, manufacturers: list[str] | None = None) -> list[RedfishNic]:
        """Return NIC information."""
        raise NotImplementedError

    def get_chassis(self) -> requests.Response:
        """Return chassis data."""
        raise NotImplementedError

    def get_serial(self) -> str:
        """Return the serial number."""
        raise NotImplementedError
