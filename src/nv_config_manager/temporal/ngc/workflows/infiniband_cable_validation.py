# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Compatibility facade for the package-owned InfiniBand cable validation workflow."""

from nv_config_manager_workflows.workflows.infiniband_cable_validation import (
    InfinibandCableValidationInput,
    InfinibandCableValidationResult,
    InfinibandCableValidationWorkflow,
    InvalidCable,
)

__all__ = [
    "InfinibandCableValidationInput",
    "InfinibandCableValidationResult",
    "InfinibandCableValidationWorkflow",
    "InvalidCable",
]
