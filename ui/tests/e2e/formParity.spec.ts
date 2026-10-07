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
 * The same scenarios on each migrated legacy form page and on
 * `/workflows/new/<ClassName>`, with the POST bodies kept as goldens in
 * `fixtures/form-parity/` (see `shared/formParity.ts`). Both routes get the same
 * mocked API: the real `/metadata` and `/form` snapshots and typed locations.
 *
 * Capture with `UPDATE_PARITY=1 npx playwright test formParity.spec.ts`. Once a
 * workflow is migrated its legacy page redirects, so only its generic side runs.
 */
import { expect, type Page } from "@playwright/test";

import {
  DEVICES_LIST,
  ROLES_LIST,
  SITES_LIST,
  SPX_OVERLAY_LIST,
  STATUS_LIST,
  TENANT_LIST,
  TYPED_LOCATIONS_LIST_API_RESPONSE,
} from "@/mocks/data";

import { mockServerCatalogAndUser, mockTypedLocationsEndpoint } from "./shared/apiMocks";
import {
  UPDATE_PARITY,
  capturePayload,
  checkPayload,
  isMigrated,
  readParityFixture,
  type ParityScenario,
  type ParityWorkflow,
} from "./shared/formParity";
import { test } from "./shared/utils";

const [PDX01_DEVICE, PDX01_DEVICE_2, PDX01_TENANT_A_PROVISIONED] = DEVICES_LIST.PDX01;
const RNO1_DEVICE = DEVICES_LIST.RNO1[0];
// Its name differs from its id, like a Nautobot location name.
const MODULE = TYPED_LOCATIONS_LIST_API_RESPONSE.find(
  (location) => location.location_type === "Module"
)!;

type Device = (typeof DEVICES_LIST.PDX01)[number];

const PDX01 = (platform: string): Device[] =>
  DEVICES_LIST.PDX01.filter((device) => device.platform === platform);

/**
 * The scenarios of a single-device form (Site/Tenant/Status filters and a device):
 * a manual fill, a manual fill narrowed by Tenant, and Nautobot's device link.
 */
const deviceScenarios = (device: Device): ParityScenario[] => [
  {
    name: "manual site and device",
    steps: async (form) => {
      await form.select("Site", SITES_LIST.pdx01);
      await form.select("Device", device.name);
    },
  },
  {
    name: "manual fill narrowed by tenant",
    steps: async (form) => {
      await form.select("Site", SITES_LIST.pdx01);
      await form.select("Tenant (optional)", device.tenant);
      await form.select("Device", device.name);
    },
  },
  {
    // configmanagerdevicestatus_workflows_tab.html
    name: "Nautobot device link",
    query: `?site=${SITES_LIST.pdx01}&device-id=${device.id}&tenant=${device.tenant}`,
    steps: async (form) => {
      await form.expectSelected("Site", SITES_LIST.pdx01);
      await form.expectSelected("Device", device.name);
    },
  },
];

/** The scenarios of a site-wide form (Site, Roles, Device Status, Tenant). */
const siteScenarios: ParityScenario[] = [
  {
    name: "manual fill",
    steps: async (form) => {
      await form.select("Site", SITES_LIST.pdx01);
      await form.select("Roles", ROLES_LIST.leaf, ROLES_LIST.spine);
      await form.select("Device Status", STATUS_LIST.planned);
      await form.select("Tenant", TENANT_LIST.tenant_a);
      await form.expectSelected(
        "Device Status",
        STATUS_LIST.active,
        STATUS_LIST.provisioned,
        STATUS_LIST.planned
      );
    },
  },
  {
    name: "module site, defaults otherwise",
    steps: (form) => form.select("Site", MODULE.name),
  },
  {
    name: "prefill from URL",
    query:
      `?site=${SITES_LIST.pdx01}&role=${ROLES_LIST.leaf}` +
      `&status=${STATUS_LIST.active}&tenant=${TENANT_LIST.tenant_a}`,
    steps: async (form) => {
      await form.expectSelected("Site", SITES_LIST.pdx01);
      await form.expectSelected("Roles", ROLES_LIST.leaf);
      await form.expectSelected("Device Status", STATUS_LIST.active);
      await form.expectSelected("Tenant", TENANT_LIST.tenant_a);
    },
  },
];

