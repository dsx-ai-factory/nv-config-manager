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
"""Form-option provider for the built-in diagnostics workflow."""

from nv_config_manager_dcim.models import DCIMDeviceSelectionFilter
from nv_config_manager_dcim.workflow_models import Platform
from pydantic import BaseModel, ConfigDict, Field

from nv_config_manager_workflows.activities.diagnostics import get_available_commands
from nv_config_manager_workflows.runtime import get_dcim_client
from nv_config_manager_workflows.ui import OptionItem, OptionSourceMeta, OptionSourceResponse

_PLATFORM_LABELS = {
    Platform.ARISTA_EOS.value: "Arista EOS",
    Platform.CUMULUS_LINUX.value: "Cumulus Linux",
    Platform.MLNX_OS.value: "MLNX-OS",
    Platform.NV_OS.value: "NV-OS",
}


class DiagnosticsCommandOptionsQuery(BaseModel):
    """Query parameters for diagnostic command options."""

    model_config = ConfigDict(extra="forbid")

    device_id: list[str] = Field(description="Selected managed device IDs")


def _diagnostic_command_options(platforms: list[str]) -> list[OptionItem]:
    """Build the command groups currently rendered by the diagnostics form."""
    catalogs: list[tuple[str, dict[str, str]]] = []
    for platform_name in platforms:
        try:
            catalog = get_available_commands(Platform(platform_name))
        except ValueError:
            catalog = {}
        catalogs.append((platform_name, catalog))

    if not catalogs:
        return []

    if len(catalogs) == 1:
        return [
            OptionItem(label=name, value=name, description=description)
            for name, description in sorted(catalogs[0][1].items())
        ]

    all_names = set().union(*(catalog for _, catalog in catalogs))
    shared_names = {name for name in all_names if all(name in catalog for _, catalog in catalogs)}
    descriptions = {
        name: description for _, catalog in catalogs for name, description in catalog.items()
    }
    options = [
        OptionItem(
            label=name,
            value=name,
            description=descriptions[name],
            group="Runs on all selected devices",
        )
        for name in sorted(shared_names)
    ]
    for platform_name, catalog in catalogs:
        group = f"{_PLATFORM_LABELS.get(platform_name, platform_name)} only"
        options.extend(
            OptionItem(
                label=name,
                value=name,
                description=description,
                group=group,
            )
            for name, description in sorted(catalog.items())
            if name not in shared_names
        )
    return options


async def resolve_diagnostics_command_options(
    query: DiagnosticsCommandOptionsQuery,
) -> OptionSourceResponse:
    """Resolve grouped command options for the selected managed devices."""
    requested_ids = set(query.device_id)
    client = get_dcim_client()
    async with client:
        devices = await client.list_devices(DCIMDeviceSelectionFilter(managed_only=True))

    selected_devices = [device for device in devices if device.id in requested_ids]
    platforms = list(
        dict.fromkeys(device.platform for device in selected_devices if device.platform)
    )
    warnings = []
    if len({device.id for device in selected_devices}) != len(requested_ids):
        warnings.append("Some selected devices could not be resolved.")
    if any(not device.platform for device in selected_devices):
        warnings.append("Some selected devices do not have a platform.")
    known_platforms = {platform.value for platform in Platform}
    unsupported_platforms = sorted(set(platforms) - known_platforms)
    if unsupported_platforms:
        warnings.append(
            "Diagnostic command options are unavailable for unsupported platforms: "
            f"{', '.join(unsupported_platforms)}."
        )

    return OptionSourceResponse(
        items=_diagnostic_command_options(platforms),
        meta=OptionSourceMeta(warnings=warnings),
    )


__all__ = [
    "DiagnosticsCommandOptionsQuery",
    "resolve_diagnostics_command_options",
]
