# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Compatibility facade for the package-owned InfiniBand unhealthy ports workflow."""

from nv_config_manager_workflows.workflows.infiniband_get_unhealthy_ports import (
    InfinibandGetUnhealthyPortsInput,
    InfinibandGetUnhealthyPortsWorkflow,
)

__all__ = [
    "InfinibandGetUnhealthyPortsInput",
    "InfinibandGetUnhealthyPortsWorkflow",
]
