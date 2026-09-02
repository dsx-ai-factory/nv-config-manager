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
"""NGC Workflow Definitions."""

from nv_config_manager.temporal.ngc.workflows.backup import BackupWorkflow
from nv_config_manager.temporal.ngc.workflows.bmc import RedfishProvisioningWorkflow
from nv_config_manager.temporal.ngc.workflows.cable_validation import (
    DeviceCableValidationWorkflow,
    SiteCableValidationWorkflow,
)
from nv_config_manager.temporal.ngc.workflows.certificate_rotation import (
    CertificateRotationWorkflow,
)
from nv_config_manager.temporal.ngc.workflows.config_diff import ConfigDiffWorkflow
from nv_config_manager.temporal.ngc.workflows.connected_host import ConnectedHostMetadataWorkflow
from nv_config_manager.temporal.ngc.workflows.cumulus_hardware_validation import (
    ValidateHardwareWorkflow,
)
from nv_config_manager.temporal.ngc.workflows.deploy import DeployWorkflow, TenantDeployWorkflow
from nv_config_manager.temporal.ngc.workflows.device_password_rotation import (
    DevicePasswordRotationWorkflow,
)
from nv_config_manager.temporal.ngc.workflows.diagnostics import DiagnosticsWorkflow
from nv_config_manager.temporal.ngc.workflows.ib_pkey_creation import IBPKeyCreationWorkflow
from nv_config_manager.temporal.ngc.workflows.ib_pkey_member_add import IBPKeyMemberAddWorkflow
from nv_config_manager.temporal.ngc.workflows.ib_pkey_member_delete import (
    IBPKeyMemberDeleteWorkflow,
)
from nv_config_manager.temporal.ngc.workflows.ib_pkey_member_update import (
    IBPKeyMemberUpdateWorkflow,
)
from nv_config_manager.temporal.ngc.workflows.ib_port_guid_discovery import (
    IBPortGuidDiscoveryWorkflow,
)
from nv_config_manager.temporal.ngc.workflows.infiniband_cable_validation import (
    InfinibandCableValidationWorkflow,
)
from nv_config_manager.temporal.ngc.workflows.infiniband_get_unhealthy_ports import (
    InfinibandGetUnhealthyPortsWorkflow,
)
from nv_config_manager.temporal.ngc.workflows.infiniband_mlnx_os_upgrade import (
    InfinibandMlnxOSUpgradeWorkflow,
)
from nv_config_manager.temporal.ngc.workflows.lldp import PortLLDPInfoWorkflow
from nv_config_manager.temporal.ngc.workflows.multi_deploy import (
    BatchDeployWorkflow,
    MultiDeployWorkflow,
)
from nv_config_manager.temporal.ngc.workflows.nvlinkswitch_firmware_upgrade import (
    NVLinkSwitchFirmwareUpgradeWorkflow,
)
from nv_config_manager.temporal.ngc.workflows.os_upgrade import SwitchOSUpgradeWorkflow
from nv_config_manager.temporal.ngc.workflows.reprovision import ReprovisionWorkflow
from nv_config_manager.temporal.ngc.workflows.site_backup import SiteBackupWorkflow
from nv_config_manager.temporal.ngc.workflows.site_password_rotation import (
    SitePasswordRotationWorkflow,
)
from nv_config_manager.temporal.ngc.workflows.spx_overlay import (
    SpXOverlayAssignmentWorkflow,
    SpXOverlayCreationWorkflow,
    SpXOverlayDeletionWorkflow,
    SpXOverlayTenantChangeWorkflow,
)

# Worker registration includes both directly executable workflows and internal children.
# Order is enforced by keep-sorted (make lint / make format); see CONTRIBUTING.
REGISTERED_WORKFLOWS = [
    # keep-sorted start
    BackupWorkflow,
    BatchDeployWorkflow,
    CertificateRotationWorkflow,
    ConfigDiffWorkflow,
    ConnectedHostMetadataWorkflow,
    DeployWorkflow,
    DeviceCableValidationWorkflow,
    DevicePasswordRotationWorkflow,
    DiagnosticsWorkflow,
    IBPKeyCreationWorkflow,
    IBPKeyMemberAddWorkflow,
    IBPKeyMemberDeleteWorkflow,
    IBPKeyMemberUpdateWorkflow,
    IBPortGuidDiscoveryWorkflow,
    InfinibandCableValidationWorkflow,
    InfinibandGetUnhealthyPortsWorkflow,
    InfinibandMlnxOSUpgradeWorkflow,
    MultiDeployWorkflow,
    NVLinkSwitchFirmwareUpgradeWorkflow,
    PortLLDPInfoWorkflow,
    RedfishProvisioningWorkflow,
    ReprovisionWorkflow,
    SiteBackupWorkflow,
    SiteCableValidationWorkflow,
    SitePasswordRotationWorkflow,
    SpXOverlayAssignmentWorkflow,
    SpXOverlayCreationWorkflow,
    SpXOverlayDeletionWorkflow,
    SpXOverlayTenantChangeWorkflow,
    SwitchOSUpgradeWorkflow,
    TenantDeployWorkflow,
    ValidateHardwareWorkflow,
    # keep-sorted end
]
