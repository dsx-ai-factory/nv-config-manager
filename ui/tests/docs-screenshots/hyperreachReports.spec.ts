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

import { expect, test } from "@playwright/test";

import type { Workflow } from "@/types/data-table.types";
import reports from "./fixtures/hyperreachReports.json";

const SCREENSHOT_DIR = path.resolve(__dirname, "../../../docs/assets/images");
const STAGE_DESCRIPTION = "Generate Cable Validation report";
const examples = [
  {
    id: "docs-hyperreach-cable-validation",
    workflowType: "SiteCableValidationWorkflow",
    stageName: "format_result",
    description: STAGE_DESCRIPTION,
    display: reports.cableValidation,
    image: "cable-validation-report-example.png",
    observedDescription: "su02-hyperreach-host A56p0",
  },
  {
    id: "docs-hyperreach-connected-host",
    workflowType: "ConnectedHostMetadataWorkflow",
    stageName: "get_connected_host_data",
    description: "Get connected host data",
    display: reports.connectedHostMetadata,
    image: "workflows/connected-host-metadata-result.png",
    observedDescription: "su02-hyperreach-host C1-2-L1",
  },
];

test.use({
  colorScheme: "light",
  locale: "en-US",
  timezoneId: "America/Los_Angeles",
  serviceWorkers: "block",
  viewport: { width: 2400, height: 1000 },
});

test.beforeEach(async ({ page, baseURL }) => {
  await page.addInitScript(() => {
    window.BYPASS_MSW = true;
  });
  await page.route("**/api/config", async (route) => {
    await route.fulfill({
      json: {
        configStoreApiUrl: baseURL,
        dhcpUrl: baseURL,
        dcimUrl: "https://nautobot.nvcm.air",
        dcimProvider: "nautobot",
        dcimDisplayName: "Nautobot",
        renderServiceUrl: baseURL,
        workflowApiUrl: baseURL,
        ztpUrl: baseURL,
      },
    });
  });
  await page.route("**/healthcheck", (route) =>
    route.fulfill({ json: { status: "ok" } })
  );
  await page.route("**/whoami", (route) =>
    route.fulfill({ json: { user: "demo", roles: ["all"] } })
  );
});

for (const example of examples) {
  test(`captures ${example.workflowType} interface metadata`, async ({ page }) => {
    const workflow: Workflow = {
      id: example.id,
      workflow_type: example.workflowType,
      workflow_input: {},
      started_by: "demo",
      start_time: "2026-10-21T20:00:00Z",
      close_time: "2026-10-21T20:00:05Z",
      status: "COMPLETED",
      pending_approval: false,
      result: null,
      search_attributes: { Site: ["SPO01"], User: ["demo"] },
      href: `/workflows/${example.id}`,
      stages: [
        {
          name: example.stageName,
          description: example.description,
          requires_approval: false,
          state: "COMPLETE",
          output: { display: example.display },
          depends_on: [],
          approvers: [],
          rejecters: [],
          approval_threshold: 0,
          state_history: [{ state: "COMPLETE", time: "2026-10-21T20:00:05Z" }],
          retryable: false,
          retry_count: 0,
          traceback: null,
          execution_time: null,
        },
      ],
    };
    await page.route(`**/v1/workflow/${example.id}`, (route) =>
      route.fulfill({ json: workflow })
    );
    await page.goto(`/workflows/${example.id}`);
    const output = page
      .getByRole("button", { name: "Output", exact: true })
      .locator("xpath=../..");
    await expect(
      output.getByText("Intended Interface Label", { exact: true })
    ).toBeVisible();
    await expect(
      output.getByText("LLDP Port Description", { exact: true })
    ).toBeVisible();
    const row = output
      .getByRole("row")
      .filter({ hasText: example.observedDescription });
    await expect(
      row.getByRole("cell", { name: "C1-2-L1", exact: true })
    ).toBeVisible();
    await expect(
      row.getByRole("cell", { name: "A56p0", exact: true })
    ).toBeVisible();
    await expect(
      row.getByRole("cell", {
        name: example.observedDescription,
        exact: true,
      })
    ).toBeVisible();
    if (example.workflowType === "SiteCableValidationWorkflow") {
      await expect(
        row.getByRole("cell", {
          name: "Incorrect cabling, actual should match intended. Based on LLDP data",
          exact: true,
        })
      ).toBeVisible();
      await expect(
        row.getByRole("cell", { name: "eth1", exact: true })
      ).toBeVisible();
      await expect(
        output.getByRole("link", { name: "Download Excel" })
      ).toBeVisible();
    }
    await page.addStyleTag({
      content: "nextjs-portal { display: none !important; }",
    });
    await page.evaluate(() => document.fonts.ready);
    await fs.mkdir(path.dirname(path.join(SCREENSHOT_DIR, example.image)), {
      recursive: true,
    });
    await output.screenshot({ path: path.join(SCREENSHOT_DIR, example.image) });
    if (example.workflowType === "SiteCableValidationWorkflow") {
      await page
        .getByRole("cell")
        .filter({
          has: page.getByRole("button", {
            name: `Select ${STAGE_DESCRIPTION}`,
          }),
        })
        .screenshot({
          path: path.join(SCREENSHOT_DIR, "cable-validation-report-link.png"),
        });
    }
  });
}
