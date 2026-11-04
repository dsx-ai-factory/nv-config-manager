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
"""Canonical workflow definitions and payload contracts.

Each name is imported from its defining module on first access, so importing one
workflow module does not import the others. The Temporal sandbox re-imports a
run's workflow modules for every run, so eager imports here would make every run
pay for all of them.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
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

_EXPORT_MODULES: dict[str, str] = {
    "BUILTIN_WORKFLOWS": "builtin",
    "BackupInput": "backup",
    "BackupResultData": "site_backup",
    "BackupWorkflow": "backup",
    "BatchBackupResultData": "multi_deploy",
    "BatchDeployInput": "multi_deploy",
    "BatchDeployWorkflow": "multi_deploy",
    "ConfigDiffInput": "config_diff",
    "ConfigDiffWorkflow": "config_diff",
    "ConfigDiffWorkflowOutput": "config_diff",
    "ConnectedHostMetadataWorkflow": "connected_host",
    "ConnectedHostWorkflowInput": "connected_host",
    "DeployInput": "deploy",
    "DeployWorkflow": "deploy",
    "DeviceCableValidationInput": "cable_validation",
    "DeviceCableValidationResult": "cable_validation",
    "DeviceCableValidationWorkflow": "cable_validation",
    "DeviceDiffData": "multi_deploy",
    "DevicePasswordRotationInput": "device_password_rotation",
    "DevicePasswordRotationWorkflow": "device_password_rotation",
    "DiagnosticsWorkflow": "diagnostics",
    "DiagnosticsWorkflowInput": "diagnostics",
    "DiagnosticsWorkflowResult": "diagnostics",
    "DiffGroup": "multi_deploy",
    "HardwareValidationResult": "cumulus_hardware_validation",
    "HelloWorld": "hello_world",
    "HelloWorldApproval": "hello_world",
    "HelloWorldInput": "hello_world",
    "HelloWorldRunning": "hello_world",
    "IBPKeyCreationInput": "ib_pkey_creation",
    "IBPKeyCreationWorkflow": "ib_pkey_creation",
    "IBPKeyCreationWorkflowOutput": "ib_pkey_creation",
    "IBPKeyMemberAddInput": "ib_pkey_member_add",
    "IBPKeyMemberAddOutput": "ib_pkey_member_add",
    "IBPKeyMemberAddWorkflow": "ib_pkey_member_add",
    "IBPKeyMemberDeleteInput": "ib_pkey_member_delete",
    "IBPKeyMemberDeleteOutput": "ib_pkey_member_delete",
    "IBPKeyMemberDeleteWorkflow": "ib_pkey_member_delete",
    "IBPKeyMemberUpdateInput": "ib_pkey_member_update",
    "IBPKeyMemberUpdateOutput": "ib_pkey_member_update",
    "IBPKeyMemberUpdateWorkflow": "ib_pkey_member_update",
    "IBPortGuidDiscoveryInput": "ib_port_guid_discovery",
    "IBPortGuidDiscoveryResult": "ib_port_guid_discovery",
    "IBPortGuidDiscoveryWorkflow": "ib_port_guid_discovery",
    "InfinibandCableValidationInput": "infiniband_cable_validation",
    "InfinibandCableValidationResult": "infiniband_cable_validation",
    "InfinibandCableValidationWorkflow": "infiniband_cable_validation",
    "InfinibandGetUnhealthyPortsInput": "infiniband_get_unhealthy_ports",
    "InfinibandGetUnhealthyPortsWorkflow": "infiniband_get_unhealthy_ports",
    "InfinibandMlnxOSUpgradeInput": "infiniband_mlnx_os_upgrade",
    "InfinibandMlnxOSUpgradeWorkflow": "infiniband_mlnx_os_upgrade",
    "InterfaceRef": "ib_pkey_member_add",
    "LOCAL_TEST_WORKFLOWS": "hello_world",
    "MultiDeployInput": "multi_deploy",
    "MultiDeployWorkflow": "multi_deploy",
    "NVLinkSwitchFirmwareUpgradeInput": "nvlinkswitch_firmware_upgrade",
    "NVLinkSwitchFirmwareUpgradeWorkflow": "nvlinkswitch_firmware_upgrade",
    "PasswordRotationResultData": "site_password_rotation",
    "PortLLDPInfoInput": "lldp",
    "PortLLDPInfoWorkflow": "lldp",
    "RedfishProvisioningInput": "bmc",
    "RedfishProvisioningResult": "bmc",
    "RedfishProvisioningWorkflow": "bmc",
    "ReprovisionInput": "reprovision",
    "ReprovisionWorkflow": "reprovision",
    "SiteBackupInput": "site_backup",
    "SiteBackupWorkflow": "site_backup",
    "SiteCableValidationInput": "cable_validation",
    "SiteCableValidationResult": "cable_validation",
    "SiteCableValidationWorkflow": "cable_validation",
    "SitePasswordRotationInput": "site_password_rotation",
    "SitePasswordRotationWorkflow": "site_password_rotation",
    "SpXOverlayAssignmentInput": "spx_overlay",
    "SpXOverlayAssignmentWorkflow": "spx_overlay",
    "SpXOverlayAssignmentWorkflowOutput": "spx_overlay",
    "SpXOverlayCreationInput": "spx_overlay",
    "SpXOverlayCreationWorkflow": "spx_overlay",
    "SpXOverlayCreationWorkflowOutput": "spx_overlay",
    "SpXOverlayDeletionInput": "spx_overlay",
    "SpXOverlayDeletionWorkflow": "spx_overlay",
    "SpXOverlayDeletionWorkflowOutput": "spx_overlay",
    "SpXOverlayTenantChangeInput": "spx_overlay",
    "SpXOverlayTenantChangeWorkflow": "spx_overlay",
    "SpXOverlayTenantChangeWorkflowOutput": "spx_overlay",
    "SwitchOSUpgradeInput": "os_upgrade",
    "SwitchOSUpgradeWorkflow": "os_upgrade",
    "TenantDeployInput": "deploy",
    "TenantDeployWorkflow": "deploy",
    "TriggerEnum": "backup",
    "ValidateHardwareInput": "cumulus_hardware_validation",
    "ValidateHardwareWorkflow": "cumulus_hardware_validation",
}

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


def __getattr__(name: str) -> object:
    module = _EXPORT_MODULES.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    # The import statement machinery, unlike importlib.import_module, goes through
    # the Temporal sandbox's import hook, which decides passthrough per module.
    value = getattr(__import__(f"{__name__}.{module}", fromlist=[name]), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
