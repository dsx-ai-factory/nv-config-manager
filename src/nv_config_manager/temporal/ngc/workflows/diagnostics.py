# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Compatibility exports for the package-owned diagnostics workflow."""

from nv_config_manager_workflows.workflows.diagnostics import (
    AssembleOutputStageInput,
    AssembleOutputStageOutput,
    CollectTechSupportStageInput,
    CollectTechSupportStageOutput,
    DiagnosticsWorkflow,
    DiagnosticsWorkflowInput,
    DiagnosticsWorkflowResult,
    PostCommentStageInput,
    PostCommentStageOutput,
    ResolveDevicesStageInput,
    ResolveDevicesStageOutput,
    RunDiagnosticsStageInput,
    RunDiagnosticsStageOutput,
    UploadAttachmentStageInput,
    UploadAttachmentStageOutput,
    UploadTechSupportStageInput,
    UploadTechSupportStageOutput,
    ValidateTicketStageInput,
    ValidateTicketStageOutput,
)

__all__ = [
    "AssembleOutputStageInput",
    "AssembleOutputStageOutput",
    "CollectTechSupportStageInput",
    "CollectTechSupportStageOutput",
    "DiagnosticsWorkflow",
    "DiagnosticsWorkflowInput",
    "DiagnosticsWorkflowResult",
    "PostCommentStageInput",
    "PostCommentStageOutput",
    "ResolveDevicesStageInput",
    "ResolveDevicesStageOutput",
    "RunDiagnosticsStageInput",
    "RunDiagnosticsStageOutput",
    "UploadAttachmentStageInput",
    "UploadAttachmentStageOutput",
    "UploadTechSupportStageInput",
    "UploadTechSupportStageOutput",
    "ValidateTicketStageInput",
    "ValidateTicketStageOutput",
]
