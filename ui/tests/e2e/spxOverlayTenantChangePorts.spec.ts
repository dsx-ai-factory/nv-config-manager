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

test("queries the selected device ports and supports multiple selections", async ({
  page,
}) => {
  await page.goto("/workflows/spxoverlaytenantchangeworkflow/form");

  await page.getByRole("button", { name: "Site" }).click();
  await page.getByRole("dialog").getByText(SITES_LIST.pdx01).click();
  await page.getByRole("button", { name: "Overlay ID" }).click();
  await page
    .getByRole("dialog")
    .getByText(SPX_OVERLAY_LIST.primary)
    .click();

  const firstDevice = DEVICES_LIST.PDX01[0];
  const interfaceResponse = page.waitForResponse((response) =>
    response.url().includes(`/v1/parameter/device/${firstDevice.id}/interfaces`)
  );
  await page.getByRole("button", { name: "Device" }).click();
  await page.getByRole("dialog").getByText(firstDevice.name).click();
  await interfaceResponse;

  await page.getByRole("button", { name: "Ports" }).click();
  await page.getByRole("dialog").getByText("swp1").click();
  await page.getByRole("dialog").getByText("swp2").click();

  await expect(
    page.getByRole("button", { name: "Remove swp1" })
  ).toBeVisible({ timeout: TEST_TIMEOUT });
  await expect(
    page.getByRole("button", { name: "Remove swp2" })
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Submit" })).toBeEnabled();

  await page.getByRole("heading", {
    name: "New SpX Overlay Tenant Change Workflow",
  }).click();
  const secondDevice = DEVICES_LIST.PDX01[1];
  const secondInterfaceResponse = page.waitForResponse((response) =>
    response.url().includes(`/v1/parameter/device/${secondDevice.id}/interfaces`)
  );
  await page.getByRole("button", { name: firstDevice.name }).click();
  await page.getByRole("dialog").getByText(secondDevice.name).click();
  await secondInterfaceResponse;

  await expect(page.getByRole("button", { name: "Submit" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Remove swp1" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Remove swp2" })).toHaveCount(0);
});

test("loads device and port selections from URL parameters", async ({ page }) => {
  const device = DEVICES_LIST.PDX01[0];
  await page.goto(
    "/workflows/spxoverlaytenantchangeworkflow/form" +
      `?site=${SITES_LIST.pdx01}` +
      `&overlay_id=${SPX_OVERLAY_LIST.primary}` +
      `&device-id=${device.id}` +
      "&port_names=swp1%2Cswp2"
  );

  await expect(
    page.getByRole("button", { name: device.name })
  ).toBeVisible({ timeout: TEST_TIMEOUT });
  await expect(
    page.getByRole("button", { name: /swp1.*swp2/ })
  ).toBeVisible({ timeout: TEST_TIMEOUT });
  await expect(page.getByRole("button", { name: "Submit" })).toBeEnabled();
});

test("shows an error when device interfaces fail to load", async ({ page }) => {
  await page.route("**/v1/parameter/device/*/interfaces", async (route) => {
    await route.fulfill({
      status: 500,
      json: { error: "Failed to load device interfaces" },
    });
  });
  await page.goto("/workflows/spxoverlaytenantchangeworkflow/form");

  await page.getByRole("button", { name: "Site" }).click();
  await page.getByRole("dialog").getByText(SITES_LIST.pdx01).click();

  const device = DEVICES_LIST.PDX01[0];
  const interfaceResponse = page.waitForResponse((response) =>
    response.url().includes(`/v1/parameter/device/${device.id}/interfaces`)
  );
  await page.getByRole("button", { name: "Device" }).click();
  await page.getByRole("dialog").getByText(device.name).click();
  await interfaceResponse;

  await expect(
    page.getByRole("alert").filter({
      hasText: "Unable to load interfaces for the selected device. Try again.",
    })
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Submit" })).toBeDisabled();
});

test("rejects URL port selections that are not on the device", async ({ page }) => {
  const device = DEVICES_LIST.PDX01[0];
  const interfaceResponse = page.waitForResponse((response) =>
    response.url().includes(`/v1/parameter/device/${device.id}/interfaces`)
  );
  await page.goto(
    "/workflows/spxoverlaytenantchangeworkflow/form" +
      `?site=${SITES_LIST.pdx01}` +
      `&device-id=${device.id}` +
      "&port_names=not-a-device-port"
  );
  await interfaceResponse;

  await expect(
    page.getByRole("button", { name: device.name })
  ).toBeVisible({ timeout: TEST_TIMEOUT });
  await expect(page.getByRole("button", { name: "Submit" })).toBeDisabled();
  await expect(page.getByText("not-a-device-port")).toHaveCount(0);
});

test("rejects delimiter-only URL port selections", async ({ page }) => {
  const device = DEVICES_LIST.PDX01[0];
  const interfaceResponse = page.waitForResponse((response) =>
    response.url().includes(`/v1/parameter/device/${device.id}/interfaces`)
  );
  await page.goto(
    "/workflows/spxoverlaytenantchangeworkflow/form" +
      `?site=${SITES_LIST.pdx01}` +
      `&overlay_id=${SPX_OVERLAY_LIST.primary}` +
      `&device-id=${device.id}` +
      "&port_names=%2C"
  );
  await interfaceResponse;

  await expect(
    page.getByRole("button", { name: device.name })
  ).toBeVisible({ timeout: TEST_TIMEOUT });
  await expect(page.getByRole("button", { name: "Submit" })).toBeDisabled();
});