/** Fill a text input by its exact label, the same way on both pages. */
const fillText = (page: Page, label: string, value: string) =>
  page.getByLabel(label, { exact: true }).fill(value);

const PARITY_WORKFLOWS: ParityWorkflow[] = [
  {
    workflow: "BackupWorkflow",
    inputModel: "BackupInput",
    endpoint: "/v1/workflow/ngc/backup",
    scenarios: [
      {
        name: "manual site and device",
        steps: async (form) => {
          await form.select("Site", SITES_LIST.pdx01);
          await form.select("Device", PDX01_DEVICE.name);
        },
      },
    ],
  },
  {
    workflow: "IBPKeyCreationWorkflow",
    inputModel: "IBPKeyCreationInput",
    endpoint: "/v1/workflow/ngc/ib_pkey_creation",
    scenarios: [
      {
        name: "manual host only",
        steps: (form) => form.fill("UFM Host", "ufm-1.lab"),
      },
      {
        name: "manual host and pkey, with surrounding spaces",
        steps: async (form) => {
          await form.fill("UFM Host", "  ufm-1.lab  ");
          await form.fill("PKey (optional)", " 0x8001 ");
        },
      },
      {
        name: "blank pkey is omitted",
        steps: async (form) => {
          await form.fill("UFM Host", "ufm-1.lab");
          await form.fill("PKey (optional)", "   ");
        },
      },
      {
        name: "prefill from URL",
        query: "?host=ufm.example.com&pkey=0x0100",
        steps: async (form) => {
          await expect(form.page.getByLabel("UFM Host")).toHaveValue("ufm.example.com");
          await expect(form.page.getByLabel("PKey (optional)")).toHaveValue("0x0100");
        },
      },
    ],
  },
  {
    workflow: "SiteCableValidationWorkflow",
    inputModel: "SiteCableValidationInput",
    endpoint: "/v1/workflow/ngc/site_cable_validation",
    scenarios: [
      {
        name: "manual fill",
        steps: async (form) => {
          await form.select("Site", SITES_LIST.pdx01);
          await form.select("Roles", ROLES_LIST.leaf, ROLES_LIST.spine);
          await form.select("Device Status", STATUS_LIST.planned);
          await form.select("Tenant", TENANT_LIST.tenant_a);
          await form.expectSelected(
            "Device Status",
            STATUS_LIST.active,
            STATUS_LIST.provisioned,
            STATUS_LIST.planned
          );
        },
      },
      {
        name: "module site, defaults otherwise",
        steps: (form) => form.select("Site", MODULE.name),
      },
      {
        name: "prefill from URL",
        query:
          `?site=${SITES_LIST.pdx01}&role=${ROLES_LIST.leaf}` +
          `&status=${STATUS_LIST.active}&tenant=${TENANT_LIST.tenant_a}`,
        steps: async (form) => {
          await form.expectSelected("Site", SITES_LIST.pdx01);
          await form.expectSelected("Roles", ROLES_LIST.leaf);
          await form.expectSelected("Device Status", STATUS_LIST.active);
          await form.expectSelected("Tenant", TENANT_LIST.tenant_a);
        },
      },
      {
        // configmanagerdevicestatus_workflows_tab.html:92
        name: "Nautobot device link",
        query: `?site=${SITES_LIST.pdx01}&device-id=${PDX01_DEVICE.id}&tenant=${TENANT_LIST.ngc}`,
        steps: async (form) => {
          await form.expectSelected("Site", SITES_LIST.pdx01);
          await form.expectSelected("Tenant", TENANT_LIST.ngc);
        },
      },
      {
        // The template does not URL-encode the location name; a device without a
        // tenant renders `tenant=`.
        name: "Nautobot device link, module name with a space, no tenant",
        query: `?site=${MODULE.name}&device-id=${PDX01_DEVICE.id}&tenant=`,
        steps: (form) => form.expectSelected("Site", MODULE.name),
      },
    ],
  },
  {
    workflow: "DeployWorkflow",
    inputModel: "DeployInput",
    endpoint: "/v1/workflow/ngc/deploy",
    scenarios: [
      {
        name: "manual fill",
        steps: async (form) => {
          await form.select("Site", SITES_LIST.pdx01);
          await form.select("Device", PDX01_DEVICE.name);
        },
      },
      {
        name: "manual fill with filters, commit-confirm off",
        steps: async (form) => {
          await form.select("Site", SITES_LIST.pdx01);
          await form.select("Tenant (optional)", TENANT_LIST.tenant_a);
          await form.select("Status (optional)", STATUS_LIST.provisioned);
          await form.select("Device", PDX01_TENANT_A_PROVISIONED.name);
          await form.setChecked("Use commit-confirm", false);
        },
      },
      {
        // configmanagerdevicestatus_workflows_tab.html:46 and inc/pending_deployment.html:5
        name: "Nautobot device link",
        query: `?site=${SITES_LIST.pdx01}&device-id=${PDX01_DEVICE.id}`,
        steps: async (form) => {
          await form.expectSelected("Site", SITES_LIST.pdx01);
          await form.expectSelected("Device", PDX01_DEVICE.name);
        },
      },
      {
        name: "prefill with tenant and status filters",
        query:
          `?site=${SITES_LIST.pdx01}&device-id=${PDX01_TENANT_A_PROVISIONED.id}` +
          `&tenant=${TENANT_LIST.tenant_a}&status=${STATUS_LIST.provisioned}`,
        steps: async (form) => {
          await form.expectSelected("Tenant (optional)", TENANT_LIST.tenant_a);
          await form.expectSelected("Status (optional)", STATUS_LIST.provisioned);
          await form.expectSelected("Device", PDX01_TENANT_A_PROVISIONED.name);
        },
      },
      {
        name: "repeated device-id takes the first",
        query:
          `?site=${SITES_LIST.pdx01}&device-id=${PDX01_DEVICE.id}` +
          `&device-id=${PDX01_DEVICE_2.id}`,
        steps: (form) => form.expectSelected("Device", PDX01_DEVICE.name),
      },
      {
        name: "prefill, then a different site and device",
        query: `?site=${SITES_LIST.pdx01}&device-id=${PDX01_DEVICE.id}`,
        steps: async (form) => {
          await form.expectSelected("Device", PDX01_DEVICE.name);
          await form.select("Site", SITES_LIST.rno1);
          await form.select("Device", RNO1_DEVICE.name);
        },
      },
    ],
  },
  {
    workflow: "SpXOverlayDeletionWorkflow",
    inputModel: "SpXOverlayDeletionInput",
    endpoint: "/v1/workflow/ngc/spx_overlay_deletion",
    scenarios: [
      {
        name: "manual fill",
        steps: async (form) => {
          await form.select("Site", SITES_LIST.pdx01);
          await form.select("Overlay ID", SPX_OVERLAY_LIST.submission);
          await form.select("Namespace Tag", "tenant-a");
        },
      },
      {
        name: "manual fill, default namespace tag",
        steps: async (form) => {
          await form.select("Site", SITES_LIST.pdx01);
          await form.select("Overlay ID", SPX_OVERLAY_LIST.submission);
        },
      },
      {
        name: "namespace tag chosen before the site",
        steps: async (form) => {
          await form.select("Namespace Tag", "tenant-a");
          await form.select("Site", SITES_LIST.pdx01);
          await form.select("Overlay ID", SPX_OVERLAY_LIST.submission);
        },
      },
      {
        name: "prefill from URL, namespace alias",
        query:
          `?site=${SITES_LIST.pdx01}&overlay_id=${SPX_OVERLAY_LIST.primary}` +
          "&namespace=tenant-a",
        steps: async (form) => {
          await form.expectSelected("Overlay ID", SPX_OVERLAY_LIST.primary);
          await form.expectSelected("Namespace Tag", "tenant-a");
        },
      },
      {
        name: "prefill from URL, namespace_tag",
        query:
          `?site=${SITES_LIST.rno1}&overlay_id=${SPX_OVERLAY_LIST.secondary}` +
          "&namespace_tag=tenant-a",
        steps: async (form) => {
          await form.expectSelected("Overlay ID", SPX_OVERLAY_LIST.secondary);
          await form.expectSelected("Namespace Tag", "tenant-a");
        },
      },
      {
        name: "prefill, then a different site, overlay, and namespace tag",
        query:
          `?site=${SITES_LIST.pdx01}&overlay_id=${SPX_OVERLAY_LIST.primary}` +
          "&namespace=tenant-a",
        steps: async (form) => {
          await form.expectSelected("Overlay ID", SPX_OVERLAY_LIST.primary);
          await form.select("Site", SITES_LIST.rno1);
          await form.select("Overlay ID", SPX_OVERLAY_LIST.modified);
          await form.select("Namespace Tag", "spectrumx");
        },
      },
    ],
  },
  {
    // A generic variantRows form: both routes render the same Python declaration.
    workflow: "IBPKeyMemberUpdateWorkflow",
    inputModel: "IBPKeyMemberUpdateInput",
    endpoint: "/v1/workflow/ngc/ib_pkey_member_update",
    scenarios: [
      {
        name: "interfaces",
        steps: async ({ page }) => {
          await page.getByLabel("UFM Host").fill("ufm-1.lab");
          await page.getByLabel("PKey").fill("0x8001");
          await page.getByPlaceholder("device (e.g. hca01)").fill("hca01");
          await page.getByPlaceholder("interface (e.g. mlx5_0)").fill("mlx5_0");
          await page.getByLabel("Membership Type for row 1").click();
          await page.getByRole("option", { name: "limited" }).click();
        },
      },
      {
        name: "GUIDs",
        steps: async ({ page }) => {
          await page.getByLabel("UFM Host").fill("ufm-1.lab");
          await page.getByLabel("PKey").fill("0x8001");
          await page.getByLabel("By GUIDs").click();
          await page.getByLabel("GUID 1").fill("0x0011223344556677");
          await page.getByLabel("Membership Type for row 1").click();
          await page.getByRole("option", { name: "limited" }).click();
          await page.getByRole("button", { name: "Add Row" }).click();
          await page.getByLabel("GUID 2").fill("0x8899aabbccddeeff");
          await page.getByLabel("Membership Type for row 2").click();
          await page.getByRole("option", { name: "full" }).click();
        },
      },
      {
        name: "prefill host and pkey from URL",
        query: "?host=ufm-1.lab&pkey=0x8001",
        steps: async ({ page }) => {
          await expect(page.getByLabel("UFM Host")).toHaveValue("ufm-1.lab");
          await expect(page.getByLabel("PKey")).toHaveValue("0x8001");
          await page.getByPlaceholder("device (e.g. hca01)").fill("hca01");
          await page.getByPlaceholder("interface (e.g. mlx5_0)").fill("mlx5_0");
          await page.getByLabel("Membership Type for row 1").click();
          await page.getByRole("option", { name: "full" }).click();
        },
      },
    ],
  },
  ...(
    [
      ["ConfigDiffWorkflow", "ConfigDiffInput", "config_diff", "Arista EOS"],
      ["ConnectedHostMetadataWorkflow", "ConnectedHostWorkflowInput", "connected_host_metadata", "Arista EOS"],
      ["DeviceCableValidationWorkflow", "DeviceCableValidationInput", "device_cable_validation", "Arista EOS"],
      ["InfinibandGetUnhealthyPortsWorkflow", "InfinibandGetUnhealthyPortsInput", "infiniband_get_unhealthy_ports", "UFM"],
      ["InfinibandMlnxOSUpgradeWorkflow", "InfinibandMlnxOSUpgradeInput", "infiniband_mlnx_os_upgrade", "MLNX-OS"],
      ["ReprovisionWorkflow", "ReprovisionInput", "reprovision", "Cumulus Linux"],
      ["SwitchOSUpgradeWorkflow", "SwitchOSUpgradeInput", "switch_os_upgrade", "Arista EOS"],
    ] as const
  ).map(
    ([workflow, inputModel, endpoint, platform]): ParityWorkflow => ({
      workflow,
      inputModel,
      endpoint: `/v1/workflow/ngc/${endpoint}`,
      scenarios: deviceScenarios(PDX01(platform)[0]),
    })
  ),
  {
    workflow: "DevicePasswordRotationWorkflow",
    inputModel: "DevicePasswordRotationInput",
    endpoint: "/v1/workflow/ngc/device_password_rotation",
    scenarios: [
      {
        name: "manual fill",
        steps: async (form) => {
          await form.select("Site", SITES_LIST.rno1);
          await form.select("Device", RNO1_DEVICE.name);
          await form.select("Secret to Rotate", "admin");
        },
      },
      {
        name: "manual fill, another secret",
        steps: async (form) => {
          await form.select("Site", SITES_LIST.rno1);
          await form.select("Device", DEVICES_LIST.RNO1[1].name);
          await form.select("Secret to Rotate", "cumulus");
        },
      },
    ],
  },
  {
    workflow: "IBPortGuidDiscoveryWorkflow",
    inputModel: "IBPortGuidDiscoveryInput",
    endpoint: "/v1/workflow/ngc/ib_port_guid_discovery",
    scenarios: [
      {
        name: "manual fill, dry run",
        steps: async (form) => {
          await form.select("Site", SITES_LIST.pdx01);
          await form.select("UFM Device", PDX01("UFM")[0].name);
          await form.select("Switch Devices", PDX01("MLNX-OS")[0].name, PDX01("MLNX-OS")[1].name);
        },
      },
      {
        name: "manual fill, dry run off",
        steps: async (form) => {
          await form.select("Site", SITES_LIST.pdx01);
          await form.select("UFM Device", PDX01("UFM")[1].name);
          await form.select("Switch Devices", PDX01("MLNX-OS")[2].name);
          await form.setChecked("Dry run", false);
        },
      },
    ],
  },
  {
    workflow: "InfinibandCableValidationWorkflow",
    inputModel: "InfinibandCableValidationInput",
    endpoint: "/v1/workflow/ngc/infiniband_cable_validation",
    scenarios: [
      {
        name: "manual fill",
        steps: async (form) => {
          await form.select("Site", SITES_LIST.pdx01);
          await form.select("Device", PDX01("UFM")[0].name);
          await form.select("Device IDs", PDX01("MLNX-OS")[0].name, PDX01("MLNX-OS")[1].name);
        },
      },
      {
        name: "prefill from URL",
        query:
          `?site=${SITES_LIST.pdx01}&device=${PDX01("UFM")[0].id}` +
          `&device-id=${PDX01("MLNX-OS")[2].id}`,
        steps: async (form) => {
          await form.expectSelected("Device", PDX01("UFM")[0].name);
          await form.expectSelected("Device IDs", PDX01("MLNX-OS")[2].name);
        },
      },
    ],
  },
  {
    workflow: "MultiDeployWorkflow",
    inputModel: "MultiDeployInput",
    endpoint: "/v1/workflow/ngc/multi_deploy",
    scenarios: [
      {
        name: "role only",
        steps: (form) => form.select("Role", ROLES_LIST.leaf),
      },
      {
        name: "every field",
        steps: async (form) => {
          await form.select("Role", ROLES_LIST.spine);
          await form.fill("Max Batch Size", "25");
          await form.select("Location", SITES_LIST.rno1);
          await form.select("Device Status", STATUS_LIST.active, STATUS_LIST.planned);
          await form.select("Tenant", TENANT_LIST.tenant_a);
          await form.setChecked("Use commit-confirm", false);
        },
      },
      {
        name: "prefill from URL",
        query:
          `?role=${ROLES_LIST.spine}&max_batch_size=15&location=${SITES_LIST.rno1}` +
          `&status=${STATUS_LIST.active}&status=${STATUS_LIST.provisioning}` +
          `&tenant=${TENANT_LIST.tenant_a}`,
        steps: async (form) => {
          await form.expectSelected("Role", ROLES_LIST.spine);
          await form.expectSelected("Location", SITES_LIST.rno1);
          await form.expectSelected("Tenant", TENANT_LIST.tenant_a);
        },
      },
    ],
  },
  {
    workflow: "PortLLDPInfoWorkflow",
    inputModel: "PortLLDPInfoInput",
    endpoint: "/v1/workflow/ngc/port_lldp_info",
    scenarios: [
      {
        name: "device and interface",
        steps: async (form) => {
          await form.select("Site", SITES_LIST.pdx01);
          await form.select("Device", PDX01_DEVICE.name);
          await fillText(form.page, "Interface", "Ethernet1/1");
        },
      },
      {
        name: "MAC address",
        steps: (form) => fillText(form.page, "MAC Address", "00:11:22:33:44:55"),
      },
      {
        name: "prefill device and interface from URL",
        query: `?site=${SITES_LIST.pdx01}&device-id=${PDX01_DEVICE.id}&interface=Ethernet1/1`,
        steps: async (form) => {
          await form.expectSelected("Device", PDX01_DEVICE.name);
          await expect(form.page.getByLabel("Interface", { exact: true })).toHaveValue("Ethernet1/1");
        },
      },
    ],
  },
  {
    workflow: "SiteBackupWorkflow",
    inputModel: "SiteBackupInput",
    endpoint: "/v1/workflow/ngc/site_backup",
    scenarios: [
      ...siteScenarios,
      {
        name: "backup-enabled only off",
        steps: async (form) => {
          await form.select("Site", SITES_LIST.pdx01);
          await form.setChecked("Backup enabled only", false);
        },
      },
    ],
  },
  {
    workflow: "ValidateHardwareWorkflow",
    inputModel: "ValidateHardwareInput",
    endpoint: "/v1/workflow/ngc/cumulus_hardware_validation",
    scenarios: siteScenarios,
  },
  {
    workflow: "SpXOverlayCreationWorkflow",
    inputModel: "SpXOverlayCreationInput",
    endpoint: "/v1/workflow/ngc/spx_overlay_creation",
    scenarios: [
      {
        name: "manual fill, defaults",
        steps: async (form) => {
          await form.select("Site", SITES_LIST.pdx01);
          await form.fill("Overlay ID", "test-overlay");
          await form.select("Tenant", TENANT_LIST.ngc);
          await form.expectSelected("Namespace Tag", "spectrumx");
        },
      },
      {
        name: "manual fill, every field",
        steps: async (form) => {
          await form.select("Site", SITES_LIST.rno1);
          await form.fill("Overlay ID", "  test-overlay-2 ");
          await form.select("Tenant", TENANT_LIST.tenant_a);
          await form.select("Namespace Tag", "tenant-a");
          await form.fill("RD Min", "61000");
          await form.fill("RD Max", "62000");
        },
      },
      {
        name: "prefill from URL, namespace alias",
        query:
          `?site=${SITES_LIST.pdx01}&overlay_id=test-overlay-1&tenant=${TENANT_LIST.ngc}` +
          "&namespace=tenant-a&rd_min=61000&rd_max=62000",
        steps: async (form) => {
          await form.expectSelected("Tenant", TENANT_LIST.ngc);
          await form.expectSelected("Namespace Tag", "tenant-a");
        },
      },
    ],
  },
  {
    workflow: "SpXOverlayTenantChangeWorkflow",
    inputModel: "SpXOverlayTenantChangeInput",
    endpoint: "/v1/workflow/ngc/spx_overlay_tenant_change",
    scenarios: [
      {
        name: "manual fill",
        steps: async (form) => {
          await form.select("Site", SITES_LIST.pdx01);
          await form.select("Overlay ID (optional — leave blank to remove)", SPX_OVERLAY_LIST.primary);
          await form.select("Device", PDX01_DEVICE.name);
          await form.select("Ports", "swp1", "swp2");
        },
      },
      {
        name: "removal without an overlay",
        steps: async (form) => {
          await form.select("Site", SITES_LIST.pdx01);
          await form.select("Device", PDX01_DEVICE_2.name);
          await form.select("Ports", "swp3");
        },
      },
    ],
  },
];

