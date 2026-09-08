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
import fs from "node:fs/promises";
import path from "node:path";

import { expect, test as base, type Page, type Route } from "@playwright/test";

const SCREENSHOT_DIR = path.resolve(
  process.cwd(),
  "../docs/assets/images/workflows"
);
const CARD_SELECTOR =
  "xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' rounded-lg ')][1]";
const AIR_SITE = "TRIAL01 - demo";
const AIR_TAN_LEAF_01_ID = "air-trial-tan-leaf-01";
const PDX_SITE = "PDX01";
const PDX_CUMULUS_ID = "pdx01-cumulus-leaf-01";
const PDX_MLNX_ID = "pdx01-mlx-switch-01";
const UFM_DEVICE_ID = "ufm-01";
const DEMO_VPC_ID = "vpc-demo-101";

const DOC_WORKFLOW_DISPLAY_NAMES: Record<string, string> = {
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
  VpcCreationWorkflow: "VPC Creation",
  VpcDeletionWorkflow: "VPC Deletion",
  VpcTenantChangeWorkflow: "VPC Tenant Change",
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

const DOC_WORKFLOW_ENDPOINTS: Record<string, string> = {
  BackupWorkflow: "/ngc/backup",
  SiteBackupWorkflow: "/ngc/site_backup",
  ConnectedHostMetadataWorkflow: "/ngc/connected_host_metadata",
  DeployWorkflow: "/ngc/deploy",
  MultiDeployWorkflow: "/ngc/multi_deploy",
  DeviceCableValidationWorkflow: "/ngc/device_cable_validation",
  DevicePasswordRotationWorkflow: "/ngc/device_password_rotation",
  PortLLDPInfoWorkflow: "/ngc/port_lldp_info",
  SiteCableValidationWorkflow: "/ngc/site_cable_validation",
  SitePasswordRotationWorkflow: "/ngc/site_password_rotation",
  VpcCreationWorkflow: "/ngc/vpc_creation",
  VpcDeletionWorkflow: "/ngc/vpc_deletion",
  VpcTenantChangeWorkflow: "/ngc/vpc-tenant-change",
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

function getDocWorkflowEndpoint(workflowType: string): string {
  const endpoint = DOC_WORKFLOW_ENDPOINTS[workflowType];
  if (!endpoint) {
    throw new Error(`Missing docs workflow endpoint for ${workflowType}`);
  }
  return endpoint;
}

type ParameterOption = {
  id: string;
  name: string;
};

type Device = {
  id: string;
  name: string;
  platform: string;
  role: string;
  status: string;
  tenant: string;
};

type DocWorkflow = {
  id: string;
  workflow_type: string;
  workflow_input: Record<string, unknown>;
  started_by: string;
  start_time: string;
  close_time: string | null;
  status: string;
  pending_approval: boolean;
  failed_stage?: boolean;
  stages: unknown[];
  result: unknown;
  search_attributes: Record<string, Array<string | boolean>>;
  href: string;
};

type DocWorkflowFixture = {
  id: string;
  workflowType: string;
  status: string;
  pendingApproval: boolean;
  user: string;
  site: string;
  deviceName: string;
  deviceId: string;
  deviceRole: string;
  devicePlatform: string;
  startTime: string;
  closeTime?: string | null;
};

type QueryValue = string | string[];

type WorkflowScreenshot = {
  fileName: string;
  path: string;
  query?: Record<string, QueryValue>;
  title: string;
};

const DOC_SITES: ParameterOption[] = [
  { id: AIR_SITE, name: AIR_SITE },
  { id: PDX_SITE, name: PDX_SITE },
  { id: "RNO1", name: "RNO1" },
];

const DOC_ROLES: ParameterOption[] = [
  { id: "TAN-HLEAF", name: "TAN-HLEAF" },
  { id: "CIN-Leaf", name: "CIN-Leaf" },
  { id: "CIN-Spine", name: "CIN-Spine" },
  { id: "UFM", name: "UFM" },
  { id: "wan", name: "wan" },
];

const DOC_STATUSES: ParameterOption[] = [
  { id: "Active", name: "Active" },
  { id: "Provisioned", name: "Provisioned" },
  { id: "Provisioning", name: "Provisioning" },
  { id: "Staged", name: "Staged" },
];

const DOC_TENANTS: ParameterOption[] = [
  { id: "NGC", name: "NGC" },
  { id: "TenantA", name: "TenantA" },
  { id: "TenantB", name: "TenantB" },
];

const DOC_NAMESPACE_TAGS: ParameterOption[] = [
  { id: "spectrumx", name: "spectrumx" },
];

const DOC_DEVICES_BY_SITE: Record<string, Device[]> = {
  [AIR_SITE]: [
    {
      id: AIR_TAN_LEAF_01_ID,
      name: "tan-leaf-01",
      platform: "Cumulus Linux",
      role: "TAN-HLEAF",
      status: "Provisioned",
      tenant: "NGC",
    },
    {
      id: "air-trial-tan-leaf-02",
      name: "tan-leaf-02",
      platform: "Cumulus Linux",
      role: "TAN-HLEAF",
      status: "Provisioned",
      tenant: "NGC",
    },
    {
      id: "air-trial-tan-leaf-03",
      name: "tan-leaf-03",
      platform: "Cumulus Linux",
      role: "TAN-HLEAF",
      status: "Provisioned",
      tenant: "NGC",
    },
    {
      id: "air-trial-tan-leaf-04",
      name: "tan-leaf-04",
      platform: "Cumulus Linux",
      role: "TAN-HLEAF",
      status: "Provisioned",
      tenant: "NGC",
    },
    {
      id: "air-trial-tan-leaf-05",
      name: "tan-leaf-05",
      platform: "Cumulus Linux",
      role: "TAN-HLEAF",
      status: "Provisioned",
      tenant: "NGC",
    },
  ],
  [PDX_SITE]: [
    {
      id: PDX_CUMULUS_ID,
      name: "pdx01-cumulus-leaf-01",
      platform: "Cumulus Linux",
      role: "CIN-Leaf",
      status: "Provisioned",
      tenant: "TenantB",
    },
    {
      id: "pdx01-arista-leaf-01",
      name: "pdx01-arista-leaf-01",
      platform: "Arista EOS",
      role: "CIN-Leaf",
      status: "Active",
      tenant: "TenantA",
    },
    {
      id: PDX_MLNX_ID,
      name: "infiniband-switch1",
      platform: "MLNX-OS",
      role: "CIN-Spine",
      status: "Active",
      tenant: "TenantB",
    },
    {
      id: UFM_DEVICE_ID,
      name: "ufm-01",
      platform: "UFM",
      role: "UFM",
      status: "Active",
      tenant: "TenantB",
    },
  ],
  RNO1: [
    {
      id: "rno1-cumulus-leaf-01",
      name: "rno1-cumulus-leaf-01",
      platform: "Cumulus Linux",
      role: "CIN-Leaf",
      status: "Active",
      tenant: "TenantA",
    },
  ],
};

const DOC_WORKFLOW_METADATA = {
  workflows: Object.entries(DOC_WORKFLOW_DISPLAY_NAMES).map(
    ([workflowType, displayName]) => ({
      name: workflowType,
      display_name: displayName,
      description: `${displayName} workflow`,
      endpoint: getDocWorkflowEndpoint(workflowType),
      namespace: "ngc",
      cli_name: workflowType.toLowerCase(),
      input_class: `${workflowType}Input`,
      read_roles: ["all"],
      execute_roles:
        workflowType === "MultiDeployWorkflow" ? ["nvcm-admin"] : ["all"],
    })
  ),
};

const DOC_WORKFLOWS: DocWorkflow[] = [
  createDocWorkflow({
    id: "workflow-20260608-000001",
    workflowType: "DeployWorkflow",
    status: "RUNNING",
    pendingApproval: true,
    user: "demo",
    site: AIR_SITE,
    deviceName: "tan-leaf-01",
    deviceId: AIR_TAN_LEAF_01_ID,
    deviceRole: "TAN-HLEAF",
    devicePlatform: "Cumulus Linux",
    startTime: "2026-06-08T16:02:00Z",
  }),
  createDocWorkflow({
    id: "workflow-20260608-000002",
    workflowType: "BackupWorkflow",
    status: "COMPLETED",
    pendingApproval: false,
    user: "demo",
    site: AIR_SITE,
    deviceName: "tan-leaf-02",
    deviceId: "air-trial-tan-leaf-02",
    deviceRole: "TAN-HLEAF",
    devicePlatform: "Cumulus Linux",
    startTime: "2026-06-08T15:48:00Z",
    closeTime: "2026-06-08T15:51:00Z",
  }),
  createDocWorkflow({
    id: "workflow-20260608-000003",
    workflowType: "DiagnosticsWorkflow",
    status: "RUNNING",
    pendingApproval: false,
    user: "nvcm-network",
    site: PDX_SITE,
    deviceName: "pdx01-cumulus-leaf-01",
    deviceId: PDX_CUMULUS_ID,
    deviceRole: "CIN-Leaf",
    devicePlatform: "Cumulus Linux",
    startTime: "2026-06-08T16:10:00Z",
  }),
  createDocWorkflow({
    id: "workflow-20260608-000004",
    workflowType: "InfinibandGetUnhealthyPortsWorkflow",
    status: "FAILED",
    pendingApproval: false,
    user: "demo",
    site: PDX_SITE,
    deviceName: "infiniband-switch1",
    deviceId: PDX_MLNX_ID,
    deviceRole: "CIN-Spine",
    devicePlatform: "MLNX-OS",
    startTime: "2026-06-08T14:37:00Z",
    closeTime: "2026-06-08T14:39:00Z",
  }),
];

const AIR_DEVICE_QUERY = {
  "device-id": AIR_TAN_LEAF_01_ID,
  site: AIR_SITE,
};
const PDX_MLNX_QUERY = {
  "device-id": PDX_MLNX_ID,
  site: PDX_SITE,
};
const SITE_SCOPE_QUERY = {
  role: "TAN-HLEAF",
  site: AIR_SITE,
  status: "Provisioned",
  tenant: "NGC",
};

const WORKFLOW_SCREENSHOTS: WorkflowScreenshot[] = [
  {
    fileName: "backupworkflow-form.png",
    path: "/workflows/backupworkflow/form",
    query: AIR_DEVICE_QUERY,
    title: "New Configuration Backup Workflow",
  },
  {
    fileName: "connectedhostmetadataworkflow-form.png",
    path: "/workflows/connectedhostmetadataworkflow/form",
    query: AIR_DEVICE_QUERY,
    title: "New Connected Host Metadata Workflow",
  },
  {
    fileName: "cumulushardwarevalidationworkflow-form.png",
    path: "/workflows/cumulushardwarevalidationworkflow/form",
    query: SITE_SCOPE_QUERY,
    title: "New Cumulus Hardware Validation Workflow",
  },
  {
    fileName: "deployworkflow-form.png",
    path: "/workflows/deployworkflow/form",
    query: AIR_DEVICE_QUERY,
    title: "New Configuration Deploy Workflow",
  },
  {
    fileName: "devicecablevalidationworkflow-form.png",
    path: "/workflows/devicecablevalidationworkflow/form",
    query: AIR_DEVICE_QUERY,
    title: "New Device Cable Validation Workflow",
  },
  {
    fileName: "devicepasswordrotationworkflow-form.png",
    path: "/workflows/devicepasswordrotationworkflow/form",
    query: {
      device: AIR_TAN_LEAF_01_ID,
      selected_secret: "cumulus",
      site: AIR_SITE,
    },
    title: "New Device Password Rotation Workflow",
  },
  {
    fileName: "ibpkeycreationworkflow-form.png",
    path: "/workflows/ibpkeycreationworkflow/form",
    query: {
      host: "ufm.example.com",
      pkey: "0x8001",
    },
    title: "New InfiniBand PKey Creation Workflow",
  },
  {
    fileName: "ibpkeymemberaddworkflow-form.png",
    path: "/workflows/ibpkeymemberaddworkflow/form",
    query: {
      host: "ufm.example.com",
      pkey: "0x8001",
    },
    title: "New InfiniBand PKey Member Add Workflow",
  },
  {
    fileName: "ibpkeymemberdeleteworkflow-form.png",
    path: "/workflows/ibpkeymemberdeleteworkflow/form",
    query: {
      host: "ufm.example.com",
      pkey: "0x8001",
    },
    title: "New InfiniBand PKey Member Delete Workflow",
  },
  {
    fileName: "ibpkeymemberupdateworkflow-form.png",
    path: "/workflows/ibpkeymemberupdateworkflow/form",
    query: {
      host: "ufm.example.com",
      pkey: "0x8001",
    },
    title: "New InfiniBand PKey Member Update Workflow",
  },
  {
    fileName: "diagnosticsworkflow-form.png",
    path: "/workflows/diagnosticsworkflow/form",
    title: "New Device Diagnostics Workflow",
  },
  {
    fileName: "ibportguiddiscoveryworkflow-form.png",
    path: "/workflows/ibportguiddiscoveryworkflow/form",
    title: "New InfiniBand Port GUID Discovery Workflow",
  },
  {
    fileName: "infinibandcablevalidationworkflow-form.png",
    path: "/workflows/infinibandcablevalidationworkflow/form",
    query: {
      role: ["UFM", "CIN-Spine"],
      site: PDX_SITE,
      status: "Active",
      tenant: "TenantB",
    },
    title: "New InfiniBand Cable Validation Workflow",
  },
  {
    fileName: "infinibandgetunhealthyportsworkflow-form.png",
    path: "/workflows/infinibandgetunhealthyportsworkflow/form",
    query: PDX_MLNX_QUERY,
    title: "New InfiniBand Get Unhealthy Ports Workflow",
  },
  {
    fileName: "infinibandmlnxosupgradeworkflow-form.png",
    path: "/workflows/infinibandmlnxosupgradeworkflow/form",
    query: PDX_MLNX_QUERY,
    title: "New InfiniBand MLNX-OS Upgrade Workflow",
  },
  {
    fileName: "multideployworkflow-form.png",
    path: "/workflows/multideployworkflow/form",
    query: {
      location: AIR_SITE,
      max_batch_size: "5",
      role: "TAN-HLEAF",
      status: "Provisioned",
    },
    title: "New Multi-Configuration Deploy Workflow",
  },
  {
    fileName: "portlldpinfoworkflow-form.png",
    path: "/workflows/portlldpinfoworkflow/form",
    title: "New Port LLDP Info Workflow",
  },
  {
    fileName: "reprovisionworkflow-form.png",
    path: "/workflows/reprovisionworkflow/form",
    query: AIR_DEVICE_QUERY,
    title: "New Reprovision Workflow",
  },
  {
    fileName: "sitecablevalidationworkflow-form.png",
    path: "/workflows/sitecablevalidationworkflow/form",
    query: SITE_SCOPE_QUERY,
    title: "New Site Cable Validation Workflow",
  },
  {
    fileName: "sitebackupworkflow-form.png",
    path: "/workflows/sitebackupworkflow/form",
    query: SITE_SCOPE_QUERY,
    title: "New Site Configuration Backup Workflow",
  },
  {
    fileName: "sitepasswordrotationworkflow-form.png",
    path: "/workflows/sitepasswordrotationworkflow/form",
    query: {
      location: AIR_SITE,
      role: "TAN-HLEAF",
      selected_secret: "cumulus",
      status: "Provisioned",
      tenant: "NGC",
    },
    title: "New Site Password Rotation Workflow",
  },
  {
    fileName: "switchosupgradeworkflow-form.png",
    path: "/workflows/switchosupgradeworkflow/form",
    query: AIR_DEVICE_QUERY,
    title: "New Switch OS Upgrade Workflow",
  },
  {
    fileName: "spxoverlaycreationworkflow-form.png",
    path: "/workflows/spxoverlaycreationworkflow/form",
    query: {
      overlay_id: DEMO_VPC_ID,
      namespace: "spectrumx",
      rd_max: "65010",
      rd_min: "65000",
      site: AIR_SITE,
      tenant: "TenantB",
    },
    title: "New SpX Overlay Creation Workflow",
  },
  {
    fileName: "spxoverlaydeletionworkflow-form.png",
    path: "/workflows/spxoverlaydeletionworkflow/form",
    query: {
      overlay_id: DEMO_VPC_ID,
      namespace: "spectrumx",
      site: AIR_SITE,
    },
    title: "New SpX Overlay Deletion Workflow",
  },
  {
    fileName: "spxoverlaytenantchangeworkflow-form.png",
    path: "/workflows/spxoverlaytenantchangeworkflow/form",
    query: {
      "device-id": AIR_TAN_LEAF_01_ID,
      overlay_id: DEMO_VPC_ID,
      namespace: "spectrumx",
      port_names: "swp1, swp2",
      site: AIR_SITE,
    },
    title: "New SpX Overlay Tenant Change Workflow",
  },
];

const test = base.extend<{ page: Page }>({
  page: async ({ browser }, run) => {
    const context = await browser.newContext({
      serviceWorkers: "block",
      viewport: { width: 1280, height: 900 },
    });
    const page = await context.newPage();

    await setupDocsMocks(page);
    await page.addStyleTag({
      content: `
        nextjs-portal,
        [data-nextjs-dev-tools-button],
        [data-nextjs-toast] {
          display: none !important;
          visibility: hidden !important;
        }
      `,
    });
    await page.addInitScript(() => {
      window.BYPASS_MSW = true;
    });

    await run(page);
    await context.close();
  },
});

test.describe("workflow form docs screenshots", () => {
  test.beforeAll(async () => {
    await fs.mkdir(SCREENSHOT_DIR, { recursive: true });
  });

  for (const workflow of WORKFLOW_SCREENSHOTS) {
    test(`captures ${workflow.title}`, async ({ page }) => {
      await page.goto(routeWithQuery(workflow.path, workflow.query));
      await page.waitForLoadState("networkidle");

      const heading = page.getByRole("heading", { name: workflow.title });
      await expect(heading).toBeVisible();
      await settleFonts(page);
      await screenshotWorkflowCard(page, workflow);
    });
  }
});

test.describe("workflow page docs screenshots", () => {
  test.beforeAll(async () => {
    await fs.mkdir(SCREENSHOT_DIR, { recursive: true });
  });

  test("captures workflow list page", async ({ page }) => {
    await page.goto("/workflows");
    await page.waitForLoadState("networkidle");

    await expect(page.getByRole("heading", { name: "Workflows" })).toBeVisible();
    await expect(page.getByRole("cell", { name: /Configuration Deploy/ })).toBeVisible();
    await settleFonts(page);
    await screenshotWorkflowListPage(page);
  });

  test("captures user roles popout", async ({ page }) => {
    await page.goto("/workflows");
    await page.waitForLoadState("networkidle");

    await page.getByRole("button", { name: "User roles" }).click();
    const popout = page
      .locator('[data-radix-popper-content-wrapper] > div')
      .filter({ hasText: "Username" })
      .first();
    await expect(popout).toBeVisible();
    await expect(popout.getByText("demo", { exact: true })).toBeVisible();
    await expect(popout.getByText("nvcm-network", { exact: true })).toBeVisible();
    await expect(popout.getByText("all", { exact: true })).toHaveCount(0);
    await settleFonts(page);
    await popout.screenshot({
      path: path.join(SCREENSHOT_DIR, "workflow-user-popout.png"),
    });
  });
});

async function setupDocsMocks(page: Page): Promise<void> {
  await page.route("**/api/config", async (route) => {
    await fulfillJson(route, {
      configStoreApiUrl: "http://localhost:9001",
      dhcpUrl: "http://localhost:9004",
      dcimUrl: "https://nautobot.nvcm.air",
      dcimProvider: "nautobot",
      dcimDisplayName: "Nautobot",
      renderServiceUrl: "http://localhost:9002",
      workflowApiUrl: "http://localhost:9000",
      ztpUrl: "http://localhost:9003",
    });
  });

  await page.route("**/healthcheck", async (route) => {
    await fulfillJson(route, { status: "ok" });
  });

  await page.route("**/whoami", async (route) => {
    await fulfillJson(route, {
      user: "demo",
      roles: ["all", "nvcm-network"],
    });
  });

  await page.route("**/v1/workflow/types", async (route) => {
    await fulfillJson(route, Object.keys(DOC_WORKFLOW_DISPLAY_NAMES));
  });

  await page.route("**/v1/workflow/metadata", async (route) => {
    await fulfillJson(route, DOC_WORKFLOW_METADATA);
  });

  await page.route(/.*\/v1\/workflow\/?(\?.*)?$/, async (route) => {
    const url = new URL(route.request().url());
    if (!url.pathname.match(/\/v1\/workflow\/?$/)) {
      await route.fallback();
      return;
    }

    await fulfillJson(route, getWorkflowListResponse(url));
  });

  await page.route("**/v1/parameter/location*", async (route) => {
    await fulfillJson(route, DOC_SITES);
  });

  await page.route(/.*\/v1\/parameter\/role/, async (route) => {
    await fulfillJson(route, DOC_ROLES);
  });

  await page.route(/.*\/v1\/parameter\/status/, async (route) => {
    await fulfillJson(route, DOC_STATUSES);
  });

  await page.route(/.*\/v1\/parameter\/tenant/, async (route) => {
    await fulfillJson(route, DOC_TENANTS);
  });

  await page.route(/.*\/v1\/parameter\/namespace-tag/, async (route) => {
    await fulfillJson(route, DOC_NAMESPACE_TAGS);
  });

  await page.route(/.*\/v1\/parameter\/overlay/, async (route) => {
    await fulfillJson(route, [{ id: "spx-overlay-demo", name: DEMO_VPC_ID }]);
  });

  await page.route(/^.*\/v1\/parameter\/device(\?.*)?$/, async (route) => {
    const url = new URL(route.request().url());
    await fulfillJson(route, filterDevices(url));
  });

  await page.route("**/v1/parameter/device/*/interfaces", async (route) => {
    await fulfillJson(route, [
      { id: "interface-swp1", name: "swp1" },
      { id: "interface-swp2", name: "swp2" },
      { id: "interface-swp3", name: "swp3" },
    ]);
  });

  await page.route("**/v1/parameter/device/*/password_users", async (route) => {
    await fulfillJson(route, [
      { description: "Cumulus Linux demo user", name: "cumulus" },
      { description: "Administrator demo user", name: "admin" },
    ]);
  });

  await page.route("**/v1/parameter/diagnostics/commands*", async (route) => {
    await fulfillJson(route, [
      { description: "Collect interface state", name: "show interface" },
      { description: "Collect LLDP neighbors", name: "show lldp neighbor" },
    ]);
  });
}

function createDocWorkflow(fixture: DocWorkflowFixture): DocWorkflow {
  const workflowInput = {
    device_id: fixture.deviceId,
    site: fixture.site,
  };

  return {
    id: fixture.id,
    workflow_type: fixture.workflowType,
    workflow_input: workflowInput,
    started_by: fixture.user,
    start_time: fixture.startTime,
    close_time: fixture.closeTime ?? null,
    status: fixture.status,
    pending_approval: fixture.pendingApproval,
    stages: [],
    result: null,
    search_attributes: {
      DeviceID: [fixture.deviceId],
      DeviceName: [fixture.deviceName],
      DevicePlatform: [fixture.devicePlatform],
      DeviceRole: [fixture.deviceRole],
      PendingApproval: [fixture.pendingApproval],
      Site: [fixture.site],
      User: [fixture.user],
    },
    href: `/v1/workflow/${fixture.id}`,
  };
}

function getWorkflowListResponse(url: URL): {
  workflows: DocWorkflow[];
  next_page_token: string | null;
  total_count: number;
  page_count: number;
} {
  const nextPageToken = url.searchParams.get("next_page_token");
  const page = nextPageToken ? Number(nextPageToken) : 0;
  const limit = Number(url.searchParams.get("limit") ?? "10");
  const pageSize = Number.isFinite(limit) && limit > 0 ? limit : 10;
  const filteredWorkflows = filterWorkflows(url);
  const paginatedWorkflows = filteredWorkflows.slice(
    page * pageSize,
    (page + 1) * pageSize
  );
  const hasMore = (page + 1) * pageSize < filteredWorkflows.length;

  return {
    workflows: paginatedWorkflows,
    next_page_token: hasMore ? String(page + 1) : null,
    total_count: filteredWorkflows.length,
    page_count:
      filteredWorkflows.length === 0
        ? 0
        : Math.ceil(filteredWorkflows.length / pageSize),
  };
}

function filterWorkflows(
  url: URL,
  workflows: DocWorkflow[] = DOC_WORKFLOWS
): DocWorkflow[] {
  const searchAttributeFilters = [
    ["device_id", "DeviceID"],
    ["device_name", "DeviceName"],
    ["device_platform", "DevicePlatform"],
    ["device_role", "DeviceRole"],
    ["site", "Site"],
    ["user", "User"],
  ];
  const workflowType = url.searchParams.get("workflow_type");
  const workflowId = url.searchParams.get("workflow_id");
  const status = url.searchParams.get("status");
  const pendingApproval =
    url.searchParams.get("pending_approval")?.toLowerCase() === "true";
  const hideCompleted =
    url.searchParams.get("hide_completed")?.toLowerCase() === "true";
  const startTimeFilter = Date.parse(url.searchParams.get("start_time") ?? "");
  const endTimeFilter = Date.parse(url.searchParams.get("end_time") ?? "");

  return workflows.filter((workflow) => {
    const displayStatus = workflow.failed_stage
      ? "FAILED"
      : workflow.pending_approval
        ? "PENDING_APPROVAL"
        : workflow.status;

    if (workflowType && workflow.workflow_type !== workflowType) {
      return false;
    }
    if (workflowId && workflow.id !== workflowId) {
      return false;
    }
    if (hideCompleted && workflow.status === "COMPLETED") {
      return false;
    }

    if (pendingApproval && !workflow.pending_approval) {
      return false;
    }

    if (
      status &&
      workflow.status !== status &&
      displayStatus !== status
    ) {
      return false;
    }

    if (!Number.isNaN(startTimeFilter)) {
      const workflowStartTime = Date.parse(workflow.start_time);

      if (Number.isNaN(workflowStartTime) || workflowStartTime < startTimeFilter) {
        return false;
      }
    }

    if (!Number.isNaN(endTimeFilter)) {
      const workflowCloseTime = Date.parse(workflow.close_time ?? "");

      if (Number.isNaN(workflowCloseTime) || workflowCloseTime > endTimeFilter) {
        return false;
      }
    }

    return searchAttributeFilters.every(([param, attribute]) => {
      const value = url.searchParams.get(param);
      if (!value) {
        return true;
      }

      return getFirstSearchAttribute(workflow, attribute) === value;
    });
  });
}

function getFirstSearchAttribute(workflow: DocWorkflow, key: string): string {
  return String(workflow.search_attributes[key]?.[0] ?? "");
}

test.describe("workflow docs mock filtering", () => {
  const baseFixture = {
    deviceId: AIR_TAN_LEAF_01_ID,
    deviceName: "tan-leaf-01",
    devicePlatform: "Cumulus Linux",
    deviceRole: "TAN-HLEAF",
    pendingApproval: false,
    site: AIR_SITE,
    status: "COMPLETED",
    user: "demo",
    workflowType: "BackupWorkflow",
  };

  test("matches end-time-only filters by close time without requiring valid start time", () => {
    const closedBeforeEnd = createDocWorkflow({
      ...baseFixture,
      id: "closed-before-end",
      startTime: "not-a-date",
      closeTime: "2026-06-08T15:51:00Z",
    });
    const unfinished = createDocWorkflow({
      ...baseFixture,
      id: "unfinished",
      startTime: "not-a-date",
      closeTime: null,
    });
    const closedAfterEnd = createDocWorkflow({
      ...baseFixture,
      id: "closed-after-end",
      startTime: "not-a-date",
      closeTime: "2026-06-08T15:53:00Z",
    });

    const endTimeOnlyUrl = new URL(
      "https://docs.test/v1/workflow?end_time=2026-06-08T15:52:00Z"
    );
    expect(
      filterWorkflows(endTimeOnlyUrl, [
        closedBeforeEnd,
        unfinished,
        closedAfterEnd,
      ]).map((workflow) => workflow.id)
    ).toEqual(["closed-before-end"]);

    const startAndEndTimeUrl = new URL(
      "https://docs.test/v1/workflow?start_time=2026-06-08T15:00:00Z&end_time=2026-06-08T15:52:00Z"
    );
    expect(filterWorkflows(startAndEndTimeUrl, [closedBeforeEnd])).toEqual([]);
  });
});

function filterDevices(url: URL): Device[] {
  const site = url.searchParams.get("site") || AIR_SITE;
  let devices = [...(DOC_DEVICES_BY_SITE[site] || [])];

  for (const key of new Set(url.searchParams.keys())) {
    if (key === "site" || key === "managed_only") {
      continue;
    }
    const values = url.searchParams.getAll(key);
    devices = devices.filter((device) =>
      values.some((value) => fieldMatches(device, key, value))
    );
  }

  return devices;
}

function fieldMatches(device: Device, key: string, value: string): boolean {
  const fieldValue = device[key as keyof Device];
  if (!fieldValue) {
    return false;
  }
  return fieldValue.toLowerCase().includes(value.toLowerCase());
}

async function fulfillJson(route: Route, json: unknown): Promise<void> {
  await route.fulfill({
    json,
    status: 200,
  });
}

async function settleFonts(page: Page): Promise<void> {
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
}

async function screenshotWorkflowCard(
  page: Page,
  workflow: WorkflowScreenshot
): Promise<void> {
  const outputPath = path.join(SCREENSHOT_DIR, workflow.fileName);

  for (let attempt = 1; attempt <= 3; attempt += 1) {
    const heading = page.getByRole("heading", { name: workflow.title });
    const card = heading.locator(CARD_SELECTOR);
    await expect(card).toBeVisible();
    await page.waitForTimeout(250);

    try {
      await card.screenshot({ path: outputPath });
      return;
    } catch (error) {
      if (attempt === 3) {
        throw error;
      }
      await page.waitForTimeout(500);
    }
  }
}

async function screenshotWorkflowListPage(page: Page): Promise<void> {
  const viewport = page.viewportSize() ?? { width: 1280, height: 900 };
  const pageContentBox = await page.locator("div.container.py-6").first().boundingBox();

  if (!pageContentBox) {
    throw new Error("Workflow page content was not available for screenshot.");
  }

  await page.screenshot({
    clip: {
      height: Math.min(
        viewport.height,
        Math.ceil(pageContentBox.y + pageContentBox.height + 24)
      ),
      width: viewport.width,
      x: 0,
      y: 0,
    },
    path: path.join(SCREENSHOT_DIR, "workflow-list.png"),
  });
}

function routeWithQuery(
  routePath: string,
  query?: Record<string, QueryValue>
): string {
  if (!query) {
    return routePath;
  }

  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    const values = Array.isArray(value) ? value : [value];
    for (const item of values) {
      params.append(key, item);
    }
  }

  return `${routePath}?${params.toString()}`;
}
