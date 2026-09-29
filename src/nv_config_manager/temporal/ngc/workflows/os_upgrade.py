# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Compatibility exports for the package-owned switch OS upgrade workflow."""

from nv_config_manager_workflows.workflows.os_upgrade import (
    DEFAULT_ACTIVITY_RETRY_POLICY,
    SUPPORTED_PLATFORMS,
    SwitchOSUpgradeInput,
    SwitchOSUpgradeWorkflow,
)

__all__ = [
    "DEFAULT_ACTIVITY_RETRY_POLICY",
    "SUPPORTED_PLATFORMS",
    "SwitchOSUpgradeInput",
    "SwitchOSUpgradeWorkflow",
]
