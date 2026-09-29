# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Compatibility exports for the package-owned multi-device deploy workflows."""

from nv_config_manager_workflows.workflows.multi_deploy import (
    CLONE_SEARCH_ATTRS,
    DEFAULT_ACTIVITY_RETRY_POLICY,
    BatchBackupResultData,
    BatchDeployInput,
    BatchDeployWorkflow,
    DeviceDiffData,
    DiffGroup,
    MultiDeployInput,
    MultiDeployWorkflow,
    _format_batch_status,
)

__all__ = [
    "CLONE_SEARCH_ATTRS",
    "DEFAULT_ACTIVITY_RETRY_POLICY",
    "BatchBackupResultData",
    "BatchDeployInput",
    "BatchDeployWorkflow",
    "DeviceDiffData",
    "DiffGroup",
    "MultiDeployInput",
    "MultiDeployWorkflow",
    "_format_batch_status",
]
