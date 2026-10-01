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
import { delay, http, HttpResponse } from "msw";
import { sanitizeUrl } from "@/lib/utils";
import { mockApiURL as apiURL } from "@/config/mockApiUrl";
import { ALL_WORKFLOW_DATA, workflowsMockData } from "@/mocks/data";
import { FORBIDDEN_WORKFLOW_ID } from "@/mocks/data/formData";
import { createGenericWorkflow } from "@/mocks/data/workflows/genericWorkflow";

export const workflowTypes = [
  "BackupWorkflow",
  "SiteBackupWorkflow",
  "ConnectedHostMetadataWorkflow",
  "DeployWorkflow",
  "MultiDeployWorkflow",
  "DeviceCableValidationWorkflow",
  "DevicePasswordRotationWorkflow",
  "HelloWorld",
  "HelloWorldApproval",
  "PortLLDPInfoWorkflow",
  "RedfishProvisioningWorkflow",
  "SiteCableValidationWorkflow",
  "SitePasswordRotationWorkflow",
  "SpXOverlayCreationWorkflow",
  "SpXOverlayDeletionWorkflow",
  "SpXOverlayAssignmentWorkflow",
  "SpXOverlayTenantChangeWorkflow",
  "InfinibandGetUnhealthyPortsWorkflow",
  "InfinibandCableValidationWorkflow",
  "InfinibandMlnxOSUpgradeWorkflow",
  "ReprovisionWorkflow",
  "SwitchOSUpgradeWorkflow",
  "ValidateHardwareWorkflow",
  "IBPKeyCreationWorkflow",
  "IBPKeyMemberAddWorkflow",
  "IBPKeyMemberUpdateWorkflow",
  "IBPKeyMemberDeleteWorkflow",
  "DiagnosticsWorkflow",
  "IBPortGuidDiscoveryWorkflow",
];

const workflowDisplayNames: Record<string, string> = {
  BackupWorkflow: "Configuration Backup",
  SiteBackupWorkflow: "Site Configuration Backup",
  ConnectedHostMetadataWorkflow: "Connected Host Metadata",
  DeployWorkflow: "Configuration Deploy",
  MultiDeployWorkflow: "Multi-Configuration Deploy",
  DeviceCableValidationWorkflow: "Device Cable Validation",
  DevicePasswordRotationWorkflow: "Device Password Rotation",
  PortLLDPInfoWorkflow: "Port LLDP Info",
  SiteCableValidationWorkflow: "Site Cable Validation",
  SitePasswordRotationWorkflow: "Site Password Rotation",
  SpXOverlayCreationWorkflow: "SpX Overlay Creation",
  SpXOverlayDeletionWorkflow: "SpX Overlay Deletion",
  SpXOverlayAssignmentWorkflow: "SpX Overlay Assignment",
  SpXOverlayTenantChangeWorkflow: "SpX Overlay Tenant Change",
  InfinibandGetUnhealthyPortsWorkflow: "InfiniBand Get Unhealthy Ports",
  InfinibandCableValidationWorkflow: "InfiniBand Cable Validation",
  InfinibandMlnxOSUpgradeWorkflow: "InfiniBand MLNX-OS Upgrade",
  ReprovisionWorkflow: "Reprovision",
  SwitchOSUpgradeWorkflow: "Switch OS Upgrade",
  ValidateHardwareWorkflow: "Cumulus Hardware Validation",
  IBPKeyCreationWorkflow: "InfiniBand PKey Creation",
  IBPKeyMemberAddWorkflow: "InfiniBand PKey Member Add",
  IBPKeyMemberUpdateWorkflow: "InfiniBand PKey Member Update",
  IBPKeyMemberDeleteWorkflow: "InfiniBand PKey Member Delete",
  DiagnosticsWorkflow: "Device Diagnostics",
  IBPortGuidDiscoveryWorkflow: "InfiniBand Port GUID Discovery",
};

