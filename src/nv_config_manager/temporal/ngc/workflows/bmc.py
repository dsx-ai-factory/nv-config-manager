# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Compatibility exports for the package-owned Redfish provisioning workflow."""

from nv_config_manager_workflows.workflows.bmc import (
    DCIM_STAGE_IDENTIFIERS_PATCH,
    NIC_MANUFACTURER_MELLANOX,
    RedfishProvisioningInput,
    RedfishProvisioningResult,
    RedfishProvisioningWorkflow,
)

__all__ = [
    "DCIM_STAGE_IDENTIFIERS_PATCH",
    "NIC_MANUFACTURER_MELLANOX",
    "RedfishProvisioningInput",
    "RedfishProvisioningResult",
    "RedfishProvisioningWorkflow",
]