for (const definition of PARITY_WORKFLOWS) {
  test.describe(`${definition.workflow} payload parity`, () => {
    // In order, in one worker: UPDATE_PARITY rewrites this workflow's fixture file.
    test.describe.configure({ mode: "default" });

    test.beforeEach(async ({ page }) => {
      await mockServerCatalogAndUser(page, ["reader", "executor"]);
      await mockTypedLocationsEndpoint(page);
    });

    if (!UPDATE_PARITY) {
      test("the fixture holds exactly these scenarios", () => {
        const fixture = readParityFixture(definition.workflow);
        expect(fixture?.workflow).toBe(definition.workflow);
        expect(fixture?.input_model).toBe(definition.inputModel);
        expect(fixture?.scenarios.map((scenario) => scenario.name)).toEqual(
          definition.scenarios.map((scenario) => scenario.name)
        );
      });
    }

    for (const scenario of definition.scenarios) {
      test(`legacy page: ${scenario.name}`, async ({ page }) => {
        test.skip(
          isMigrated(definition.workflow),
          "The legacy page redirects; its payload is the golden captured before migration."
        );
        const payload = await capturePayload(page, definition, scenario, "legacy");
        checkPayload(definition, scenario, "legacy", payload);
      });

      test(`generic route: ${scenario.name}`, async ({ page }) => {
        const payload = await capturePayload(page, definition, scenario, "generic");
        checkPayload(definition, scenario, "generic", payload);
      });
    }
  });
}
