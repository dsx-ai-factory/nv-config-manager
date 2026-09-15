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
"""Convenience rendering backed by the generated Render API."""

from __future__ import annotations

from nv_config_manager_logging import LogCategory, get_logger
from pydantic import BaseModel

from nv_config_manager_clients._base import HeaderProvider, ServiceClient
from nv_config_manager_clients.generated.render import ApiClient, Configuration
from nv_config_manager_clients.generated.render.api.default_api import DefaultApi
from nv_config_manager_clients.generated.render.api.render_api import RenderApi
from nv_config_manager_clients.generated.render.models.render_request import RenderRequest


class FileCommit(BaseModel):
    """A rendered file and the config store commit it produced."""

    filename: str
    commit: str


class RenderClientException(Exception):
    """An unsuccessful render request."""


class RenderClient(ServiceClient):
    """Async render service wrapper."""

    api_client_type = ApiClient
    configuration_type = Configuration
    default_api_type = DefaultApi
    logger = get_logger(__name__, category=LogCategory.RENDER)

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
            attempts=5,
            retry_statuses={409, 500, 502, 503, 504},
        )
        self._api = RenderApi(self.api_client)

    async def execute_render(self, device_id: str, workflow_id: str) -> list[FileCommit]:
        """Render a device and return the files that changed."""
        self.logger.info("Rendering device=%s, workflow=%s", device_id, workflow_id)
        try:
            data = await self._call(
                self._api.render_v1_render_device_uuid_render_post_without_preload_content,
                device_uuid=device_id,
                render_request=RenderRequest(
                    commit_message=f"Render triggered by workflow {workflow_id}"
                ),
            )
            return [FileCommit.model_validate(item) for item in data.get("updated_files", [])]
        except Exception as exc:
            self.logger.exception("Failed to render device %s: %s", device_id, exc)
            raise RenderClientException(f"Failed to render device: {exc}") from exc
