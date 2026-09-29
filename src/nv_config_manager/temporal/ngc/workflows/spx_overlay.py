# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Compatibility facade for the package-owned Spectrum-X overlay workflows."""

from nv_config_manager_workflows.workflows.spx_overlay import (
    DEFAULT_ACTIVITY_RETRY_POLICY,
    NAMESPACE_TAG,
    RD_MAX,
    RD_MIN,
    SpXOverlayAssignmentInput,
    SpXOverlayAssignmentWorkflow,
    SpXOverlayAssignmentWorkflowOutput,
    SpXOverlayCreationInput,
    SpXOverlayCreationWorkflow,
    SpXOverlayCreationWorkflowOutput,
    SpXOverlayDeletionInput,
    SpXOverlayDeletionWorkflow,
    SpXOverlayDeletionWorkflowOutput,
    SpXOverlayTenantChangeInput,
    SpXOverlayTenantChangeWorkflow,
    SpXOverlayTenantChangeWorkflowOutput,
)

__all__ = [
    "DEFAULT_ACTIVITY_RETRY_POLICY",
    "NAMESPACE_TAG",
    "RD_MAX",
    "RD_MIN",
    "SpXOverlayAssignmentInput",
    "SpXOverlayAssignmentWorkflow",
    "SpXOverlayAssignmentWorkflowOutput",
    "SpXOverlayCreationInput",
    "SpXOverlayCreationWorkflow",
    "SpXOverlayCreationWorkflowOutput",
    "SpXOverlayDeletionInput",
    "SpXOverlayDeletionWorkflow",
    "SpXOverlayDeletionWorkflowOutput",
    "SpXOverlayTenantChangeInput",
    "SpXOverlayTenantChangeWorkflow",
    "SpXOverlayTenantChangeWorkflowOutput",
]
