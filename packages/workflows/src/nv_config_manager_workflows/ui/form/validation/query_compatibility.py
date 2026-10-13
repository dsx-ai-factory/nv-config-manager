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
"""Compatibility metadata for already-shipped workflow-form query parameters."""

from collections.abc import Mapping

QUERY_ALIASES: Mapping[tuple[str, str], tuple[str, ...]] = {
    (
        "nv_config_manager_workflows.workflows.spx_overlay.SpXOverlayCreationInput",
        "namespace_tag",
    ): ("namespace",),
    (
        "nv_config_manager_workflows.workflows.spx_overlay.SpXOverlayDeletionInput",
        "namespace_tag",
    ): ("namespace",),
    (
        "nv_config_manager_workflows.workflows.device_password_rotation."
        "DevicePasswordRotationInput",
        "device_id",
    ): ("device",),
    (
        "nv_config_manager_workflows.workflows.infiniband_cable_validation."
        "InfinibandCableValidationInput",
        "ufm_device_id",
    ): ("device",),
    (
        "nv_config_manager_workflows.workflows.cable_validation.SiteCableValidationInput",
        "roles",
    ): ("role",),
    (
        "nv_config_manager_workflows.workflows.cumulus_hardware_validation.ValidateHardwareInput",
        "roles",
    ): ("role",),
    (
        "nv_config_manager_workflows.workflows.site_backup.SiteBackupInput",
        "roles",
    ): ("role",),
    (
        "nv_config_manager_workflows.workflows.site_password_rotation.SitePasswordRotationInput",
        "roles",
    ): ("role",),
}
"""Already-shipped URL aliases, keyed by (input model, property)."""

QUERY_SEPARATORS: Mapping[tuple[str, str], str] = {
    (
        "nv_config_manager_workflows.workflows.spx_overlay.SpXOverlayTenantChangeInput",
        "port_names",
    ): ",",
}
"""Already-shipped multi-value URL delimiters, keyed by (input model, property)."""

__all__ = ["QUERY_ALIASES", "QUERY_SEPARATORS"]
