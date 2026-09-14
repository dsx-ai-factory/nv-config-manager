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
"""Compatibility exports for the relocated UFM client."""

from nv_config_manager.common.config_loader import load_config
from nv_config_manager.temporal.factories.ufm import ufm_client_settings
from nv_config_manager_workflows.clients.ufm import (
    UFMAuthError,
    UFMClientError,
)
from nv_config_manager_workflows.clients.ufm import UFMClient as BaseUFMClient


class UFMClient(BaseUFMClient):
    """Compatibility client preserving service-side configuration lookup."""

    def __init__(
        self,
        host: str,
        site: str | None = None,
        timeout_seconds: int = 30,
        max_passwords: int = 2,
    ) -> None:
        """Resolve legacy constructor arguments through the service adapter."""
        settings = ufm_client_settings(
            load_config(),
            site=site,
            max_passwords=max_passwords,
        )
        super().__init__(
            base_url=f"https://{host}/ufmRest",
            username=settings["username"],
            passwords=settings["passwords"],
            ssl=False,
            timeout_seconds=timeout_seconds,
        )
        self._site = site
        self._max_passwords = max_passwords


__all__ = [
    "UFMAuthError",
    "UFMClient",
    "UFMClientError",
]
