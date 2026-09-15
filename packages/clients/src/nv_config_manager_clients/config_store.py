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
"""NVIDIA Config Manager Config Store Service API Client (Async)."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Self, cast

import aiohttp
from nv_config_manager_logging import LogCategory, get_logger
from pydantic import BaseModel

from nv_config_manager_clients._base import ServiceClient
from nv_config_manager_clients._types import ConfigStoreType
from nv_config_manager_clients.generated.config_store import ApiClient, Configuration
from nv_config_manager_clients.generated.config_store.api.config_api import ConfigApi
from nv_config_manager_clients.generated.config_store.api.default_api import DefaultApi
from nv_config_manager_clients.generated.config_store.models.batch_config_request import (
    BatchConfigRequest,
)

_FILE_TYPE_UNSET = object()


class ConfigStoreException(Exception):
    """Exception interacting with Config Store."""


class ConfigStoreFileNotFound(ConfigStoreException):
    """Desired file not found in Config Store."""


class ConfigFileMetadata(BaseModel):
    filename: str
    commit: str


class ConfigFile(ConfigFileMetadata):
    content: str
    sha: str | None = None
    created_at: str | None = None


class ConfigStoreClient(ServiceClient):
    """Async Config Store conveniences backed by generated service operations."""

    api_client_type = ApiClient
    configuration_type = Configuration
    default_api_type = DefaultApi

    logger = get_logger(__name__, category=LogCategory.CONFIG_STORE)

    def __init__(
        self,
        target: str,
        file_type: ConfigStoreType | str,
        ui_url: str,
        verify: bool | str = True,
        client_certificate: tuple[str, str] | None = None,
        headers: dict[str, str] | Callable[[], dict[str, str]] | None = None,
    ) -> None:
        """Configure the generated client and preserve Config Store conveniences."""
        self.file_type = ConfigStoreType(file_type).value
        super().__init__(
            target,
            verify=verify,
            client_certificate=client_certificate,
            headers=headers,
            attempts=5,
            retry_statuses={429, 500, 502, 503, 504},
        )
        self.target = self.base_url
        self._ui_url = ui_url.rstrip("/")
        self._api = ConfigApi(self.api_client)

    @classmethod
    def for_mcp(
        cls,
        target: str,
        headers: dict[str, str] | Callable[[], dict[str, str]],
        file_type: ConfigStoreType | str = "intended",
        ui_url: str | None = None,
        verify: bool | str = True,
    ) -> Self:
        """Create a Config Store client for MCP with explicit caller-scoped headers."""
        if hasattr(file_type, "value"):
            file_type_str = str(file_type.value)
        else:
            file_type_str = str(file_type)
        return cls(
            target=target,
            file_type=file_type_str,
            ui_url=ui_url or target,
            verify=verify,
            client_certificate=None,
            headers=headers,
        )

    @staticmethod
    def _sanitize_url(url: str) -> str:
        """Remove duplicate slashes from URL."""
        return re.sub(r"([^:]/)(/)+", r"\1", url)

    @property
    def ui_target(self) -> str:
        """Return the UI target URL."""
        return self._ui_url

    def file_url(self, device_uuid: str, filename: str, version: str | None = None) -> str:
        """Generate URL to view full file content in the UI."""
        url = f"{self.ui_target}/device/{device_uuid}/{filename}?file_type={self.file_type}"
        if version:
            url += f"&version={version}"
        return self._sanitize_url(url)

    def history_url(self, device_uuid: str, filename: str) -> str:
        """Generate URL to view version history."""
        return self._sanitize_url(
            f"{self.ui_target}/device/{device_uuid}/{filename}/history?file_type={self.file_type}"
        )

    async def load_file(self, device_uuid: str, filename: str) -> ConfigFile:
        """Load file content and adapt API metadata to the legacy model."""
        data = await self.get_config_file(device_uuid, filename)
        return ConfigFile(
            content=cast(str, data["content"]),
            commit=str(data["version"]),
            filename=filename,
            sha=cast(str, data["content_hash"]),
            created_at=cast(str | None, data.get("created_at")),
        )

    async def list_device_configs(
        self,
        device_uuid: str,
        file_type: ConfigStoreType | str | None | object = _FILE_TYPE_UNSET,
    ) -> list[dict[str, object]]:
        """List the latest files through the generated operation."""
        try:
            return cast(
                "list[dict[str, object]]",
                await self._call(
                    self._api.get_device_configs_v1_config_device_device_uuid_get_without_preload_content,
                    device_uuid=device_uuid,
                    **self._file_type_params(file_type),
                ),
            )
        except Exception as exc:
            raise ConfigStoreException(f"Failed to list configs for {device_uuid}: {exc}") from exc

    async def get_config_file(
        self,
        device_uuid: str,
        filename: str,
        file_type: ConfigStoreType | str | None | object = _FILE_TYPE_UNSET,
        version: int | None = None,
    ) -> dict[str, object]:
        """Get a configuration while preserving wrapper exception types."""
        try:
            return cast(
                "dict[str, object]",
                await self._call(
                    self._api.get_config_v1_config_device_uuid_filename_get_without_preload_content,
                    device_uuid=device_uuid,
                    filename=filename,
                    version=version,
                    **self._file_type_params(file_type),
                ),
            )
        except aiohttp.ClientResponseError as exc:
            if exc.status == 404:
                raise ConfigStoreFileNotFound(
                    f"Did not locate {filename} for device {device_uuid}"
                ) from exc
            raise ConfigStoreException(
                f"Failed to get config {device_uuid}/{filename}: {exc}"
            ) from exc
        except Exception as exc:
            raise ConfigStoreException(
                f"Failed to get config {device_uuid}/{filename}: {exc}"
            ) from exc

    async def get_config_versions(
        self,
        device_uuid: str,
        filename: str,
        file_type: ConfigStoreType | str | None | object = _FILE_TYPE_UNSET,
        limit: int = 100,
    ) -> dict[str, object]:
        """List versions using the generated endpoint."""
        try:
            return cast(
                "dict[str, object]",
                await self._call(
                    self._api.list_versions_v1_config_device_uuid_filename_versions_get_without_preload_content,
                    device_uuid=device_uuid,
                    filename=filename,
                    limit=limit,
                    **self._file_type_params(file_type),
                ),
            )
        except Exception as exc:
            raise ConfigStoreException(
                f"Failed to list versions for {device_uuid}/{filename}: {exc}"
            ) from exc

    async def get_config_diff(
        self,
        device_uuid: str,
        filename: str,
        from_version: int,
        to_version: int,
        file_type: ConfigStoreType | str | None | object = _FILE_TYPE_UNSET,
    ) -> dict[str, object]:
        """Compare stored versions through the generated endpoint."""
        try:
            return cast(
                "dict[str, object]",
                await self._call(
                    self._api.get_diff_v1_config_device_uuid_filename_diff_get_without_preload_content,
                    device_uuid=device_uuid,
                    filename=filename,
                    from_version=from_version,
                    to_version=to_version,
                    **self._file_type_params(file_type),
                ),
            )
        except Exception as exc:
            raise ConfigStoreException(f"Failed to diff {device_uuid}/{filename}: {exc}") from exc

    async def persist_files(
        self,
        device_uuid: str,
        files: dict[str, str],
        commit_message: str,
        user: str,
        user_domain: str,
    ) -> list[ConfigFileMetadata] | None:
        """Persist files to the Config Store using batch API.

        Args:
            device_uuid: DCIM provider device identifier
            files: Dictionary mapping filenames to content
            commit_message: Commit message for the changes
            user: Username
            user_domain: User's domain (for email construction)

        Returns:
            List of ConfigFileMetadata for created/updated files, or None if nothing changed
        """
        author_email = f"{user}@{user_domain}"

        filtered_items = []
        for filename, content in files.items():
            clean_filename = filename.replace(".j2", "")
            try:
                existing_file = await self.load_file(device_uuid, clean_filename)
                if content == existing_file.content:
                    self.logger.info("No diff for %s/%s", device_uuid, clean_filename)
                    continue
            except ConfigStoreFileNotFound:
                pass

            filtered_items.append(
                {
                    "filename": clean_filename,
                    "content": content,
                    "author": author_email,
                    "commit_message": commit_message,
                    "file_type": self.file_type,
                }
            )

        if not filtered_items:
            return None

        try:
            result = await self._call(
                self._api.batch_create_configs_v1_config_device_uuid_batch_post_without_preload_content,
                device_uuid=device_uuid,
                batch_config_request=BatchConfigRequest.model_validate({"files": filtered_items}),
            )
            created = result.get("created", [])
            if not created:
                return None
            return [
                ConfigFileMetadata(
                    commit=str(item["version"]), filename=filtered_items[i]["filename"]
                )
                for i, item in enumerate(created)
            ]
        except Exception as exc:
            raise ConfigStoreException(f"Failed to persist files: {exc}") from exc

    def _file_type_params(
        self, file_type: ConfigStoreType | str | None | object
    ) -> dict[str, str | None]:
        if file_type is None:
            return {"file_type": None}
        if file_type is _FILE_TYPE_UNSET:
            return {"file_type": self.file_type}
        return {"file_type": ConfigStoreType(file_type).value}
