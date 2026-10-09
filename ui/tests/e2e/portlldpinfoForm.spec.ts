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
 * The Port LLDP Info form on its class-name route (the legacy
 * `/workflows/portlldpinfoworkflow/form` redirects there).
 *
 * Device lookup requires Site, device, and interface. Device lookup and MAC lookup
 * remain mutually exclusive: using either mode disables the other, and incomplete
 * modes are blocked before submission while the API boundary remains authoritative.
 */
import { expect, type Page } from "@playwright/test";

import {
  DEVICES_LIST,
  FORBIDDEN_DEVICE_IDS,
  FORBIDDEN_SITE_ID,
  SITES_LIST,
} from "@/mocks/data";

import { mockServerCatalogAndUser } from "./shared/apiMocks";
import { test, TEST_TIMEOUT } from "./shared/utils";
import {
  choose,
  expectFailureToast,
  expectWorkflowDetails,
  formErrors,
  formPath,
  nextPost,
  noFailureToast,
  picker,
  SELECT_DEVICE,
  SELECT_SITE,
  SITE_FIRST,
  selected,
  submit,
} from "./shared/workflowFormTests";

const PATH = formPath("PortLLDPInfoWorkflow");
const TITLE = "New Port LLDP Info Workflow";
const ENDPOINT = "/v1/workflow/ngc/port_lldp_info";
const MAC = "00:11:22:33:44:55";
const INTERFACE = "Ethernet1/1";
const SITE = SITES_LIST.pdx01;
const [DEVICE] = DEVICES_LIST[SITE];
const OTHER = DEVICES_LIST[SITES_LIST.rno1][0];

const interfaceInput = (page: Page) => page.getByLabel("Interface");
const macInput = (page: Page) => page.getByLabel("MAC Address");

test.beforeEach(async ({ page }) => {
  await mockServerCatalogAndUser(page, ["reader", "executor"]);
});

test.describe("Port LLDP Info Form", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(PATH);
    await expect(page.getByRole("heading", { name: TITLE })).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
  });

  test("requires Site before choosing a device", async ({
    page,
  }) => {
    await expect(page.locator("form label")).toHaveText([
      "Site *",
      "Device",
      "Interface",
      "MAC Address",
    ]);
    await expect(
      page.getByText(
        "Select a device and interface, or enter a remote MAC address."
      )
    ).toBeVisible();
    await expect(
      page.getByText("Use this instead of the device and interface fields.")
    ).toBeVisible();
    await expect(picker(page, SITE_FIRST)).toBeDisabled({
      timeout: TEST_TIMEOUT,
    });
    await expect(interfaceInput(page)).toBeEnabled();
    await expect(macInput(page)).toBeEnabled();
    await choose(page, SELECT_SITE, SITE);
    await expect(picker(page, SELECT_DEVICE)).toBeEnabled({
      timeout: TEST_TIMEOUT,
    });
    await expect(macInput(page)).toBeDisabled();
  });

  test("submits a device and interface; the interface is trimmed", async ({
    page,
  }) => {
    await choose(page, SELECT_SITE, SITE);
    await choose(page, SELECT_DEVICE, DEVICE.name);
    await interfaceInput(page).fill(`  ${INTERFACE} `);

    const post = nextPost(page, ENDPOINT);
    await submit(page);
    expect((await post).postDataJSON()).toEqual({
      device_id: DEVICE.id,
      interface: INTERFACE,
    });
    await expectWorkflowDetails(page);
  });

  test("submits a MAC address alone", async ({ page }) => {
    await macInput(page).fill(MAC);
    const post = nextPost(page, ENDPOINT);
    await submit(page);
    expect((await post).postDataJSON()).toEqual({ remote_mac_address: MAC });
    await expectWorkflowDetails(page);
  });

  test("keeps device/interface and MAC entry mutually exclusive", async ({
    page,
  }) => {
    await macInput(page).fill(MAC);
    await expect(picker(page, SELECT_SITE)).toBeDisabled();
    await expect(picker(page, SITE_FIRST)).toBeDisabled();
    await expect(interfaceInput(page)).toBeDisabled();

    await macInput(page).fill("");
    await expect(picker(page, SELECT_SITE)).toBeEnabled();
    await expect(interfaceInput(page)).toBeEnabled();

    await interfaceInput(page).fill(INTERFACE);
    await expect(macInput(page)).toBeDisabled();
    await interfaceInput(page).fill("");
    await expect(macInput(page)).toBeEnabled();

    await choose(page, SELECT_SITE, SITE);
    await expect(macInput(page)).toBeDisabled();
  });

  test("a Site change clears the device", async ({ page }) => {
    await choose(page, SELECT_SITE, SITE);
    await choose(page, SELECT_DEVICE, DEVICE.name);
    await selected(page, SITE).click();
    await page
      .getByRole("dialog")
      .getByRole("option", { name: SITES_LIST.rno1, exact: true })
      .click();
    await expect(picker(page, SELECT_DEVICE)).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
    await choose(page, SELECT_DEVICE, OTHER.name);
    await expect(selected(page, OTHER.name)).toBeVisible();
  });

  test("blocks empty and incomplete modes before calling the API", async ({
    page,
  }) => {
    const posts: string[] = [];
    page.on("request", (request) => {
      if (request.method() === "POST" && request.url().endsWith(ENDPOINT)) {
        posts.push(request.url());
      }
    });

    await submit(page);
    await expect(formErrors(page)).toContainText(
      "Provide Site, Device, Interface or MAC Address"
    );
    expect(posts).toEqual([]);
    await noFailureToast(page);

    await interfaceInput(page).fill(INTERFACE);
    await submit(page);
    await expect(page.getByText("Device is required")).toBeVisible();
    await expect(page.getByText("Site is required for Device")).toBeVisible();
    expect(posts).toEqual([]);
  });

  test("shows a forbidden device in the failure toast", async ({ page }) => {
    const forbidden = DEVICES_LIST[FORBIDDEN_SITE_ID].find(
      (device) => device.id === FORBIDDEN_DEVICE_IDS.ARISTA
    )!;
    await choose(page, SELECT_SITE, FORBIDDEN_SITE_ID);
    await choose(page, SELECT_DEVICE, forbidden.name);
    await interfaceInput(page).fill(INTERFACE);
    await submit(page);
    await expectFailureToast(
      page,
      "Forbidden: You do not have permission to run this workflow"
    );
  });
});

