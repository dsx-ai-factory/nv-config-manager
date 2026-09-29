# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Compatibility facade for the package-owned InfiniBand PKey member-update workflow."""

from nv_config_manager_workflows.workflows.ib_pkey_member_update import (
    DCIM_STAGE_IDENTIFIERS_PATCH,
    IBPKeyMemberUpdateInput,
    IBPKeyMemberUpdateOutput,
    IBPKeyMemberUpdateWorkflow,
    InterfaceRef,
    _unresolved_guid_values,
)

__all__ = [
    "DCIM_STAGE_IDENTIFIERS_PATCH",
    "IBPKeyMemberUpdateInput",
    "IBPKeyMemberUpdateOutput",
    "IBPKeyMemberUpdateWorkflow",
    "InterfaceRef",
    "_unresolved_guid_values",
]
