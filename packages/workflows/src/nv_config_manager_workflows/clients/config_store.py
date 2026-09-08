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
"""Configuration-independent Config Store Service API client."""

from __future__ import annotations

import logging
import re
import ssl
import types
from collections.abc import Callable
from enum import Enum
from typing import NotRequired, TypedDict, cast
from urllib.parse import quote

import aiohttp
from aiohttp_retry import ExponentialRetry
from pydantic import BaseModel

from nv_config_manager_workflows.clients._http import (
    HeaderProvider,
    _WhoamiViaRetryClientMixin,
)


class ConfigStoreType(Enum):
    """Config Store file types."""

    BACKUP = "backup"
    INTENDED = "intended"


class _FileTypeUnset:
    """Sentinel distinguishing an omitted file type from an explicit ``None``."""


_FILE_TYPE_UNSET = _FileTypeUnset()


class ConfigStoreClientSettings(TypedDict):
    """Explicit constructor settings for :class:`ConfigStoreClient`."""

    target: str
    file_type: ConfigStoreType
    ui_url: str
    verify: NotRequired[bool | str]
    client_certificate: NotRequired[tuple[str, str] | None]
    headers: NotRequired[HeaderProvider]


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


class ConfigStoreClient(_WhoamiViaRetryClientMixin):
    """Async NVIDIA Config Manager Config Store Service Client.

    This client interfaces with the nv-config-manager-config-store-service API using async/await.
    """

    logger: logging.Logger = logging.getLogger(__name__)

    def __init__(
        self,
        target: str,
        file_type: ConfigStoreType,
        ui_url: str,
        verify: bool | str = True,
        client_certificate: tuple[str, str] | None = None,
        headers: dict[str, str] | Callable[[], dict[str, str]] | None = None,
    ) -> None:
        """Initialize an Async Config Store Client.

        Args:
            target: Base URL of the nv-config-manager-config-store-service
            file_type: Config Store file type
            ui_url: UI base URL for generating user-facing links
            verify: SSL verification - True (default), False (disable), or str (path to CA cert)
            client_certificate: Tuple of (cert_file, key_file) for mTLS
            headers: Static dict or callable returning fresh headers per-request
        """
        base_url = target.rstrip("/")
        super().__init__(
            base_url=base_url,
            connector=self._create_connector(verify, client_certificate),
            timeout=aiohttp.ClientTimeout(total=30, connect=10),
            retry_options=ExponentialRetry(
                attempts=5,
                start_timeout=1.0,
                statuses={429, 500, 502, 503, 504},
            ),
            headers=headers,
        )
        self.target: str = base_url
        self.file_type: str = file_type.value
        self.config_url: str = f"{base_url}/v1/config"
        self._ui_url: str = ui_url.rstrip("/")
        self._verify: bool | str = verify
        self._client_certificate: tuple[str, str] | None = client_certificate

    @classmethod
    def for_mcp(
        cls,
        target: str,
        headers: dict[str, str] | Callable[[], dict[str, str]],
        file_type: ConfigStoreType = ConfigStoreType.INTENDED,
        ui_url: str | None = None,
        verify: bool | str = True,
    ) -> ConfigStoreClient:
        """Create a Config Store client for MCP with explicit caller-scoped headers."""
        return cls(
            target=target,
            file_type=file_type,
            ui_url=ui_url or target,
            verify=verify,
            client_certificate=None,
            headers=headers,
        )

    @staticmethod
    def _sanitize_url(url: str) -> str:
        """Remove duplicate slashes from URL."""
        return re.sub(r"([^:]/)(/)+", r"\1", url)

    @staticmethod
    def _create_connector(
        verify: bool | str = True,
        client_certificate: tuple[str, str] | None = None,
    ) -> aiohttp.TCPConnector:
        ssl_context = ssl.create_default_context()
        if isinstance(verify, str):
            ssl_context.load_verify_locations(verify)
        elif verify is False:
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

        if client_certificate:
            ssl_context.load_cert_chain(client_certificate[0], client_certificate[1])

        return aiohttp.TCPConnector(ssl=ssl_context)

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
        """Load a file from the Config Store."""
        try:
            async with self._new_session() as session:
                async with session.get(
                    f"{self.config_url}/{device_uuid}/{quote(filename, safe='')}",
                    params={"file_type": self.file_type},
                ) as rsp:
                    rsp.raise_for_status()
                    data = cast("dict[str, object]", await rsp.json())

                    return ConfigFile(
                        content=cast("str", data["content"]),
                        commit=str(data["version"]),
                        filename=filename,
                        sha=cast("str", data["content_hash"]),
                        created_at=cast("str | None", data.get("created_at")),
                    )
        except aiohttp.ClientResponseError as e:
            if e.status == 404:
                raise ConfigStoreFileNotFound(
                    f"Did not locate {filename} for device {device_uuid}"
                ) from e
            raise ConfigStoreException(f"Failed to load {filename}: {e.status} {e.message}") from e
        except Exception as e:
            raise ConfigStoreException(f"Failed to load {filename}: {e}") from e

    async def list_device_configs(
        self,
        device_uuid: str,
        file_type: ConfigStoreType | None | _FileTypeUnset = _FILE_TYPE_UNSET,
    ) -> list[dict[str, object]]:
        """List latest configuration files for a device."""
        try:
            async with self._new_session() as session:
                async with session.get(
                    f"{self.config_url}/device/{device_uuid}",
                    params=self._file_type_params(file_type),
                ) as rsp:
                    rsp.raise_for_status()
                    return cast("list[dict[str, object]]", await rsp.json())
        except aiohttp.ClientResponseError as exc:
            raise ConfigStoreException(
                f"Failed to list configs for {device_uuid}: {exc.status} {exc.message}"
            ) from exc
        except Exception as exc:
            raise ConfigStoreException(f"Failed to list configs for {device_uuid}: {exc}") from exc

    async def get_config_file(
        self,
        device_uuid: str,
        filename: str,
        file_type: ConfigStoreType | None | _FileTypeUnset = _FILE_TYPE_UNSET,
        version: int | None = None,
    ) -> dict[str, object]:
        """Get a configuration file from Config Store."""
        params = self._file_type_params(file_type)
        if version is not None:
            params["version"] = version
        try:
            async with self._new_session() as session:
                async with session.get(
                    f"{self.config_url}/{device_uuid}/{quote(filename, safe='')}",
                    params=params,
                ) as rsp:
                    rsp.raise_for_status()
                    return cast("dict[str, object]", await rsp.json())
        except aiohttp.ClientResponseError as exc:
            if exc.status == 404:
                raise ConfigStoreFileNotFound(
                    f"Did not locate {filename} for device {device_uuid}"
                ) from exc
            raise ConfigStoreException(
                f"Failed to get config {device_uuid}/{filename}: {exc.status} {exc.message}"
            ) from exc
        except Exception as exc:
            raise ConfigStoreException(
                f"Failed to get config {device_uuid}/{filename}: {exc}"
            ) from exc

    async def get_config_versions(
        self,
        device_uuid: str,
        filename: str,
        file_type: ConfigStoreType | None | _FileTypeUnset = _FILE_TYPE_UNSET,
        limit: int = 100,
    ) -> dict[str, object]:
        """List versions for a device configuration file."""
        params = self._file_type_params(file_type)
        params["limit"] = limit
        try:
            async with self._new_session() as session:
                async with session.get(
                    f"{self.config_url}/{device_uuid}/{quote(filename, safe='')}/versions",
                    params=params,
                ) as rsp:
                    rsp.raise_for_status()
                    return cast("dict[str, object]", await rsp.json())
        except aiohttp.ClientResponseError as exc:
            raise ConfigStoreException(
                f"Failed to list versions for {device_uuid}/{filename}: {exc.status} {exc.message}"
            ) from exc
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
        file_type: ConfigStoreType | None | _FileTypeUnset = _FILE_TYPE_UNSET,
    ) -> dict[str, object]:
        """Get a diff between two Config Store versions."""
        params = self._file_type_params(file_type)
        params["from_version"] = from_version
        params["to_version"] = to_version
        try:
            async with self._new_session() as session:
                async with session.get(
                    f"{self.config_url}/{device_uuid}/{quote(filename, safe='')}/diff",
                    params=params,
                ) as rsp:
                    rsp.raise_for_status()
                    return cast("dict[str, object]", await rsp.json())
        except aiohttp.ClientResponseError as exc:
            raise ConfigStoreException(
                f"Failed to diff {device_uuid}/{filename}: {exc.status} {exc.message}"
            ) from exc
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

        filtered_items: list[dict[str, str]] = []
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
            async with self._new_session() as session:
                async with session.post(
                    f"{self.config_url}/{device_uuid}/batch",
                    json={"files": filtered_items},
                ) as rsp:
                    rsp.raise_for_status()
                    result = cast("dict[str, object]", await rsp.json())
                    created = result.get("created", [])

                    if not isinstance(created, list) or not created:
                        return None

                    created_items = cast("list[object]", created)
                    return [
                        ConfigFileMetadata(
                            commit=str(cast("dict[str, object]", item)["version"]),
                            filename=filtered_items[i]["filename"],
                        )
                        for i, item in enumerate(created_items)
                    ]
        except aiohttp.ClientResponseError as exc:
            raise ConfigStoreException(
                f"Failed to persist files: {exc.status} {exc.message}"
            ) from exc
        except Exception as exc:
            raise ConfigStoreException(f"Failed to persist files: {exc}") from exc

    async def close(self) -> None:
        """Close the connector."""
        if self.connector and not self.connector.closed:
            await self.connector.close()

    def _file_type_params(
        self,
        file_type: ConfigStoreType | None | _FileTypeUnset,
    ) -> dict[str, object]:
        if file_type is None:
            return {}
        if isinstance(file_type, _FileTypeUnset):
            return {"file_type": self.file_type}
        return {"file_type": file_type.value}

    async def __aenter__(self) -> ConfigStoreClient:
        """Async context manager entry."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        """Async context manager exit."""
        await self.close()
