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
/**
 * First-party presentation overrides for the "New workflow" launcher, keyed by workflow
 * class name. The launcher lists the `/v1/workflow/metadata` catalog; an override can
 * only rename or hide an entry. It cannot grant permission or change where or what a
 * workflow is submitted; form wording lives in each workflow's `ui_schema`. A workflow
 * without an override is still listed (this is how plugin workflows appear).
 */
export interface WorkflowLauncherOverride {
  /**
   * Title used while the catalog has no entry for the workflow (still loading, failed
   * to load, or not registered); the catalog `display_name` wins otherwise.
   */
  title?: string;
  /** Leave the workflow out of the launcher. */
  hidden?: boolean;
}

/**
 * Built-in workflows whose titles should remain available while the catalog loads.
 * Registered API workflows that have never been offered in the launcher are hidden.
 */
const workflowOverrides: Readonly<Record<string, WorkflowLauncherOverride>> = {
  BackupWorkflow: { title: "Configuration Backup" },
  SiteBackupWorkflow: { title: "Site Configuration Backup" },
  ConnectedHostMetadataWorkflow: { title: "Connected Host Metadata" },
  DeployWorkflow: { title: "Configuration Deploy" },
  ConfigDiffWorkflow: { title: "Configuration Diff" },
  MultiDeployWorkflow: { title: "Multi-Configuration Deploy" },
  DeviceCableValidationWorkflow: { title: "Device Cable Validation" },
  SiteCableValidationWorkflow: { title: "Site Cable Validation" },
  PortLLDPInfoWorkflow: { title: "Port LLDP Info" },
  SpXOverlayCreationWorkflow: { title: "SpX Overlay Creation" },
  SpXOverlayDeletionWorkflow: { title: "SpX Overlay Deletion" },
  SpXOverlayTenantChangeWorkflow: { title: "SpX Overlay Tenant Change" },
  InfinibandGetUnhealthyPortsWorkflow: { title: "InfiniBand Get Unhealthy Ports" },
  InfinibandCableValidationWorkflow: { title: "InfiniBand Cable Validation" },
  InfinibandMlnxOSUpgradeWorkflow: { title: "InfiniBand MLNX-OS Upgrade" },
  ReprovisionWorkflow: { title: "Reprovision" },
  SwitchOSUpgradeWorkflow: { title: "Switch OS Upgrade" },
  ValidateHardwareWorkflow: { title: "Cumulus Hardware Validation" },
  DevicePasswordRotationWorkflow: { title: "Device Password Rotation" },
  SitePasswordRotationWorkflow: { title: "Site Password Rotation" },
  DiagnosticsWorkflow: { title: "Device Diagnostics" },
  IBPortGuidDiscoveryWorkflow: { title: "InfiniBand Port GUID Discovery" },
  IBPKeyCreationWorkflow: { title: "InfiniBand PKey Creation" },
  IBPKeyMemberAddWorkflow: { title: "InfiniBand PKey Member Add" },
  IBPKeyMemberUpdateWorkflow: { title: "InfiniBand PKey Member Update" },
  IBPKeyMemberDeleteWorkflow: { title: "InfiniBand PKey Member Delete" },
  HelloWorld: { hidden: true },
  HelloWorldApproval: { hidden: true },
  NVLinkSwitchFirmwareUpgradeWorkflow: { hidden: true },
  RedfishProvisioningWorkflow: { hidden: true },
  SpXOverlayAssignmentWorkflow: { hidden: true },
};

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
  workflowOverrides,
};
