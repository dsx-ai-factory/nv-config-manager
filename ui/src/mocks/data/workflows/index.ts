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
import type { Workflow } from "@/types/data-table.types";
import backupWorkflows from "./fixtures/backup.json";
import configDiffWorkflows from "./fixtures/config_diff.json";
import connectedHostMetadataWorkflows from "./fixtures/connected_host_metadata.json";
import deployWorkflows from "./fixtures/deploy.json";
import deviceCableValidationWorkflows from "./fixtures/device_cable_validation.json";
import devicePasswordRotationWorkflows from "./fixtures/device_password_rotation.json";
import diagnosticsWorkflows from "./fixtures/diagnostics.json";
import ibPkeyCreationWorkflows from "./fixtures/ib_pkey_creation.json";
import ibPkeyMemberAddWorkflows from "./fixtures/ib_pkey_member_add.json";
import ibPkeyMemberDeleteWorkflows from "./fixtures/ib_pkey_member_delete.json";
import ibPkeyMemberUpdateWorkflows from "./fixtures/ib_pkey_member_update.json";
import ibPortGuidDiscoveryWorkflows from "./fixtures/ib_port_guid_discovery.json";
import infinibandCableValidationWorkflows from "./fixtures/infiniband_cable_validation.json";
import infinibandGetUnhealthyPortsWorkflows from "./fixtures/infiniband_get_unhealthy_ports.json";
import infinibandMlnxOsUpgradeWorkflows from "./fixtures/infiniband_mlnx_os_upgrade.json";
import multiDeployWorkflows from "./fixtures/multi_deploy.json";
import portLldpInfoWorkflows from "./fixtures/port_lldp_info.json";
import reprovisionWorkflows from "./fixtures/reprovision.json";
import siteBackupWorkflows from "./fixtures/site_backup.json";
import siteCableValidationWorkflows from "./fixtures/site_cable_validation.json";
import sitePasswordRotationWorkflows from "./fixtures/site_password_rotation.json";
import spxOverlayCreationWorkflows from "./fixtures/spx_overlay_creation.json";
import spxOverlayDeletionWorkflows from "./fixtures/spx_overlay_deletion.json";
import spxOverlayTenantChangeWorkflows from "./fixtures/spx_overlay_tenant_change.json";
import switchOsUpgradeWorkflows from "./fixtures/switch_os_upgrade.json";
import validateHardwareWorkflows from "./fixtures/validate_hardware.json";

type WorkflowSummaryCollection = {
  workflows: Array<Omit<Workflow, "result" | "stages">>;
  next_page_token: string | null;
};

export const workflowsMockData = Object.fromEntries(
  [
    backupWorkflows,
    connectedHostMetadataWorkflows,
    deployWorkflows,
    deviceCableValidationWorkflows,
    siteCableValidationWorkflows,
    portLldpInfoWorkflows,
    spxOverlayCreationWorkflows,
    spxOverlayDeletionWorkflows,
    infinibandGetUnhealthyPortsWorkflows,
    reprovisionWorkflows,
    configDiffWorkflows,
    devicePasswordRotationWorkflows,
    diagnosticsWorkflows,
    ibPkeyCreationWorkflows,
    ibPkeyMemberAddWorkflows,
    ibPkeyMemberDeleteWorkflows,
    ibPkeyMemberUpdateWorkflows,
    ibPortGuidDiscoveryWorkflows,
    infinibandCableValidationWorkflows,
    infinibandMlnxOsUpgradeWorkflows,
    multiDeployWorkflows,
    siteBackupWorkflows,
    sitePasswordRotationWorkflows,
    spxOverlayTenantChangeWorkflows,
    switchOsUpgradeWorkflows,
    validateHardwareWorkflows,
  ].map((collection) => [collection.workflows[0].workflow_type, collection])
) as Record<string, WorkflowSummaryCollection>;

export const ALL_WORKFLOW_DATA = {
  workflows: Object.values(workflowsMockData).flatMap(
    ({ workflows }) => workflows
  ),
  next_page_token: null,
};
