# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Compatibility facade for the package-owned InfiniBand GUID discovery workflow."""

from nv_config_manager_workflows.workflows.ib_port_guid_discovery import (
    IBPortGuidDiscoveryInput,
    IBPortGuidDiscoveryResult,
    IBPortGuidDiscoveryWorkflow,
)

__all__ = [
    "IBPortGuidDiscoveryInput",
    "IBPortGuidDiscoveryResult",
    "IBPortGuidDiscoveryWorkflow",
]
