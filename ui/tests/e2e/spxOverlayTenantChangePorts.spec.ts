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
 * Both repeated and legacy comma-separated `?port_names=` links are accepted. Submit
 * is enabled while the form is incomplete and reports "<label> is required"; a ports
 * load failure shows under the Ports picker.
 */
import { expect, type Page } from "@playwright/test";

import { DEVICES_LIST, SITES_LIST, SPX_OVERLAY_LIST } from "@/mocks/data";

import {
  mockServerCatalogAndUser,
  mockTypedLocationsEndpoint,
} from "./shared/apiMocks";
import { test, TEST_TIMEOUT } from "./shared/utils";
import {
  choose,
  expectWorkflowDetails,
  formErrors,
  formPath,
  nextPost,
  picker,
  recordPosts,
  SELECT_DEVICE,
  SELECT_SITE,
  selected,
  submit,
} from "./shared/workflowFormTests";

const PATH = formPath("SpXOverlayTenantChangeWorkflow");
const TITLE = "New SpX Overlay Tenant Change Workflow";
const ENDPOINT = "/v1/workflow/ngc/spx_overlay_tenant_change";
const SELECT_OVERLAY =
  "Select a Overlay ID (optional — leave blank to remove)...";
const SELECT_PORTS = "Select Ports...";
const [DEVICE, SECOND_DEVICE] = DEVICES_LIST.PDX01;

const pickPorts = async (page: Page, ...ports: string[]) => {
  await picker(page, SELECT_PORTS).click();
  for (const port of ports) {
    await page
      .getByRole("dialog")
      .getByRole("option", { name: port, exact: true })
      .click();
  }
  await page.keyboard.press("Escape");
};

test.beforeEach(async ({ page }) => {
  await mockServerCatalogAndUser(page, ["reader", "executor"]);
  await mockTypedLocationsEndpoint(page);
});

test.describe("SpX Overlay Tenant Change Form", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(PATH);
    await expect(page.getByRole("heading", { name: TITLE })).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
  });

  test("renders one Site, used by the overlay and the device", async ({
    page,
  }) => {
    await expect(page.locator("form label")).toHaveText([
      "Site *",
      "Overlay ID (optional — leave blank to remove)",
      "Device *",
      "Ports *",
    ]);
  });

  test("reports the missing inputs and sends nothing", async ({ page }) => {
    const posts = recordPosts(page, ENDPOINT);
    await submit(page);
    for (const label of ["Site", "Device", "Ports"]) {
      await expect(
        page.getByText(`${label} is required`, { exact: true })
      ).toBeVisible();
    }
    expect(posts).toEqual([]);
  });

  test("queries the device's ports, submits several, and clears them on a device change", async ({
    page,
  }) => {
    await choose(page, SELECT_SITE, SITES_LIST.pdx01);
    await choose(page, SELECT_OVERLAY, SPX_OVERLAY_LIST.primary);
    const ports = page.waitForRequest((request) =>
      request.url().includes(`/v1/parameter/device/${DEVICE.id}/interfaces`)
    );
    await choose(page, SELECT_DEVICE, DEVICE.name);
    await ports;
    await pickPorts(page, "swp1", "swp2");
    await expect(selected(page, "swp1, swp2")).toBeVisible();

    // Another device: its ports load and the selection is cleared.
    const secondPorts = page.waitForRequest((request) =>
      request
        .url()
        .includes(`/v1/parameter/device/${SECOND_DEVICE.id}/interfaces`)
    );
    await selected(page, DEVICE.name).click();
    await page
      .getByRole("dialog")
      .getByRole("option", { name: SECOND_DEVICE.name, exact: true })
      .click();
    await secondPorts;
    await expect(picker(page, SELECT_PORTS)).toBeEnabled();
    await pickPorts(page, "swp3");

    const post = nextPost(page, ENDPOINT);
    await submit(page);
    expect((await post).postDataJSON()).toEqual({
      site: SITES_LIST.pdx01,
      site_type: "Site",
      overlay_id: SPX_OVERLAY_LIST.primary,
      device_id: SECOND_DEVICE.id,
      port_names: ["swp3"],
    });
    await expectWorkflowDetails(page);
  });

  test("submits without an overlay to remove the tenant", async ({ page }) => {
    await choose(page, SELECT_SITE, SITES_LIST.pdx01);
    await choose(page, SELECT_DEVICE, DEVICE.name);
    await pickPorts(page, "swp4");
    const post = nextPost(page, ENDPOINT);
    await submit(page);
    expect((await post).postDataJSON()).toEqual({
      site: SITES_LIST.pdx01,
      site_type: "Site",
      device_id: DEVICE.id,
      port_names: ["swp4"],
    });
  });

  test("shows a ports load failure and requires ports", async ({ page }) => {
    await page.route("**/v1/parameter/device/*/interfaces", (route) =>
      route.fulfill({
        status: 500,
        json: { error: "Failed to load device interfaces" },
      })
    );
    await choose(page, SELECT_SITE, SITES_LIST.pdx01);
    await choose(page, SELECT_DEVICE, DEVICE.name);
    await expect(page.getByText("Could not load Ports options.")).toBeVisible({
      timeout: TEST_TIMEOUT,
    });

    const posts = recordPosts(page, ENDPOINT);
    await submit(page);
    await expect(
      page.getByText("Ports is required", { exact: true })
    ).toBeVisible();
    expect(posts).toEqual([]);
  });

  test("shows a 422 on a port item and a string detail form-level", async ({
    page,
  }) => {
    let detail: unknown = [
      {
        type: "value_error",
        loc: ["body", "port_names", 0],
        msg: "Value error, port is a fabric port",
      },
    ];
    await page.route(`**${ENDPOINT}`, (route) =>
      route.fulfill({ status: 422, json: { detail } })
    );
    await choose(page, SELECT_SITE, SITES_LIST.pdx01);
    await choose(page, SELECT_DEVICE, DEVICE.name);
    await pickPorts(page, "swp1");
    await submit(page);
    await expect(
      page.getByText("Item 1: Value error, port is a fabric port")
    ).toBeVisible();

    detail = "Overlay test-overlay-1 is not on this device";
    await submit(page);
    await expect(formErrors(page)).toContainText(
      "Overlay test-overlay-1 is not on this device"
    );
  });
});