const workflowEndpoints: Record<string, string> = {
  BackupWorkflow: "/ngc/backup",
  SiteBackupWorkflow: "/ngc/site_backup",
  ConnectedHostMetadataWorkflow: "/ngc/connected_host_metadata",
  DeployWorkflow: "/ngc/deploy",
  MultiDeployWorkflow: "/ngc/multi_deploy",
  DeviceCableValidationWorkflow: "/ngc/device_cable_validation",
  DevicePasswordRotationWorkflow: "/ngc/device_password_rotation",
  HelloWorld: "/hello_world",
  HelloWorldApproval: "/hello_world_approval",
  PortLLDPInfoWorkflow: "/ngc/port_lldp_info",
  RedfishProvisioningWorkflow: "/ngc/redfish_provisioning",
  SiteCableValidationWorkflow: "/ngc/site_cable_validation",
  SitePasswordRotationWorkflow: "/ngc/site_password_rotation",
  SpXOverlayCreationWorkflow: "/ngc/spx_overlay_creation",
  SpXOverlayDeletionWorkflow: "/ngc/spx_overlay_deletion",
  SpXOverlayAssignmentWorkflow: "/ngc/spx_overlay_assignment",
  SpXOverlayTenantChangeWorkflow: "/ngc/spx_overlay_tenant_change",
  InfinibandGetUnhealthyPortsWorkflow: "/ngc/infiniband_get_unhealthy_ports",
  InfinibandCableValidationWorkflow: "/ngc/infiniband_cable_validation",
  InfinibandMlnxOSUpgradeWorkflow: "/ngc/infiniband_mlnx_os_upgrade",
  ReprovisionWorkflow: "/ngc/reprovision",
  SwitchOSUpgradeWorkflow: "/ngc/switch_os_upgrade",
  ValidateHardwareWorkflow: "/ngc/cumulus_hardware_validation",
  IBPKeyCreationWorkflow: "/ngc/ib_pkey_creation",
  IBPKeyMemberAddWorkflow: "/ngc/ib_pkey_member_add",
  IBPKeyMemberUpdateWorkflow: "/ngc/ib_pkey_member_update",
  IBPKeyMemberDeleteWorkflow: "/ngc/ib_pkey_member_delete",
  DiagnosticsWorkflow: "/ngc/diagnostics",
  IBPortGuidDiscoveryWorkflow: "/ngc/ib_port_guid_discovery",
};

const getWorkflowEndpoint = (workflowType: string) => {
  const endpoint = workflowEndpoints[workflowType];
  if (!endpoint) {
    throw new Error(`Missing mock workflow endpoint for ${workflowType}`);
  }
  return endpoint;
};

const getWorkflowExecuteRoles = (workflowType: string) =>
  workflowType === "MultiDeployWorkflow" ? ["nvcm-admin"] : ["all"];

export const workflowMetadata = {
  workflows: workflowTypes.map((workflowType) => ({
    name: workflowType,
    display_name: workflowDisplayNames[workflowType] ?? workflowType,
    description: `${workflowDisplayNames[workflowType] ?? workflowType} workflow`,
    endpoint: getWorkflowEndpoint(workflowType),
    namespace: "ngc",
    cli_name: workflowType.toLowerCase(),
    input_class: `${workflowType}Input`,
    read_roles: ["all"],
    execute_roles: getWorkflowExecuteRoles(workflowType),
  })),
};

const getFirstSearchAttribute = (workflow: unknown, key: string): string => {
  const workflowRecord = workflow as {
    search_attributes?: Record<string, Array<string | number | boolean>>;
  };
  return String(workflowRecord.search_attributes?.[key]?.[0] ?? "");
};

const getWorkflowStartTimestamp = (workflow: unknown): number => {
  const workflowRecord = workflow as { start_time?: string };
  return Date.parse(workflowRecord.start_time ?? "");
};

const getWorkflowCloseTimestamp = (workflow: unknown): number => {
  const workflowRecord = workflow as { close_time?: string | null };
  return Date.parse(workflowRecord.close_time ?? "");
};

const getWorkflowDisplayStatus = (workflow: unknown): string => {
  const workflowRecord = workflow as {
    failed_stage?: boolean;
    pending_approval?: boolean;
    status?: string;
  };

  if (workflowRecord.failed_stage) {
    return "FAILED";
  }
  if (workflowRecord.pending_approval) {
    return "PENDING_APPROVAL";
  }
  return workflowRecord.status ?? "";
};

const SEARCH_ATTRIBUTE_FILTERS = [
  ["device_id", "DeviceID"],
  ["device_name", "DeviceName"],
  ["device_platform", "DevicePlatform"],
  ["device_role", "DeviceRole"],
  ["site", "Site"],
  ["user", "User"],
] as const;

type WorkflowFilterRecord = {
  id?: string;
  pending_approval?: boolean;
  status?: string;
  workflow_type?: string;
};

type WorkflowFilters = {
  workflowType: string | null;
  workflowId: string | null;
  status: string | null;
  pendingApproval: boolean;
  hideCompleted: boolean;
  startTime: number;
  endTime: number;
};

const getWorkflowFilters = (searchParams: URLSearchParams): WorkflowFilters => ({
  workflowType: searchParams.get("workflow_type"),
  workflowId: searchParams.get("workflow_id"),
  status: searchParams.get("status"),
  pendingApproval:
    searchParams.get("pending_approval")?.toLowerCase() === "true",
  hideCompleted:
    searchParams.get("hide_completed")?.toLowerCase() === "true",
  startTime: Date.parse(searchParams.get("start_time") ?? ""),
  endTime: Date.parse(searchParams.get("end_time") ?? ""),
});

