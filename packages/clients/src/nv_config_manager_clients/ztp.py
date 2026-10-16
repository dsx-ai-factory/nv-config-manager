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
"""File-existence checks through the generated ZTP API."""

from __future__ import annotations

import aiohttp

from nv_config_manager_clients._base import HeaderProvider, ServiceClient
from nv_config_manager_clients.generated.ztp import ApiClient, Configuration
from nv_config_manager_clients.generated.ztp.api.default_api import DefaultApi
from nv_config_manager_clients.generated.ztp.api.files_api import FilesApi


class ZTPClientException(Exception):
    """An unsuccessful ZTP request."""


class ZTPClient(ServiceClient):
    """Async ZTP service wrapper."""

    api_client_type = ApiClient
    configuration_type = Configuration
    default_api_type = DefaultApi

    def __init__(
        self,
        base_url: str,
        client_certificate: tuple[str, str] | None = None,
        headers: HeaderProvider = None,
        *,
        verify: bool | str = True,
    ) -> None:
        super().__init__(
            base_url,
            client_certificate=client_certificate,
            headers=headers,
            verify=verify,
            attempts=3,
            retry_statuses={500, 502, 503, 504},
        )
        self._api = FilesApi(self.api_client)

    async def check_file_exists(self, file_path: str) -> bool:
        """Check metadata without downloading firmware content."""
        parts = file_path.split("/")
        if len(parts) != 3 or any(not part or part in {".", ".."} for part in parts):
            raise ZTPClientException("Firmware source path must contain three non-empty segments")
        try:
            platform, version, filename = parts
            await self._call(
                self._api.check_object_v1_files_platform_version_filename_head_without_preload_content,
                platform=platform,
                version=version,
                filename=filename,
            )
            return True
        except aiohttp.ClientResponseError as exc:
            if exc.status == 404:
                return False
            raise ZTPClientException(f"Failed to check firmware file: {exc}") from exc
        except aiohttp.ClientError as exc:
            raise ZTPClientException(f"Failed to check firmware file: {exc}") from exc
