# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Compatibility exports for the package-owned NVLink switch firmware workflow."""

from nv_config_manager_workflows.workflows.nvlinkswitch_firmware_upgrade import (
    DEFAULT_ACTIVITY_RETRY_POLICY,
    SUPPORTED_PLATFORMS,
    NVLinkSwitchFirmwareUpgradeInput,
    NVLinkSwitchFirmwareUpgradeWorkflow,
)

__all__ = [
    "DEFAULT_ACTIVITY_RETRY_POLICY",
    "SUPPORTED_PLATFORMS",
    "NVLinkSwitchFirmwareUpgradeInput",
    "NVLinkSwitchFirmwareUpgradeWorkflow",
]