test.describe("SpX Overlay Tenant Change Form - URL prefill", () => {
  test("?site=&device-id= and repeated ?port_names= prefill the form through the location field", async ({
    page,
  }) => {
    const query =
      `?site=${SITES_LIST.pdx01}&overlay_id=${SPX_OVERLAY_LIST.primary}` +
      `&device-id=${DEVICE.id}&port_names=swp1&port_names=swp2`;
    await page.goto(`/workflows/spxoverlaytenantchangeworkflow/form${query}`);
    await expect(page).toHaveURL(`${PATH}${query}`);

    await expect(selected(page, SITES_LIST.pdx01)).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
    await expect(selected(page, SPX_OVERLAY_LIST.primary)).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
    await expect(selected(page, DEVICE.name)).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
    await expect(selected(page, "swp1, swp2")).toBeVisible({
      timeout: TEST_TIMEOUT,
    });

    const post = nextPost(page, ENDPOINT);
    await submit(page);
    expect((await post).postDataJSON()).toEqual({
      site: SITES_LIST.pdx01,
      site_type: "Site",
      overlay_id: SPX_OVERLAY_LIST.primary,
      device_id: DEVICE.id,
      port_names: ["swp1", "swp2"],
    });
  });

  test("drops URL ports that are not on the device", async ({ page }) => {
    await page.goto(
      `${PATH}?site=${SITES_LIST.pdx01}&device-id=${DEVICE.id}&port_names=not-a-device-port`
    );
    await expect(selected(page, DEVICE.name)).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
    await expect(picker(page, SELECT_PORTS)).toBeEnabled({
      timeout: TEST_TIMEOUT,
    });
    await expect(page.getByText("not-a-device-port")).toHaveCount(0);
  });

  test("a comma-separated ?port_names= prefills the ports", async ({
    page,
  }) => {
    await page.goto(
      `${PATH}?site=${SITES_LIST.pdx01}&device-id=${DEVICE.id}&port_names=swp1%2Cswp2`
    );
    await expect(selected(page, DEVICE.name)).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
    await expect(selected(page, "swp1, swp2")).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
  });
});
