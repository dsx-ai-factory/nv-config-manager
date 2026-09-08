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
"""NVIDIA Config Manager Render functionality."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

from nv_config_manager_templates.render import Renderer
from nv_config_manager_templates.version import template_version_key

from nv_config_manager.common.client.config_store import ConfigStoreFileNotFound
from nv_config_manager.common.client.render import FileCommit
from nv_config_manager.common.config import (
    LogCategory,
    config_store_client,
    config_store_ui_url,
    get_logger,
)
from nv_config_manager.dcim import (
    DCIMClient,
    IntendedConfigurationUpdate,
    RenderDataRequest,
    dcim_client_session,
)
from nv_config_manager.render.exceptions import RenderException

logger = get_logger(__name__, category=LogCategory.RENDER)

DEPLOYABLE_FILES = ["startup.yaml", "full-config"]


async def _update_intended_configuration(  # pylint: disable=too-many-arguments
    dcim_client: DCIMClient,
    device_uuid: str,
    config_store_instance: str,
    commit_id: str,
    paths: list[str],
    user: str,
    commit_message: str,
    updated_at: str | None = None,
) -> None:
    """Persist the deployable render metadata through the selected provider."""
    for filename in paths:
        if filename in DEPLOYABLE_FILES:
            await dcim_client.upsert_intended_configuration(
                IntendedConfigurationUpdate(
                    device_id=device_uuid,
                    config_store_instance=config_store_instance,
                    path=filename,
                    commit_id=commit_id,
                    updated=updated_at or datetime.now(UTC).isoformat(),
                    updated_by=user,
                    commit_message=commit_message,
                    template_version=template_version_key(),
                )
            )
            return

    await _update_template_version(dcim_client, device_uuid)


async def _update_template_version(dcim_client: DCIMClient, device_uuid: str) -> None:
    """Record a template version change without a deployable file update."""
    await dcim_client.update_render_template_version(device_uuid, template_version_key())


async def _persist_rendered_files(
    dcim_client: DCIMClient,
    device_uuid: str,
    files: dict[str, str],
    commit_message: str,
    user: str,
    user_domain: str,
) -> list[FileCommit]:
    """Persist rendered files and synchronize the resulting provider state."""
    csclient = config_store_client()
    async with csclient:
        updated_files = await csclient.persist_files(
            device_uuid, files, commit_message, user, user_domain
        )

        if updated_files:
            file_commits = [
                FileCommit(filename=file.filename, commit=file.commit) for file in updated_files
            ]
            deployable_file = next(
                (file for file in file_commits if file.filename in DEPLOYABLE_FILES), None
            )
            logger.info(
                "Produced commits for %s: %s",
                device_uuid,
                {file.filename: file.commit for file in file_commits},
            )
            if deployable_file:
                await _update_intended_configuration(
                    dcim_client,
                    device_uuid,
                    config_store_ui_url(),
                    deployable_file.commit,
                    [file.filename for file in file_commits],
                    user,
                    commit_message,
                )
            else:
                await _update_template_version(dcim_client, device_uuid)
            return file_commits

        for candidate in files:
            if candidate not in DEPLOYABLE_FILES:
                continue
            try:
                latest = await csclient.load_file(device_uuid, candidate)
                logger.info(
                    "No config diff for %s, re-syncing DCIM with latest version %s of %s",
                    device_uuid,
                    latest.commit,
                    candidate,
                )
                await _update_intended_configuration(
                    dcim_client,
                    device_uuid,
                    config_store_ui_url(),
                    latest.commit,
                    [candidate],
                    user,
                    commit_message,
                    updated_at=latest.created_at,
                )
                return []
            except ConfigStoreFileNotFound:
                continue

        await _update_template_version(dcim_client, device_uuid)
        return []


async def execute_render(device_uuid: str, commit_message: str, user: str) -> list[FileCommit]:
    """Execute a render for a device."""
    logger.info(
        "Rendering configuration for %s with commit message '%s'",
        device_uuid,
        commit_message,
    )
    user_domain = os.getenv("NV_CONFIG_MANAGER_DOMAIN", "local.config-manager.example.com")

    async with dcim_client_session() as dcim_client:
        try:
            renderer = Renderer()
            render_data = await dcim_client.get_render_data(
                RenderDataRequest(
                    device_id=device_uuid,
                    plugin_data_requirements=renderer.plugin_data_requirements,
                )
            )
            files = await asyncio.to_thread(
                renderer.render_entrypoints,
                render_data=render_data,
            )
            if not files:
                logger.info("No entrypoint templates matched for %s", device_uuid)
                return []
            files = {key.split("/")[-1].replace(".j2", ""): value for key, value in files.items()}
        except Exception as exc:
            raise RenderException(f"Failed to render entrypoints for {device_uuid}: {exc}") from exc

        try:
            return await _persist_rendered_files(
                dcim_client,
                device_uuid,
                files,
                commit_message,
                user,
                user_domain,
            )
        except Exception:
            logger.exception("Failed to persist or synchronize render for %s", device_uuid)
            raise
