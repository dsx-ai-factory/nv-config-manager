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
"""Shared Redfish HTTP connection behavior."""

import logging
import time

import requests
from nv_config_manager_logging import LogCategory, get_logger
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.clients.redfish.models import RedfishHost, RedfishNic

logger = get_logger(__name__, category=LogCategory.TEMPORAL_ACTIVITY)
logger.setLevel(logging.INFO)


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
