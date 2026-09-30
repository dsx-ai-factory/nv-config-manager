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
"""Configuration backup activities."""

from contextlib import closing

from nv_config_manager_clients._types import ConfigStoreType
from nv_config_manager_dcim.models import ConfigurationBackupIntent
from nv_config_manager_dcim.workflow_models import NetworkDeviceData
from temporalio import activity

from nv_config_manager_workflows.activities.backup.helpers import (
    backup_filename,
    format_backup_markdown,
    resolve_user_domain,
)
from nv_config_manager_workflows.activities.backup.models import (
    PersistConfigBackupInput,
    RecordBackupConfigManagerPluginInput,
)
from nv_config_manager_workflows.runtime import (
    get_config_store_runtime,
    get_dcim_client,
    get_device_connection,
)


@activity.defn
def load_running_configuration(device_data: NetworkDeviceData) -> str:
    """Load the running configuration for the given device."""
    with closing(get_device_connection(device_data)) as connection:
        return connection.get_running_configuration()


@activity.defn
async def persist_config_backup(activity_input: PersistConfigBackupInput) -> str:
    """Persist the config backup to the Config Store."""
    runtime = get_config_store_runtime()
    client = runtime.client(ConfigStoreType.BACKUP)
    user_domain = resolve_user_domain(
        activity_input.user_domain,
        runtime.default_user_domain,
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
        file = await client.load_file(
            device_uuid=activity_input.device_data.id,
            filename=activity_input.device_data.backup_file,
        )
        return file.commit


@activity.defn
async def record_backup_config_manager_plugin(
    activity_input: RecordBackupConfigManagerPluginInput,
) -> tuple[bool, str]:
    """Record configuration-backup metadata in the configured DCIM."""
    config_store = get_config_store_runtime()
    csclient = config_store.client(ConfigStoreType.BACKUP)
    fname = backup_filename(activity_input.path)

    markdown = format_backup_markdown(
        csclient.file_url(device_uuid=activity_input.device_id, filename=fname)
    )

    client = get_dcim_client()
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
            if existing_backup.workflow_id == activity_input.workflow_id:
                return True, f"Persisted new backup configuration:\n{markdown}"
            return False, f"No diff to previous backup execution:\n{markdown}"

        if config_store_changed:
            workflow_id = activity_input.workflow_id
        else:
            assert existing_backup is not None
            workflow_id = existing_backup.workflow_id or activity_input.workflow_id

        await client.record_configuration_backup(
            ConfigurationBackupIntent(
                device_id=activity_input.device_id,
                config_store_url=config_store.ui_url,
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