test.describe("Port LLDP Info Form - URL prefill", () => {
  test("a legacy link prefills Site, Device, and Interface", async ({
    page,
  }) => {
    const query = `?site=${SITE}&device-id=${DEVICE.id}&interface=${INTERFACE}`;
    await page.goto(`/workflows/portlldpinfoworkflow/form${query}`);
    // The redirect keeps the query (re-encoding "/" as %2F).
    await expect(page).toHaveURL(
      (url) =>
        url.pathname === PATH && url.searchParams.get("interface") === INTERFACE
    );

    await expect(selected(page, SITE)).toBeVisible({ timeout: TEST_TIMEOUT });
    await expect(selected(page, DEVICE.name)).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
    await expect(interfaceInput(page)).toHaveValue(INTERFACE);

    const post = nextPost(page, ENDPOINT);
    await submit(page);
    expect((await post).postDataJSON()).toEqual({
      device_id: DEVICE.id,
      interface: INTERFACE,
    });
    await expectWorkflowDetails(page);
  });

  test("prefills a MAC address", async ({ page }) => {
    await page.goto(`${PATH}?remote_mac_address=${MAC}`);
    await expect(macInput(page)).toHaveValue(MAC);
    await expect(interfaceInput(page)).toBeDisabled();
    await expect(picker(page, SELECT_SITE)).toBeDisabled();
    const post = nextPost(page, ENDPOINT);
    await submit(page);
    expect((await post).postDataJSON()).toEqual({ remote_mac_address: MAC });
  });

  test("an unknown Site is dropped and a device absent from the Site is dropped", async ({
    page,
  }) => {
    await page.goto(`${PATH}?site=NOPE&device-id=${DEVICE.id}`);
    // A required unknown Site is dropped, so the device picker stays unavailable.
    await expect(picker(page, SELECT_SITE)).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
    await expect(picker(page, SITE_FIRST)).toBeDisabled({
      timeout: TEST_TIMEOUT,
    });

    await page.goto(`${PATH}?site=${SITES_LIST.rno1}&device-id=${DEVICE.id}`);
    await expect(selected(page, SITES_LIST.rno1)).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
    await expect(picker(page, SELECT_DEVICE)).toBeEnabled({
      timeout: TEST_TIMEOUT,
    });
    await expect(page.getByRole("button", { name: "Submit" })).toBeEnabled();
  });
});
