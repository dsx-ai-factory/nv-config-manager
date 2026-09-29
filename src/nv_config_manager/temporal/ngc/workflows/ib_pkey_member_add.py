# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Compatibility facade for the package-owned InfiniBand PKey member-add workflow."""

from nv_config_manager_workflows.workflows.ib_pkey_member_add import (
    IBPKeyMemberAddInput,
    IBPKeyMemberAddOutput,
    IBPKeyMemberAddWorkflow,
    InterfaceRef,
)

__all__ = [
    "IBPKeyMemberAddInput",
    "IBPKeyMemberAddOutput",
    "IBPKeyMemberAddWorkflow",
    "InterfaceRef",
]
