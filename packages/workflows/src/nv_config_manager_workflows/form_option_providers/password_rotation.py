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
"""Form-option provider for the built-in site password-rotation workflow."""

from collections.abc import Mapping

from nv_config_manager_dcim.models import (
    DCIMDeviceSelectionFilter,
    dcim_location_reference,
)
from pydantic import BaseModel, ConfigDict, Field

from nv_config_manager_workflows.runtime import get_dcim_client
from nv_config_manager_workflows.ui import OptionItem, OptionSourceMeta, OptionSourceResponse

_SERVICE_ACCOUNT = "svc-ngc-cfa-nv-config-manager"


class PasswordUserOptionsQuery(BaseModel):
    """Device filters used to discover password users for a site rotation."""

    model_config = ConfigDict(extra="forbid")

    location: str = Field(description="Location containing the target devices")
    location_type: str | None = Field(
        default=None,
        description="DCIM location type for the location identifier",
    )
    role: list[str] | None = None
    status: list[str] | None = None
    tenant: str | None = None
    managed_only: bool = Field(
        default=True,
        description="Limit to NVIDIA Config Manager-managed devices",
    )


async def resolve_password_user_options(
    query: PasswordUserOptionsQuery,
) -> OptionSourceResponse:
    """Return password users from the first device matching the supplied filters."""
    filters = DCIMDeviceSelectionFilter(
        sites=(dcim_location_reference(query.location, query.location_type),),
        statuses=tuple(query.status or ()),
        roles=tuple(query.role or ()),
        tenants=(query.tenant,) if query.tenant else (),
        managed_only=query.managed_only,
    )
    client = get_dcim_client()
    async with client:
        devices = await client.list_devices(filters)
        password_mappings = (
            await client.get_device_password_secret_names(devices[0].id) if devices else {}
        )

    items = []
    if isinstance(password_mappings, Mapping):
        items = [
            OptionItem(
                label=username,
                value=username,
                description=f"{username} ({secret_name})",
            )
            for username, secret_name in password_mappings.items()
            if username != _SERVICE_ACCOUNT
        ]

    return OptionSourceResponse(
        items=items,
        meta=OptionSourceMeta(matching_device_count=len(devices)),
    )


__all__ = [
    "PasswordUserOptionsQuery",
    "resolve_password_user_options",
]