const matchesWorkflowFields = (
  workflow: unknown,
  workflowRecord: WorkflowFilterRecord,
  filters: WorkflowFilters
): boolean => {
  if (
    filters.workflowType &&
    workflowRecord.workflow_type !== filters.workflowType
  ) {
    return false;
  }
  if (filters.workflowId && workflowRecord.id !== filters.workflowId) {
    return false;
  }
  if (filters.hideCompleted && workflowRecord.status === "COMPLETED") {
    return false;
  }
  if (filters.pendingApproval && !workflowRecord.pending_approval) {
    return false;
  }
  if (
    filters.status &&
    workflowRecord.status !== filters.status &&
    getWorkflowDisplayStatus(workflow) !== filters.status
  ) {
    return false;
  }
  return true;
};

const matchesWorkflowTimeRange = (
  workflow: unknown,
  filters: WorkflowFilters
): boolean => {
  if (Number.isNaN(filters.startTime) && Number.isNaN(filters.endTime)) {
    return true;
  }

  const workflowStartTime = getWorkflowStartTimestamp(workflow);
  const workflowCloseTime = getWorkflowCloseTimestamp(workflow);

  if (Number.isNaN(workflowStartTime)) {
    return false;
  }
  if (
    !Number.isNaN(filters.startTime) &&
    workflowStartTime < filters.startTime
  ) {
    return false;
  }
  if (!Number.isNaN(filters.endTime) && Number.isNaN(workflowCloseTime)) {
    return false;
  }
  if (!Number.isNaN(filters.endTime) && workflowCloseTime > filters.endTime) {
    return false;
  }
  return true;
};

const matchesSearchAttributes = (
  workflow: unknown,
  searchParams: URLSearchParams
): boolean =>
  SEARCH_ATTRIBUTE_FILTERS.every(([param, attribute]) => {
    const value = searchParams.get(param);
    return !value || getFirstSearchAttribute(workflow, attribute) === value;
  });

const matchesWorkflow = (
  workflow: unknown,
  filters: WorkflowFilters,
  searchParams: URLSearchParams
): boolean => {
  const workflowRecord = workflow as WorkflowFilterRecord;
  return (
    matchesWorkflowFields(workflow, workflowRecord, filters) &&
    matchesWorkflowTimeRange(workflow, filters) &&
    matchesSearchAttributes(workflow, searchParams)
  );
};

const filterWorkflows = (workflows: unknown[], url: URL) => {
  const filters = getWorkflowFilters(url.searchParams);
  return workflows.filter((workflow) =>
    matchesWorkflow(workflow, filters, url.searchParams)
  );
};

export const workflowFetchingHandlers = [
  http.get(sanitizeUrl(`${apiURL}/whoami`), async () => {
    return HttpResponse.json(
      { user: "joliao@nvidia.com", roles: ["all", "nvcm-network"] },
      { status: 200 }
    );
  }),
  http.get(sanitizeUrl(`${apiURL}/v1/workflow/types`), async () => {
    return HttpResponse.json(workflowTypes, { status: 200 });
  }),
  http.get(sanitizeUrl(`${apiURL}/v1/workflow/metadata`), async () => {
    return HttpResponse.json(workflowMetadata, { status: 200 });
  }),
  http.get(sanitizeUrl(`${apiURL}/v1/workflow`), async ({ request }) => {
    const url = new URL(request.url);
    const workflowType = url.searchParams.get("workflow_type");
    const nextPageToken = url.searchParams.get("next_page_token");
    const limit = url.searchParams.get("limit");

    const pageSize = limit ? Number.parseInt(limit, 10) : 10;
    const page = nextPageToken ? Number.parseInt(nextPageToken, 10) : 0;

    const workflows = filterWorkflows(
      workflowType
        ? workflowsMockData[workflowType as keyof typeof workflowsMockData]
            ?.workflows || []
        : ALL_WORKFLOW_DATA.workflows,
      url
    );
    const paginatedWorkflows = workflows.slice(
      page * pageSize,
      (page + 1) * pageSize
    );
    const hasMore = (page + 1) * pageSize < workflows.length;

    await delay(2500);

    return HttpResponse.json(
      {
        workflows: paginatedWorkflows,
        next_page_token: hasMore ? (page + 1).toString() : null,
        total_count: workflows.length,
        page_count:
          workflows.length === 0 ? 0 : Math.ceil(workflows.length / pageSize),
      },
      { status: 200 }
    );
  }),
  http.get(sanitizeUrl(`${apiURL}/v1/workflow/:id`), async ({ params }) => {
    const { id } = params;

    if (id === FORBIDDEN_WORKFLOW_ID) {
      return HttpResponse.json(
        {
          error: "Forbidden: You do not have permission to view this workflow",
        },
        { status: 403 }
      );
    }

    await delay(2500);

    return HttpResponse.json(createGenericWorkflow(String(id)));
  }),
];
