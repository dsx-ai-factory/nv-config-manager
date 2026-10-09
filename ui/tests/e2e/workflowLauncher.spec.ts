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
import { expect, type Locator, type Page } from "@playwright/test";
import workflowFormIds from "@/config/workflow-form-ids.json";
import { mockWorkflowMetadataEndpoint } from "./shared/apiMocks";
import { test } from "./shared/utils";

const API_UPGRADE_REQUIRED =
  "Upgrade the Config Manager workflow API before deploying this UI version. Browser workflow forms require the backend form metadata and endpoints.";

type LauncherEntry = {
  title: string;
  /** Link target, or `null` when the entry renders disabled. */
  href: string | null;
};

const workflowForm = (name: keyof typeof workflowFormIds) =>
  `/workflows/new/${workflowFormIds[name]}`;
const launcherCollator = new Intl.Collator("en", { numeric: true });

/**
 * The launcher as it rendered from the hard-coded `siteConfig.workflows` list, against
 * the shared Playwright mocks. That `/metadata` mock omits Configuration Diff and the
 * InfiniBand PKey workflows, so those stay disabled ("Workflow metadata is
 * unavailable."), and Multi-Configuration Deploy needs a role the mocked user lacks.
 * Every workflow form is on its form ID route (the legacy form routes redirect).
 */
const EXPECTED_LAUNCHER: LauncherEntry[] = [
  { title: "Configuration Backup", href: workflowForm("BackupWorkflow") },
  {
    title: "Site Configuration Backup",
    href: workflowForm("SiteBackupWorkflow"),
  },
  {
    title: "Connected Host Metadata",
    href: workflowForm("ConnectedHostMetadataWorkflow"),
  },
  { title: "Configuration Deploy", href: workflowForm("DeployWorkflow") },
  { title: "Configuration Diff", href: null },
  { title: "Multi-Configuration Deploy", href: null },
  {
    title: "Device Cable Validation",
    href: workflowForm("DeviceCableValidationWorkflow"),
  },
  {
    title: "Site Cable Validation",
    href: workflowForm("SiteCableValidationWorkflow"),
  },
  { title: "Port LLDP Info", href: workflowForm("PortLLDPInfoWorkflow") },
  {
    title: "SpX Overlay Creation",
    href: workflowForm("SpXOverlayCreationWorkflow"),
  },
  {
    title: "SpX Overlay Deletion",
    href: workflowForm("SpXOverlayDeletionWorkflow"),
  },
  {
    title: "SpX Overlay Tenant Change",
    href: workflowForm("SpXOverlayTenantChangeWorkflow"),
  },
  {
    title: "InfiniBand Get Unhealthy Ports",
    href: workflowForm("InfinibandGetUnhealthyPortsWorkflow"),
  },
  {
    title: "InfiniBand Cable Validation",
    href: workflowForm("InfinibandCableValidationWorkflow"),
  },
  {
    title: "InfiniBand MLNX-OS Upgrade",
    href: workflowForm("InfinibandMlnxOSUpgradeWorkflow"),
  },
  { title: "Reprovision", href: workflowForm("ReprovisionWorkflow") },
  { title: "Switch OS Upgrade", href: workflowForm("SwitchOSUpgradeWorkflow") },
  {
    title: "Cumulus Hardware Validation",
    href: workflowForm("ValidateHardwareWorkflow"),
  },
  {
    title: "Device Password Rotation",
    href: workflowForm("DevicePasswordRotationWorkflow"),
  },
  {
    title: "Site Password Rotation",
    href: workflowForm("SitePasswordRotationWorkflow"),
  },
  { title: "Device Diagnostics", href: workflowForm("DiagnosticsWorkflow") },
  {
    title: "InfiniBand Port GUID Discovery",
    href: workflowForm("IBPortGuidDiscoveryWorkflow"),
  },
  { title: "InfiniBand PKey Creation", href: null },
  { title: "InfiniBand PKey Member Add", href: null },
  { title: "InfiniBand PKey Member Update", href: null },
  { title: "InfiniBand PKey Member Delete", href: null },
].sort((a, b) => launcherCollator.compare(a.title, b.title));

const openLauncher = async (page: Page) => {
  await page.getByRole("button", { name: "New workflow" }).click();
  return page.getByRole("dialog");
};

const readLauncher = (launcher: Locator) =>
  launcher.locator("a, button").evaluateAll((elements) =>
    elements.map((element) => ({
      title: element.textContent?.trim() ?? "",
      href:
        element.getAttribute("aria-disabled") === "true"
          ? null
          : element.getAttribute("href"),
    }))
  );

