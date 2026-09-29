# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Canonical workflow definitions and payload contracts."""

from nv_config_manager_workflows.workflows.backup import (
    BackupInput,
    BackupWorkflow,
    TriggerEnum,
)
from nv_config_manager_workflows.workflows.bmc import (
    RedfishProvisioningInput,
    RedfishProvisioningResult,
    RedfishProvisioningWorkflow,
)
from nv_config_manager_workflows.workflows.builtin import BUILTIN_WORKFLOWS
from nv_config_manager_workflows.workflows.cable_validation import (
    DeviceCableValidationInput,
    DeviceCableValidationResult,
    DeviceCableValidationWorkflow,
    SiteCableValidationInput,
    SiteCableValidationResult,
    SiteCableValidationWorkflow,
)
from nv_config_manager_workflows.workflows.config_diff import (
    ConfigDiffInput,
    ConfigDiffWorkflow,
    ConfigDiffWorkflowOutput,
)
from nv_config_manager_workflows.workflows.connected_host import (
    ConnectedHostMetadataWorkflow,
    ConnectedHostWorkflowInput,
)
from nv_config_manager_workflows.workflows.cumulus_hardware_validation import (
    HardwareValidationResult,
    ValidateHardwareInput,
    ValidateHardwareWorkflow,
)
from nv_config_manager_workflows.workflows.deploy import (
    DeployInput,
    DeployWorkflow,
    TenantDeployInput,
    TenantDeployWorkflow,
)
from nv_config_manager_workflows.workflows.device_password_rotation import (
    DevicePasswordRotationInput,
    DevicePasswordRotationWorkflow,
)
from nv_config_manager_workflows.workflows.diagnostics import (
    DiagnosticsWorkflow,
    DiagnosticsWorkflowInput,
    DiagnosticsWorkflowResult,
)
from nv_config_manager_workflows.workflows.hello_world import (
    LOCAL_TEST_WORKFLOWS,
    HelloWorld,
    HelloWorldApproval,
    HelloWorldInput,
    HelloWorldRunning,
)
from nv_config_manager_workflows.workflows.ib_pkey_creation import (
    IBPKeyCreationInput,
    IBPKeyCreationWorkflow,
    IBPKeyCreationWorkflowOutput,
)
from nv_config_manager_workflows.workflows.ib_pkey_member_add import (
    IBPKeyMemberAddInput,
    IBPKeyMemberAddOutput,
    IBPKeyMemberAddWorkflow,
    InterfaceRef,
)
from nv_config_manager_workflows.workflows.ib_pkey_member_delete import (
    IBPKeyMemberDeleteInput,
    IBPKeyMemberDeleteOutput,
    IBPKeyMemberDeleteWorkflow,
)
from nv_config_manager_workflows.workflows.ib_pkey_member_update import (
    IBPKeyMemberUpdateInput,
    IBPKeyMemberUpdateOutput,
    IBPKeyMemberUpdateWorkflow,
)
from nv_config_manager_workflows.workflows.ib_port_guid_discovery import (
    IBPortGuidDiscoveryInput,
    IBPortGuidDiscoveryResult,
    IBPortGuidDiscoveryWorkflow,
)
from nv_config_manager_workflows.workflows.infiniband_cable_validation import (
    InfinibandCableValidationInput,
    InfinibandCableValidationResult,
    InfinibandCableValidationWorkflow,
)
from nv_config_manager_workflows.workflows.infiniband_get_unhealthy_ports import (
    InfinibandGetUnhealthyPortsInput,
    InfinibandGetUnhealthyPortsWorkflow,
)
from nv_config_manager_workflows.workflows.infiniband_mlnx_os_upgrade import (
    InfinibandMlnxOSUpgradeInput,
    InfinibandMlnxOSUpgradeWorkflow,
)
from nv_config_manager_workflows.workflows.lldp import (
    PortLLDPInfoInput,
    PortLLDPInfoWorkflow,
)
from nv_config_manager_workflows.workflows.multi_deploy import (
    BatchBackupResultData,
    BatchDeployInput,
    BatchDeployWorkflow,
    DeviceDiffData,
    DiffGroup,
    MultiDeployInput,
    MultiDeployWorkflow,
)
from nv_config_manager_workflows.workflows.nvlinkswitch_firmware_upgrade import (
    NVLinkSwitchFirmwareUpgradeInput,
    NVLinkSwitchFirmwareUpgradeWorkflow,
)
from nv_config_manager_workflows.workflows.os_upgrade import (
    SwitchOSUpgradeInput,
    SwitchOSUpgradeWorkflow,
)
from nv_config_manager_workflows.workflows.reprovision import (
    ReprovisionInput,
    ReprovisionWorkflow,
)
from nv_config_manager_workflows.workflows.site_backup import (
    BackupResultData,
    SiteBackupInput,
    SiteBackupWorkflow,
)
from nv_config_manager_workflows.workflows.site_password_rotation import (
    PasswordRotationResultData,
    SitePasswordRotationInput,
    SitePasswordRotationWorkflow,
)
from nv_config_manager_workflows.workflows.spx_overlay import (
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
    "BUILTIN_WORKFLOWS",
    "LOCAL_TEST_WORKFLOWS",
    "BackupInput",
    "BackupResultData",
    "BackupWorkflow",
    "BatchBackupResultData",
    "BatchDeployInput",
    "BatchDeployWorkflow",
    "ConfigDiffInput",
    "ConfigDiffWorkflow",
    "ConfigDiffWorkflowOutput",
    "ConnectedHostMetadataWorkflow",
    "ConnectedHostWorkflowInput",
    "DeployInput",
    "DeployWorkflow",
    "DeviceCableValidationInput",
    "DeviceCableValidationResult",
    "DeviceCableValidationWorkflow",
    "DeviceDiffData",
    "DevicePasswordRotationInput",
    "DevicePasswordRotationWorkflow",
    "DiagnosticsWorkflow",
    "DiagnosticsWorkflowInput",
    "DiagnosticsWorkflowResult",
    "DiffGroup",
    "HardwareValidationResult",
    "HelloWorld",
    "HelloWorldApproval",
    "HelloWorldInput",
    "HelloWorldRunning",
    "IBPKeyCreationInput",
    "IBPKeyCreationWorkflow",
    "IBPKeyCreationWorkflowOutput",
    "IBPKeyMemberAddInput",
    "IBPKeyMemberAddOutput",
    "IBPKeyMemberAddWorkflow",
    "IBPKeyMemberDeleteInput",
    "IBPKeyMemberDeleteOutput",
    "IBPKeyMemberDeleteWorkflow",
    "IBPKeyMemberUpdateInput",
    "IBPKeyMemberUpdateOutput",
    "IBPKeyMemberUpdateWorkflow",
    "IBPortGuidDiscoveryInput",
    "IBPortGuidDiscoveryResult",
    "IBPortGuidDiscoveryWorkflow",
    "InfinibandCableValidationInput",
    "InfinibandCableValidationResult",
    "InfinibandCableValidationWorkflow",
    "InfinibandGetUnhealthyPortsInput",
    "InfinibandGetUnhealthyPortsWorkflow",
    "InfinibandMlnxOSUpgradeInput",
    "InfinibandMlnxOSUpgradeWorkflow",
    "InterfaceRef",
    "MultiDeployInput",
    "MultiDeployWorkflow",
    "NVLinkSwitchFirmwareUpgradeInput",
    "NVLinkSwitchFirmwareUpgradeWorkflow",
    "PasswordRotationResultData",
    "PortLLDPInfoInput",
    "PortLLDPInfoWorkflow",
    "RedfishProvisioningInput",
    "RedfishProvisioningResult",
    "RedfishProvisioningWorkflow",
    "ReprovisionInput",
    "ReprovisionWorkflow",
    "SiteBackupInput",
    "SiteBackupWorkflow",
    "SiteCableValidationInput",
    "SiteCableValidationResult",
    "SiteCableValidationWorkflow",
    "SitePasswordRotationInput",
    "SitePasswordRotationWorkflow",
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
    "SwitchOSUpgradeInput",
    "SwitchOSUpgradeWorkflow",
    "TenantDeployInput",
    "TenantDeployWorkflow",
    "TriggerEnum",
    "ValidateHardwareInput",
    "ValidateHardwareWorkflow",
]
