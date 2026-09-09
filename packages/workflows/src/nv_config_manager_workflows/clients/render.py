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
"""Configuration-independent Render Service client."""

from __future__ import annotations

import logging
import ssl
from collections.abc import Callable
from typing import TypedDict, cast

from aiohttp import ClientTimeout, TCPConnector
from aiohttp_retry import ExponentialRetry
from pydantic import BaseModel

from nv_config_manager_workflows.clients._http import (
    _WhoamiViaRetryClientMixin,
)

logger = logging.getLogger(__name__)


class FileCommit(BaseModel):
    """A rendered file and the config store commit it produced."""

    filename: str
    commit: str


class _RenderResponse(TypedDict):
    updated_files: list[dict[str, object]]


class RenderClientException(Exception):
    """Exception raised for errors in the Render client."""


type HeaderProvider = dict[str, str] | Callable[[], dict[str, str]] | None


class RenderClientSettings(TypedDict):
    """Constructor settings for the render client."""

    base_url: str
    client_certificate: tuple[str, str] | None
    headers: HeaderProvider


class RenderClient(_WhoamiViaRetryClientMixin):
    """Async client for interacting with the render service."""

    def __init__(
        self,
        base_url: str,
        client_certificate: tuple[str, str] | None = None,
        headers: HeaderProvider = None,
    ) -> None:
        """Initialize the render client.

        Args:
            base_url: Base URL of the render service
            client_certificate: Tuple of (cert_file, key_file) for mTLS, or None for internal endpoints
            headers: Static dict or callable returning fresh headers per-request
        """
        if client_certificate:
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.load_cert_chain(client_certificate[0], client_certificate[1])
            ssl_ctx.minimum_version = ssl.TLSVersion.TLSv1_3
            connector = TCPConnector(ssl=ssl_ctx)
        else:
            connector = TCPConnector()

        super().__init__(
            base_url=base_url.rstrip("/"),
            connector=connector,
            timeout=ClientTimeout(total=30),
            retry_options=ExponentialRetry(
                attempts=5,
                start_timeout=1.0,
                max_timeout=10.0,
                factor=2.0,
                statuses={409, 500, 502, 503, 504},
            ),
            headers=headers,
        )

    async def execute_render(self, device_id: str, workflow_id: str) -> list[FileCommit]:
        """Execute a fresh render for a device.

        Args:
            device_id: The device ID to render
            workflow_id: The workflow ID triggering this render

        Returns:
            List of FileCommit objects for files that changed

        Raises:
            RenderClientException: If the render request fails
        """
        logger.info("Rendering device=%s, workflow=%s", device_id, workflow_id)
        url = f"{self.base_url}/v1/render/{device_id}/render"
        payload = {"commit_message": f"Render triggered by workflow {workflow_id}"}

        try:
            async with self._new_session() as session:
                async with session.post(url, json=payload) as response:
                    response.raise_for_status()
                    data = cast("_RenderResponse", await response.json())
                    raw_commits = data.get("updated_files", [])
                    return [FileCommit.model_validate(fc) for fc in raw_commits]
        except Exception as exc:
            logger.exception("Failed to render device %s: %s", device_id, str(exc))
            raise RenderClientException(f"Failed to render device: {exc}") from exc