test.describe("New workflow launcher", () => {
  test("lists the same workflows, order, states, and links as before", async ({
    page,
  }) => {
    await page.goto("/workflows");
    const launcher = await openLauncher(page);

    await expect(launcher.getByRole("link")).toHaveCount(
      EXPECTED_LAUNCHER.filter((entry) => entry.href !== null).length
    );
    await expect(launcher.locator("a, button")).toHaveText(
      EXPECTED_LAUNCHER.map((entry) => entry.title)
    );
    expect(await readLauncher(launcher)).toEqual(EXPECTED_LAUNCHER);
    // One flat list: no section heading while the catalog reports no groups.
    await expect(launcher.getByText("Other", { exact: true })).toHaveCount(0);

    // Registered workflows that were never in the launcher stay out of it.
    for (const hidden of [
      "HelloWorld",
      "HelloWorldApproval",
      "Redfish Provisioning",
      "RedfishProvisioningWorkflow",
      "SpX Overlay Assignment",
    ]) {
      await expect(launcher.getByText(hidden, { exact: true })).toHaveCount(0);
    }

    const configDiff = launcher.getByRole("button", {
      name: "Configuration Diff",
      exact: true,
    });
    await configDiff.hover();
    await expect(configDiff).toHaveAccessibleDescription(
      "Workflow metadata is unavailable."
    );
  });

  test("lists a plugin workflow from the catalog on its form ID route", async ({
    page,
  }) => {
    const pluginWorkflow = {
      name: "AcmeFabricAuditWorkflow",
      display_name: "Acme Fabric Audit",
      description: "Audit the fabric.",
      endpoint: "/acme/fabric_audit",
      namespace: "acme",
      cli_name: "acme_fabric_audit",
      input_class: "AcmeFabricAuditInput",
      read_roles: ["all"],
      execute_roles: ["all"],
      has_form: true,
      form_id: "acme-fabric-audit",
    };
    await mockWorkflowMetadataEndpoint(page, [pluginWorkflow]);

    await page.goto("/workflows");
    const launcher = await openLauncher(page);

    const expected = [
      ...EXPECTED_LAUNCHER,
      {
        title: "Acme Fabric Audit",
        href: "/workflows/new/acme-fabric-audit",
      },
    ].sort((a, b) => launcherCollator.compare(a.title, b.title));
    await expect(launcher.locator("a, button")).toHaveText(
      expected.map((entry) => entry.title)
    );
    await expect(
      launcher.getByRole("link", { name: "Acme Fabric Audit" })
    ).toBeVisible();
    expect(await readLauncher(launcher)).toEqual(expected);
  });

  test("keeps the built-in list, disabled, when the catalog cannot load", async ({
    page,
  }) => {
    await page.unroute("**/v1/workflow/metadata");
    await page.route("**/v1/workflow/metadata", (route) =>
      route.fulfill({ status: 500, json: { detail: "unavailable" } })
    );

    await page.goto("/");
    const launcher = await openLauncher(page);

    await expect(launcher.locator("a, button")).toHaveText(
      EXPECTED_LAUNCHER.map((entry) => entry.title)
    );
    await expect(launcher.getByRole("link")).toHaveCount(0);

    // The popover focuses the first entry, which opens its tooltip too, so check the
    // hovered entry's own description rather than "the" tooltip.
    const deploy = launcher.getByRole("button", {
      name: "Configuration Deploy",
      exact: true,
    });
    await deploy.hover();
    await expect(deploy).toHaveAccessibleDescription(
      "Workflow metadata is unavailable."
    );
  });

  test("disables links with an API upgrade message when metadata omits has_form", async ({
    page,
  }) => {
    await page.route("**/v1/workflow/metadata", (route) =>
      route.fulfill({
        status: 200,
        json: {
          workflows: [
            {
              name: "DeployWorkflow",
              display_name: "Configuration Deploy",
              description: "Deploy configuration.",
              endpoint: "/ngc/deploy",
              namespace: "ngc",
              cli_name: "deploy",
              input_class: "DeployInput",
              read_roles: ["all"],
              execute_roles: ["all"],
            },
          ],
        },
      })
    );

    await page.goto("/workflows");
    const launcher = await openLauncher(page);
    const deploy = launcher.getByRole("button", {
      name: "Configuration Deploy",
      exact: true,
    });

    await expect(
      launcher.getByRole("link", { name: "Configuration Deploy" })
    ).toHaveCount(0);
    await deploy.hover();
    await expect(deploy).toHaveAccessibleDescription(API_UPGRADE_REQUIRED);
  });
});
