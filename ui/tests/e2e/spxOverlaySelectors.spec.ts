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
import { expect } from "@playwright/test";
import { DEVICES_LIST, SITES_LIST, SPX_OVERLAY_LIST } from "@/mocks/data";
import { test, TEST_TIMEOUT } from "./shared/utils";

test("tenant change selects from the site's Spectrum-X overlays", async ({
  page,
}) => {
  const overlaysRequest = page.waitForRequest((request) =>
    request.url().includes("/v1/parameter/overlay")
  );

  await page.goto(
    "/workflows/spxoverlaytenantchangeworkflow/form" +
      `?site=${SITES_LIST.pdx01}` +
      `&overlay_id=${SPX_OVERLAY_LIST.primary}`
  );

  const request = await overlaysRequest;
  expect(new URL(request.url()).searchParams.get("location")).toBe(
    SITES_LIST.pdx01
  );
  expect(new URL(request.url()).searchParams.get("isolation_type")).toBe(
    "spectrum_x_vrf"
  );
  await expect(
    page.getByRole("button", {
      name: `${SPX_OVERLAY_LIST.primary}. Open options`,
      exact: true,
    })
  ).toBeVisible({ timeout: TEST_TIMEOUT });

  await page
    .getByRole("button", {
      name: `${SPX_OVERLAY_LIST.primary}. Open options`,
      exact: true,
    })
    .click();
  await expect(
    page.getByRole("dialog").getByText(SPX_OVERLAY_LIST.secondary)
  ).toBeVisible();
});

test("tenant change submits removals without a replacement overlay", async ({
  page,
}) => {
  const device = DEVICES_LIST.PDX01[0];
  await page.route(
    "**/v1/workflow/ngc/spx_overlay_tenant_change",
    async (route) => {
      await route.fulfill({
        status: 201,
        json: { id: "spx-removal-workflow" },
      });
    }
  );
  await page.goto(
    "/workflows/spxoverlaytenantchangeworkflow/form" +
      `?site=${SITES_LIST.pdx01}` +
      `&device-id=${device.id}` +
      "&port_names=swp1%2Cswp2"
  );

  await expect(
    page.getByText("Overlay ID (optional — leave blank to remove)", {
      exact: true,
    })
  ).toBeVisible({ timeout: TEST_TIMEOUT });
  await expect(
    page.getByRole("button", { name: device.name })
  ).toBeVisible({ timeout: TEST_TIMEOUT });
  await expect(page.getByRole("button", { name: "Submit" })).toBeEnabled();

  const requestPromise = page.waitForRequest((request) =>
    request.url().includes("/v1/workflow/ngc/spx_overlay_tenant_change")
  );
  await page.getByRole("button", { name: "Submit" }).click();
  const request = await requestPromise;

  expect(JSON.parse((await request.postData()) || "{}")).toEqual({
    site: SITES_LIST.pdx01,
    overlay_id: null,
    device_id: device.id,
    port_names: ["swp1", "swp2"],
  });
});

test("tenant change rejects delimiter-only port names", async ({ page }) => {
  const device = DEVICES_LIST.PDX01[0];
  await page.goto(
    "/workflows/spxoverlaytenantchangeworkflow/form" +
      `?site=${SITES_LIST.pdx01}` +
      `&device-id=${device.id}` +
      "&port_names=%2C"
  );

  await expect(
    page.getByRole("button", { name: device.name })
  ).toBeVisible({ timeout: TEST_TIMEOUT });
  await expect(page.getByRole("button", { name: "Submit" })).toBeDisabled();
});
