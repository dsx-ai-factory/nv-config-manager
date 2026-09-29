# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Compatibility exports for the package-owned deployment workflows."""

from nv_config_manager_workflows.workflows.deploy import (
    DEFAULT_ACTIVITY_RETRY_POLICY,
    INTENDED_CONFIG_COMMIT_ID_DESCRIPTION,
    TENANT_CONFIG_COMMIT_ID_DESCRIPTION,
    DeployInput,
    DeployWorkflow,
    TenantDeployInput,
    TenantDeployWorkflow,
)

__all__ = [
    "DEFAULT_ACTIVITY_RETRY_POLICY",
    "INTENDED_CONFIG_COMMIT_ID_DESCRIPTION",
    "TENANT_CONFIG_COMMIT_ID_DESCRIPTION",
    "DeployInput",
    "DeployWorkflow",
    "TenantDeployInput",
    "TenantDeployWorkflow",
]
