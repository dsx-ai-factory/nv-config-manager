# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Compatibility exports for the package-owned configuration diff workflow."""

from nv_config_manager_workflows.workflows.config_diff import (
    CONFIG_DIFF_DEVICE_DESCRIPTION,
    DEFAULT_ACTIVITY_RETRY_POLICY,
    ConfigDiffInput,
    ConfigDiffWorkflow,
    ConfigDiffWorkflowOutput,
)

__all__ = [
    "CONFIG_DIFF_DEVICE_DESCRIPTION",
    "DEFAULT_ACTIVITY_RETRY_POLICY",
    "ConfigDiffInput",
    "ConfigDiffWorkflow",
    "ConfigDiffWorkflowOutput",
]
