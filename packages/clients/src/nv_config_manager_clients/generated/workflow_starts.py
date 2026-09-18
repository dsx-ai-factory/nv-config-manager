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

"""Generated workflow-start dispatch; do not edit."""

from nv_config_manager_clients.generated.temporal import models

WORKFLOW_STARTS = {
    '/hello_world': ('helloworld_endpoint_v1_workflow_hello_world_post', 'hello_world_input', models.HelloWorldInput),
    '/hello_world_approval': ('helloworldapproval_endpoint_v1_workflow_hello_world_approval_post', 'hello_world_input', models.HelloWorldInput),
    '/ngc/backup': ('backupworkflow_endpoint_v1_workflow_ngc_backup_post', 'backup_input', models.BackupInput),
    '/ngc/config_diff': ('configdiffworkflow_endpoint_v1_workflow_ngc_config_diff_post', 'config_diff_input', models.ConfigDiffInput),
    '/ngc/connected_host_metadata': ('connectedhostmetadataworkflow_endpoint_v1_workflow_ngc_connected_host_metadata_post', 'connected_host_workflow_input', models.ConnectedHostWorkflowInput),
    '/ngc/cumulus_hardware_validation': ('validatehardwareworkflow_endpoint_v1_workflow_ngc_cumulus_hardware_validation_post', 'validate_hardware_input', models.ValidateHardwareInput),
    '/ngc/deploy': ('deployworkflow_endpoint_v1_workflow_ngc_deploy_post', 'deploy_input', models.DeployInput),
    '/ngc/device_cable_validation': ('devicecablevalidationworkflow_endpoint_v1_workflow_ngc_device_cable_validation_post', 'device_cable_validation_input', models.DeviceCableValidationInput),
    '/ngc/device_password_rotation': ('devicepasswordrotationworkflow_endpoint_v1_workflow_ngc_device_password_rotation_post', 'device_password_rotation_input', models.DevicePasswordRotationInput),
    '/ngc/diagnostics': ('diagnosticsworkflow_endpoint_v1_workflow_ngc_diagnostics_post', 'diagnostics_workflow_input', models.DiagnosticsWorkflowInput),
    '/ngc/ib_pkey_creation': ('ibpkeycreationworkflow_endpoint_v1_workflow_ngc_ib_pkey_creation_post', 'ibp_key_creation_input', models.IBPKeyCreationInput),
    '/ngc/ib_pkey_member_add': ('ibpkeymemberaddworkflow_endpoint_v1_workflow_ngc_ib_pkey_member_add_post', 'ibp_key_member_add_input', models.IBPKeyMemberAddInput),
    '/ngc/ib_pkey_member_delete': ('ibpkeymemberdeleteworkflow_endpoint_v1_workflow_ngc_ib_pkey_member_delete_post', 'ibp_key_member_delete_input', models.IBPKeyMemberDeleteInput),
    '/ngc/ib_pkey_member_update': ('ibpkeymemberupdateworkflow_endpoint_v1_workflow_ngc_ib_pkey_member_update_post', 'ibp_key_member_update_input', models.IBPKeyMemberUpdateInput),
    '/ngc/ib_port_guid_discovery': ('ibportguiddiscoveryworkflow_endpoint_v1_workflow_ngc_ib_port_guid_discovery_post', 'ib_port_guid_discovery_input', models.IBPortGuidDiscoveryInput),
    '/ngc/infiniband_cable_validation': ('infinibandcablevalidationworkflow_endpoint_v1_workflow_ngc_infiniband_cable_validation_post', 'infiniband_cable_validation_input', models.InfinibandCableValidationInput),
    '/ngc/infiniband_get_unhealthy_ports': ('infinibandgetunhealthyportsworkflow_endpoint_v1_workflow_ngc_infiniband_get_unhealthy_ports_post', 'infiniband_get_unhealthy_ports_input', models.InfinibandGetUnhealthyPortsInput),
    '/ngc/infiniband_mlnx_os_upgrade': ('infinibandmlnxosupgradeworkflow_endpoint_v1_workflow_ngc_infiniband_mlnx_os_upgrade_post', 'infiniband_mlnx_os_upgrade_input', models.InfinibandMlnxOSUpgradeInput),
    '/ngc/multi_deploy': ('multideployworkflow_endpoint_v1_workflow_ngc_multi_deploy_post', 'multi_deploy_input', models.MultiDeployInput),
    '/ngc/nvlinkswitch_firmware_upgrade': ('nvlinkswitchfirmwareupgradeworkflow_endpoint_v1_workflow_ngc_nvlinkswitch_firmware_upgrade_post', 'nv_link_switch_firmware_upgrade_input', models.NVLinkSwitchFirmwareUpgradeInput),
    '/ngc/port_lldp_info': ('portlldpinfoworkflow_endpoint_v1_workflow_ngc_port_lldp_info_post', 'port_lldp_info_input', models.PortLLDPInfoInput),
    '/ngc/redfish_provisioning': ('redfishprovisioningworkflow_endpoint_v1_workflow_ngc_redfish_provisioning_post', 'redfish_provisioning_input', models.RedfishProvisioningInput),
    '/ngc/reprovision': ('reprovisionworkflow_endpoint_v1_workflow_ngc_reprovision_post', 'reprovision_input', models.ReprovisionInput),
    '/ngc/site_backup': ('sitebackupworkflow_endpoint_v1_workflow_ngc_site_backup_post', 'site_backup_input', models.SiteBackupInput),
    '/ngc/site_cable_validation': ('sitecablevalidationworkflow_endpoint_v1_workflow_ngc_site_cable_validation_post', 'site_cable_validation_input', models.SiteCableValidationInput),
    '/ngc/site_password_rotation': ('sitepasswordrotationworkflow_endpoint_v1_workflow_ngc_site_password_rotation_post', 'site_password_rotation_input', models.SitePasswordRotationInput),
    '/ngc/spx_overlay_assignment': ('spxoverlayassignmentworkflow_endpoint_v1_workflow_ngc_spx_overlay_assignment_post', 'sp_x_overlay_assignment_input', models.SpXOverlayAssignmentInput),
    '/ngc/spx_overlay_creation': ('spxoverlaycreationworkflow_endpoint_v1_workflow_ngc_spx_overlay_creation_post', 'sp_x_overlay_creation_input', models.SpXOverlayCreationInput),
    '/ngc/spx_overlay_deletion': ('spxoverlaydeletionworkflow_endpoint_v1_workflow_ngc_spx_overlay_deletion_post', 'sp_x_overlay_deletion_input', models.SpXOverlayDeletionInput),
    '/ngc/spx_overlay_tenant_change': ('spxoverlaytenantchangeworkflow_endpoint_v1_workflow_ngc_spx_overlay_tenant_change_post', 'sp_x_overlay_tenant_change_input', models.SpXOverlayTenantChangeInput),
    '/ngc/switch_os_upgrade': ('switchosupgradeworkflow_endpoint_v1_workflow_ngc_switch_os_upgrade_post', 'switch_os_upgrade_input', models.SwitchOSUpgradeInput),
}
