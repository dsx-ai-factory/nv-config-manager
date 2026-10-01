/*
 * SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
export type SiteConfig = typeof siteConfig;

export const siteConfig = {
  name: "NVIDIA Config Manager",
  description: "Network automation and configuration management.",
  mainNav: [
    {
      title: "Workflows",
      href: "/workflows",
    },
    {
      title: "Configs",
      href: "/configs",
    },
    {
      title: "DHCP",
      href: "/dhcp",
    },
  ],
  workflows: [
    {
      title: "Configuration Backup",
      workflowName: "BackupWorkflow",
      slug: "backupworkflow",
      enabled: true,
    },
    {
      title: "Site Configuration Backup",
      workflowName: "SiteBackupWorkflow",
      slug: "sitebackupworkflow",
      enabled: true,
    },
    {
      title: "Connected Host Metadata",
      workflowName: "ConnectedHostMetadataWorkflow",
      slug: "connectedhostmetadataworkflow",
      enabled: true,
    },
    {
      title: "Configuration Deploy",
      workflowName: "DeployWorkflow",
      slug: "deployworkflow",
      enabled: true,
    },
    {
      title: "Configuration Diff",
      workflowName: "ConfigDiffWorkflow",
      slug: "configdiffworkflow",
      enabled: true,
    },
    {
      title: "Multi-Configuration Deploy",
      workflowName: "MultiDeployWorkflow",
      slug: "multideployworkflow",
      enabled: true,
    },
    {
      title: "Device Cable Validation",
      workflowName: "DeviceCableValidationWorkflow",
      slug: "devicecablevalidationworkflow",
      enabled: true,
    },
    {
      title: "Site Cable Validation",
      workflowName: "SiteCableValidationWorkflow",
      slug: "sitecablevalidationworkflow",
      enabled: true,
    },
    {
      title: "Port LLDP Info",
      workflowName: "PortLLDPInfoWorkflow",
      slug: "portlldpinfoworkflow",
      enabled: true,
    },
    {
      title: "SpX Overlay Creation",
      workflowName: "SpXOverlayCreationWorkflow",
      slug: "spxoverlaycreationworkflow",
      enabled: true,
    },
    {
      title: "SpX Overlay Deletion",
      workflowName: "SpXOverlayDeletionWorkflow",
      slug: "spxoverlaydeletionworkflow",
      enabled: true,
    },
    {
      title: "SpX Overlay Tenant Change",
      workflowName: "SpXOverlayTenantChangeWorkflow",
      slug: "spxoverlaytenantchangeworkflow",
      enabled: true,
    },
    {
      title: "InfiniBand Get Unhealthy Ports",
      workflowName: "InfinibandGetUnhealthyPortsWorkflow",
      slug: "infinibandgetunhealthyportsworkflow",
      enabled: true,
    },
    {
      title: "InfiniBand Cable Validation",
      workflowName: "InfinibandCableValidationWorkflow",
      slug: "infinibandcablevalidationworkflow",
      enabled: true,
    },
    {
      title: "InfiniBand MLNX-OS Upgrade",
      workflowName: "InfinibandMlnxOSUpgradeWorkflow",
      slug: "infinibandmlnxosupgradeworkflow",
      enabled: true,
    },
    {
      title: "Reprovision",
      workflowName: "ReprovisionWorkflow",
      slug: "reprovisionworkflow",
      enabled: true,
    },
    {
      title: "Switch OS Upgrade",
      workflowName: "SwitchOSUpgradeWorkflow",
      slug: "switchosupgradeworkflow",
      enabled: true,
    },
    {
      title: "Cumulus Hardware Validation",
      workflowName: "ValidateHardwareWorkflow",
      slug: "cumulushardwarevalidationworkflow",
      enabled: true,
    },
    {
      title: "Device Password Rotation",
      workflowName: "DevicePasswordRotationWorkflow",
      slug: "devicepasswordrotationworkflow",
      enabled: true,
    },
    {
      title: "Site Password Rotation",
      workflowName: "SitePasswordRotationWorkflow",
      slug: "sitepasswordrotationworkflow",
      enabled: true,
    },
    { // NOSONAR
      title: "Device Diagnostics",
      workflowName: "DiagnosticsWorkflow",
      slug: "diagnosticsworkflow",
      enabled: true,
    },
    {
      title: "InfiniBand Port GUID Discovery",
      workflowName: "IBPortGuidDiscoveryWorkflow",
      slug: "ibportguiddiscoveryworkflow",
      enabled: true,
    },
    {
      title: "InfiniBand PKey Creation",
      workflowName: "IBPKeyCreationWorkflow",
      slug: "ibpkeycreationworkflow",
      enabled: true,
    },
    {
      title: "InfiniBand PKey Member Add",
      workflowName: "IBPKeyMemberAddWorkflow",
      slug: "ibpkeymemberaddworkflow",
      enabled: true,
    },
    {
      title: "InfiniBand PKey Member Update",
      workflowName: "IBPKeyMemberUpdateWorkflow",
      slug: "ibpkeymemberupdateworkflow",
      enabled: true,
    },
    {
      title: "InfiniBand PKey Member Delete",
      workflowName: "IBPKeyMemberDeleteWorkflow",
      slug: "ibpkeymemberdeleteworkflow",
      enabled: true,
    },
  ],
};
