# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Compatibility exports for the package-owned LLDP workflow."""

from nv_config_manager_workflows.workflows.lldp import (
    DEFAULT_ACTIVITY_RETRY_POLICY,
    PortLLDPInfoInput,
    PortLLDPInfoWorkflow,
)

__all__ = ["DEFAULT_ACTIVITY_RETRY_POLICY", "PortLLDPInfoInput", "PortLLDPInfoWorkflow"]
