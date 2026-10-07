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
 * Option loading of the SpX Overlay Tenant Change selectors on the class-name route:
 * the location type of a linked Site reaches the overlay request (a Site and a Module
 * may share an ID), and the overlay list is the Site's Spectrum-X overlays. Submission
 * scenarios live in `spxOverlayTenantChangePorts.spec.ts`.
 */
import { expect } from "@playwright/test";

import { SITES_LIST, SPX_OVERLAY_LIST } from "@/mocks/data";

import { mockServerCatalogAndUser } from "./shared/apiMocks";
import { test, TEST_TIMEOUT } from "./shared/utils";
import { formPath, selected } from "./shared/workflowFormTests";

const PATH = formPath("SpXOverlayTenantChangeWorkflow");

test.beforeEach(async ({ page }) => {
  await mockServerCatalogAndUser(page, ["reader", "executor"]);
});

test("tenant change keeps the location type of a Site linked by name", async ({ page }) => {
  await page.route("**/v1/parameter/location*", (route) =>
    route.fulfill({
      status: 200,
      json: [
        { id: "42", name: "SJC01", location_type: "Site" },
        { id: "42", name: "Module 1", location_type: "Module" },
      ],
    })
  );
  const overlaysRequest = page.waitForRequest((request) =>
    request.url().includes("/v1/parameter/overlay")
  );

  await page.goto(`/workflows/spxoverlaytenantchangeworkflow/form?site=Module%201`);

  const searchParams = new URL((await overlaysRequest).url()).searchParams;
  expect(searchParams.get("location")).toBe("42");
  expect(searchParams.get("location_type")).toBe("Module");
  await expect(selected(page, "Module 1")).toBeVisible({ timeout: TEST_TIMEOUT });
});

test("tenant change selects from the site's Spectrum-X overlays", async ({ page }) => {
  const overlaysRequest = page.waitForRequest((request) =>
    request.url().includes("/v1/parameter/overlay")
  );

  await page.goto(`${PATH}?site=${SITES_LIST.pdx01}&overlay_id=${SPX_OVERLAY_LIST.primary}`);

  const searchParams = new URL((await overlaysRequest).url()).searchParams;
  expect(searchParams.get("location")).toBe(SITES_LIST.pdx01);
  expect(searchParams.get("isolation_type")).toBe("spectrum_x_vrf");
  await expect(selected(page, SPX_OVERLAY_LIST.primary)).toBeVisible({ timeout: TEST_TIMEOUT });

  await selected(page, SPX_OVERLAY_LIST.primary).click();
  await expect(
    page.getByRole("dialog").getByRole("option", { name: SPX_OVERLAY_LIST.secondary, exact: true })
  ).toBeVisible();
});
