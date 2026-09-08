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
"""Configuration Backup Activities."""

import os
from contextlib import closing

from pydantic import BaseModel
from temporalio import activity

from nv_config_manager.common.config import (
    ConfigStoreType,
    config_store_client,
    config_store_ui_url,
)
from nv_config_manager.dcim import ConfigurationBackupIntent, create_dcim_client
from nv_config_manager.temporal.client.device import NetworkConnection
from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData


@activity.defn
def load_running_configuration(device_data: NetworkDeviceData) -> str:
    """Load the running configuration for the given device."""
    with closing(NetworkConnection.from_device_data(device_data)) as connection:
        return connection.get_running_configuration()


class PersistConfigBackupInput(BaseModel):
    """Input class for persist_config_backup activity."""

    device_data: NetworkDeviceData
    device_running_config: str
    commit_message: str
    user: str
    user_domain: str | None


@activity.defn
async def persist_config_backup(activity_input: PersistConfigBackupInput) -> str:
    """Persist the config backup to the Config Store."""
    client = config_store_client(ConfigStoreType.BACKUP)
    user_domain = (
        activity_input.user_domain
        if activity_input.user_domain is not None
        else os.getenv("NV_CONFIG_MANAGER_DOMAIN", "local.config-manager.example.com")
    )

    async with client:
        metadata = await client.persist_files(
            device_uuid=activity_input.device_data.id,
            files={activity_input.device_data.backup_file: activity_input.device_running_config},
            commit_message=activity_input.commit_message,
            user=activity_input.user,
            user_domain=user_domain,
        )
        if metadata:
            return metadata[0].commit
        # if nothing persisted, pull existing commit
        file = await client.load_file(
            device_uuid=activity_input.device_data.id,
            filename=activity_input.device_data.backup_file,
        )
        return file.commit


class RecordBackupConfigManagerPluginInput(BaseModel):
    """Input class for record_backup_config_manager_plugin activity."""

    workflow_id: str
    device_id: str
    commit_id: str
    path: str
    user: str
    commit_message: str
    deployed_commit_id: str | None


@activity.defn
async def record_backup_config_manager_plugin(  # pylint: disable=too-many-arguments
    activity_input: RecordBackupConfigManagerPluginInput,
) -> tuple[bool, str]:
    """Record configuration-backup metadata in the configured DCIM."""
    csclient = config_store_client(ConfigStoreType.BACKUP)
    fname = activity_input.path.split("/")[-1]

    markdown = f"[Configuration Backup]({csclient.file_url(device_uuid=activity_input.device_id, filename=fname)})"

    client = create_dcim_client()
    async with client:
        existing_backup = await client.get_configuration_backup_metadata(activity_input.device_id)
        deployed_commit_id = activity_input.deployed_commit_id or None
        config_store_changed = (
            existing_backup is None or existing_backup.commit_id != activity_input.commit_id
        )
        deployed_commit_changed = (
            existing_backup is None
            or (existing_backup.deployed_commit_id or None) != deployed_commit_id
        )
        if not config_store_changed and not deployed_commit_changed:
            assert existing_backup is not None
            # Check if it was updated by this workflow,
            # if so this may be a retry that occurred despite the update succeeding
            if existing_backup.workflow_id == activity_input.workflow_id:
                return True, f"Persisted new backup configuration:\n{markdown}"
            return False, f"No diff to previous backup execution:\n{markdown}"

        # Updating only the deployed commit metadata does not represent a new Config Store
        # backup. Preserve the workflow that wrote the existing backup so an activity retry
        # cannot incorrectly report this metadata-only update as a new backup.
        if config_store_changed:
            workflow_id = activity_input.workflow_id
        else:
            assert existing_backup is not None
            workflow_id = existing_backup.workflow_id or activity_input.workflow_id

        await client.record_configuration_backup(
            ConfigurationBackupIntent(
                device_id=activity_input.device_id,
                config_store_url=config_store_ui_url(),
                commit_id=activity_input.commit_id,
                filename=fname,
                user=activity_input.user,
                commit_message=activity_input.commit_message,
                workflow_id=workflow_id,
                deployed_commit_id=deployed_commit_id,
            )
        )
    if config_store_changed:
        return True, f"Persisted new backup configuration:\n{markdown}"
    return False, f"No diff to previous backup execution:\n{markdown}"
