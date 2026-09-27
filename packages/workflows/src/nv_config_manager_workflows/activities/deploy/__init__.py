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
"""Configuration deployment and diff activities."""

import asyncio
import re
from contextlib import closing

from nv_config_manager_clients._types import ConfigStoreType
from nv_config_manager_dcim.workflow_models import NetworkDeviceData
from temporalio import activity
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.activities.deploy.models import (
    ConfigApplyActivityInput,
    DiffActivityInput,
    LoadPartialConfigurationActivityInput,
    ValidateConfigDiffActivityInput,
    ValidateConfigDiffActivityOutput,
    WaitForTenantRenderInput,
    WaitForTenantRenderOutput,
)
from nv_config_manager_workflows.runtime import (
    get_config_store_runtime,
    get_device_connection,
)


@activity.defn
async def load_intended_configuration(
    device_data: NetworkDeviceData,
) -> tuple[str, str, str]:
    """Load the latest intended config for a device from the Config Store."""
    client = get_config_store_runtime().client(ConfigStoreType.INTENDED)
    async with client:
        file = await client.load_file(
            device_uuid=device_data.id,
            filename=device_data.intended_config_file,
        )
        url = client.file_url(
            device_uuid=device_data.id,
            filename=device_data.intended_config_file,
            version=file.commit,
        )
    return file.content, file.commit, url


@activity.defn
async def load_partial_configuration(
    activity_input: LoadPartialConfigurationActivityInput,
) -> tuple[str, str, str]:
    """Load the latest or pinned partial config from the Config Store."""
    client = get_config_store_runtime().client(ConfigStoreType.INTENDED)
    async with client:
        if activity_input.commit_id is None:
            file = await client.load_file(
                device_uuid=activity_input.device_data.id,
                filename=activity_input.config_file,
            )
            content = file.content
            commit_id = file.commit
        else:
            file_data = await client.get_config_file(
                device_uuid=activity_input.device_data.id,
                filename=activity_input.config_file,
                version=int(activity_input.commit_id),
            )
            content = str(file_data["content"])
            commit_id = str(file_data["version"])
        url = client.file_url(
            device_uuid=activity_input.device_data.id,
            filename=activity_input.config_file,
            version=commit_id,
        )
    return content, commit_id, url


@activity.defn
def perform_candidate_diff(activity_input: DiffActivityInput) -> str:
    """Load the candidate configuration and return the diff."""
    with closing(get_device_connection(activity_input.device_data)) as connection:
        return connection.perform_candidate_diff(
            activity_input.configuration, partial=activity_input.partial
        )


@activity.defn
def apply_approved_configuration(
    activity_input: ConfigApplyActivityInput,
) -> None:
    """Load the candidate configuration and apply it."""
    with closing(get_device_connection(activity_input.device_data)) as connection:
        connection.commit_candidate_config(
            activity_input.configuration,
            activity_input.approved_diff,
            commit_confirm=activity_input.commit_confirm,
            partial=activity_input.partial,
        )


@activity.defn
def validate_config_diff(
    activity_input: ValidateConfigDiffActivityInput,
) -> ValidateConfigDiffActivityOutput:
    """Validate that a diff contains only allowed tenant configuration commands."""
    if not activity_input.allowed_patterns and not activity_input.disallowed_patterns:
        raise ApplicationError(
            "At least one of 'allowed_patterns' or 'disallowed_patterns' must be provided"
        )

    if not activity_input.diff.strip():
        return ValidateConfigDiffActivityOutput(valid=True)

    disallowed_patterns = activity_input.disallowed_patterns or []
    allowed_patterns = activity_input.allowed_patterns or []

    invalid_lines = []
    for line in activity_input.diff.splitlines():
        line = line.strip()
        if not line:
            continue
        is_disallowed = disallowed_patterns and any(
            re.match(pattern, line) for pattern in disallowed_patterns
        )
        is_not_allowed = allowed_patterns and not any(
            re.match(pattern, line) for pattern in allowed_patterns
        )
        if is_disallowed or is_not_allowed:
            invalid_lines.append(line)

    if invalid_lines:
        return ValidateConfigDiffActivityOutput(
            valid=False,
            message=(f"Validation failed: {len(invalid_lines)} lines not allowed: {invalid_lines}"),
        )
    return ValidateConfigDiffActivityOutput(valid=True)


@activity.defn
async def wait_for_tenant_render(
    activity_input: WaitForTenantRenderInput,
) -> WaitForTenantRenderOutput:
    """Wait for tenant render to be available with the specified config ID."""
    config_id = activity_input.config_id
    config_client = get_config_store_runtime().client(ConfigStoreType.INTENDED)

    async with config_client:
        if config_id is None:
            await config_client.load_file(
                device_uuid=activity_input.device.id,
                filename=activity_input.device.tenant_config_file,
            )
            return WaitForTenantRenderOutput(config_id=None)

        expected_commit = int(config_id)
        for _attempt in range(activity_input.max_attempts):
            file = await config_client.load_file(
                device_uuid=activity_input.device.id,
                filename=activity_input.device.tenant_config_file,
            )

            if int(file.commit) >= expected_commit:
                return WaitForTenantRenderOutput(config_id=file.commit)

            await asyncio.sleep(activity_input.interval)

        raise ApplicationError(
            f"Tenant render not available after "
            f"{activity_input.max_attempts * activity_input.interval} seconds"
        )


DEPLOY_ACTIVITIES = (
    load_intended_configuration,
    load_partial_configuration,
    perform_candidate_diff,
    apply_approved_configuration,
    validate_config_diff,
    wait_for_tenant_render,
)

__all__ = [
    "DEPLOY_ACTIVITIES",
    "ConfigApplyActivityInput",
    "DiffActivityInput",
    "LoadPartialConfigurationActivityInput",
    "ValidateConfigDiffActivityInput",
    "ValidateConfigDiffActivityOutput",
    "WaitForTenantRenderInput",
    "WaitForTenantRenderOutput",
    "apply_approved_configuration",
    "load_intended_configuration",
    "load_partial_configuration",
    "perform_candidate_diff",
    "validate_config_diff",
    "wait_for_tenant_render",
]
