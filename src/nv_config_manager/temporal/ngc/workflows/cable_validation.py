# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Compatibility exports for package-owned cable-validation workflows."""

from nv_config_manager_workflows.workflows.cable_validation import (
    CABLE_STATUS_UPDATE_PATCH_ID,
    CABLE_VALIDATION_CHILD_TIMEOUT_PATCH_ID,
    DCIM_PERSISTENCE_PENDING_MESSAGE,
    CableStatusPersistenceMixin,
    DeviceCableValidationInput,
    DeviceCableValidationResult,
    DeviceCableValidationWorkflow,
    InvalidCable,
    SiteCableValidationInput,
    SiteCableValidationResult,
    SiteCableValidationWorkflow,
)

__all__ = [
    "CABLE_STATUS_UPDATE_PATCH_ID",
    "CABLE_VALIDATION_CHILD_TIMEOUT_PATCH_ID",
    "DCIM_PERSISTENCE_PENDING_MESSAGE",
    "CableStatusPersistenceMixin",
    "DeviceCableValidationInput",
    "DeviceCableValidationResult",
    "DeviceCableValidationWorkflow",
    "InvalidCable",
    "SiteCableValidationInput",
    "SiteCableValidationResult",
    "SiteCableValidationWorkflow",
]
