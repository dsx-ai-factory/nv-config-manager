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
 * The Configuration Deploy form on its class-name route, rendered by the RJSF form
 * because the legacy `/workflows/deployworkflow/form` redirects there. Ported
 * from the legacy page's spec and its shared `runWorkflowFormTests` suite. Its wording
 * comes from the server's
 * `ui_schema` (`ui:title`, `ui:help`, `ui:globalOptions.hideSchemaDescriptions`).
 *
 * Differences from the legacy page, by design: Site is a device filter rather than a
 * model property, so an empty submission reports only "Device is required" (the device
 * picker says "Select a Site first"); a failed submission shows the generic
 * "Workflow Failed" toast with the server's message (plan section 17); required labels
 * carry a visible " *".
 */
import { expect, type Page } from "@playwright/test";
import {
  SITES_LIST,
  DEVICES_LIST,
  FORBIDDEN_SITE_ID,
  FORBIDDEN_DEVICE_IDS,
} from "@/mocks/data";
import { mockServerCatalogAndUser } from "./shared/apiMocks";
import { test, TEST_TIMEOUT, WORKFLOW_DETAILS_TIMEOUT } from "./shared/utils";

const FORM_PATH = "/workflows/new/DeployWorkflow";
const FORM_TITLE = "New Configuration Deploy Workflow";
const ENDPOINT = "/v1/workflow/ngc/deploy";

/** A SelectBox trigger by its exact accessible name: the selection, or the placeholder. */
const picker = (page: Page, name: string) =>
  page.getByRole("button", { name, exact: true });
const selected = (page: Page, label: string) => picker(page, `${label}. Open options`);

const SELECT_SITE = "Select a Site...";
const SELECT_DEVICE = "Select a Device...";
const SITE_FIRST = "Select a Site first";
const COMMIT_CONFIRM_HELP =
  "Rollback if device becomes unreachable after apply. Disable for changes " +
  "that are expected to interrupt connectivity.";

const choose = async (page: Page, trigger: string, option: string) => {
  await picker(page, trigger).click();
  await page.getByRole("dialog").getByText(option, { exact: true }).click();
};

test.beforeEach(async ({ page }) => {
  await mockServerCatalogAndUser(page, ["reader", "executor"]);
});

test.describe(`${FORM_TITLE} Form`, () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(FORM_PATH);
  });

  test(`renders ${FORM_TITLE} with correct title`, async ({ page }) => {
    await expect(page.getByRole("heading", { name: FORM_TITLE })).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
  });

  test("shows the legacy fields, in order, with commit-confirm on", async ({
    page,
  }) => {
    await expect(page.locator("form label")).toHaveText([
      "Site *",
      "Tenant (optional)",
      "Status (optional)",
      "Device *",
      "Use commit-confirm",
    ]);
    await expect(
      page.getByRole("checkbox", { name: "Use commit-confirm" })
    ).toBeChecked();
    // Like the legacy page: only the commit-confirm help, no schema descriptions.
    await expect(page.locator("form p")).toHaveText([COMMIT_CONFIRM_HELP]);
    // Until a site is chosen the device picker has no options (now also disabled).
    await expect(picker(page, SITE_FIRST)).toBeDisabled();
  });

  test(`displays validation errors for empty ${FORM_TITLE} submission`, async ({
    page,
  }) => {
    const posts: string[] = [];
    page.on("request", (request) => {
      if (request.method() === "POST" && request.url().includes(ENDPOINT)) {
        posts.push(request.url());
      }
    });
    await page.getByRole("button", { name: "Submit" }).click();
    // Site is a device filter, not an input of the workflow: only the device reports.
    await expect(page.getByText("Device is required")).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
    await expect(page.getByText("Site is required")).toHaveCount(0);

    // After the failed submit, validation is live: choosing the device clears it.
    await choose(page, SELECT_SITE, SITES_LIST.pdx01);
    await expect(page.getByText("Device is required")).toBeVisible();
    await choose(page, SELECT_DEVICE, DEVICES_LIST[SITES_LIST.pdx01][0].name);
    await expect(page.getByText("Device is required")).toHaveCount(0);
    expect(posts).toEqual([]);
  });

  test(`successfully submits ${FORM_TITLE} with valid data and verifies API request`, async ({
    page,
  }) => {
    const site = SITES_LIST.pdx01;
    const device = DEVICES_LIST[site][0].name;

    await choose(page, SELECT_SITE, site);
    await choose(page, SELECT_DEVICE, device);

    await page.getByRole("button", { name: "Submit" }).click();

    await page.waitForURL("**/workflows/**");
    await expect(
      page.getByRole("heading", { name: "Workflow Details" })
    ).toBeVisible({ timeout: WORKFLOW_DETAILS_TIMEOUT });
  });

  test(`${FORM_TITLE} device field updates when site changes`, async ({
    page,
  }) => {
    const initialSite = SITES_LIST.pdx01;
    const initialDevice = DEVICES_LIST[initialSite][0].name;
    const changedSite = SITES_LIST.rno1;

    await choose(page, SELECT_SITE, initialSite);
    await choose(page, SELECT_DEVICE, initialDevice);

    await selected(page, initialSite).click();
    await page.getByRole("dialog").getByText(changedSite, { exact: true }).click();

    await expect(picker(page, SELECT_DEVICE)).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
  });

  test(`${FORM_TITLE} clears device field when site is cleared`, async ({
    page,
  }) => {
    const initialSite = SITES_LIST.pdx01;
    const initialDevice = DEVICES_LIST[initialSite][0].name;

    await choose(page, SELECT_SITE, initialSite);
    await choose(page, SELECT_DEVICE, initialDevice);

    await expect(selected(page, initialSite)).toBeVisible();
    await expect(selected(page, initialDevice)).toBeVisible();

    // The site picker's clear button comes first.
    await page.getByRole("button", { name: "Clear selection" }).first().click();

    await expect(picker(page, SELECT_SITE)).toBeVisible();
    await expect(picker(page, SITE_FIRST)).toBeDisabled({
      timeout: TEST_TIMEOUT,
    });
  });

  test(`${FORM_TITLE} resets device field when switching between sites`, async ({
    page,
  }) => {
    const firstSite = SITES_LIST.pdx01;
    const firstDevice = DEVICES_LIST[firstSite][0].name;
    const secondSite = SITES_LIST.rno1;
    const secondDevice = DEVICES_LIST[secondSite][0].name;

    await choose(page, SELECT_SITE, firstSite);
    await choose(page, SELECT_DEVICE, firstDevice);
    await expect(selected(page, firstSite)).toBeVisible();
    await expect(selected(page, firstDevice)).toBeVisible();

    await selected(page, firstSite).click();
    await page.getByRole("dialog").getByText(secondSite, { exact: true }).click();
    await expect(picker(page, SELECT_DEVICE)).toBeVisible({
      timeout: TEST_TIMEOUT,
    });

    await choose(page, SELECT_DEVICE, secondDevice);
    await expect(selected(page, secondSite)).toBeVisible();
    await expect(selected(page, secondDevice)).toBeVisible();

    await selected(page, secondSite).click();
    await page.getByRole("dialog").getByText(firstSite, { exact: true }).click();
    await expect(picker(page, SELECT_DEVICE)).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
  });

  test(`${FORM_TITLE} handles URL parameters correctly and submits with those values`, async ({
    page,
  }) => {
    const site = SITES_LIST.pdx01;
    const { id: deviceId, name: deviceName } = DEVICES_LIST[site][0];

    await page.goto(`${FORM_PATH}?site=${site}&device-id=${deviceId}`);

    await expect(selected(page, site)).toBeVisible({ timeout: TEST_TIMEOUT });
    await expect(selected(page, deviceName)).toBeVisible({
      timeout: TEST_TIMEOUT,
    });

    await page.getByRole("button", { name: "Submit" }).click();

    await page.waitForURL("**/workflows/**");
    await expect(
      page.getByRole("heading", { name: "Workflow Details" })
    ).toBeVisible({ timeout: WORKFLOW_DETAILS_TIMEOUT });
  });

  test(`disables ${FORM_TITLE} during submission`, async ({ page }) => {
    const site = SITES_LIST.pdx01;
    const device = DEVICES_LIST[site][0].name;

    await choose(page, SELECT_SITE, site);
    await choose(page, SELECT_DEVICE, device);

    await page.getByRole("button", { name: "Submit" }).click();

    await expect(selected(page, site)).toBeDisabled();
    await expect(selected(page, device)).toBeDisabled();
    await expect(
      page.getByRole("button", { name: "Submitting..." })
    ).toBeDisabled();
  });
});

test.describe(`${FORM_TITLE} - Error Scenarios`, () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(FORM_PATH);
  });

  test("displays forbidden error notification when submitting with forbidden values", async ({
    page,
  }) => {
    const forbiddenDevice = DEVICES_LIST[FORBIDDEN_SITE_ID].find(
      (device) => device.id === FORBIDDEN_DEVICE_IDS.ARISTA
    )!;

    await choose(page, SELECT_SITE, FORBIDDEN_SITE_ID);
    await choose(page, SELECT_DEVICE, forbiddenDevice.name);

    await page.getByRole("button", { name: "Submit" }).click();

    // NOTE: While not ideal, firefox has a weird bug where the toast notification is not visible unless we force a viewport adjustment.
    const errorTitle = page.locator("div.text-sm.font-semibold", {
      hasText: "Workflow Failed",
    });
    const errorMessage = page.locator("div.text-sm.opacity-90", {
      hasText: "Forbidden: You do not have permission to run this workflow",
    });

    await expect(errorTitle).toHaveText("Workflow Failed", { timeout: TEST_TIMEOUT });
    // The generic toast: the server's message, without the legacy page's prefix.
    await expect(errorMessage).toHaveText(
      "Forbidden: You do not have permission to run this workflow",
      { timeout: TEST_TIMEOUT }
    );
    await expect(page.getByRole("button", { name: "Submit" })).toBeEnabled();
  });

  test("shows FastAPI 422 errors inline: on the field, unknown locations form-level", async ({
    page,
  }) => {
    const [first, second] = DEVICES_LIST[SITES_LIST.pdx01];
    await page.route(`**${ENDPOINT}`, (route) =>
      route.fulfill({
        status: 422,
        json: {
          detail: [
            {
              type: "value_error",
              loc: ["body", "device_id"],
              msg: "Value error, device is not managed",
              input: first.id,
            },
            { type: "missing", loc: ["body", "user"], msg: "Field required" },
          ],
        },
      })
    );

    await choose(page, SELECT_SITE, SITES_LIST.pdx01);
    await choose(page, SELECT_DEVICE, first.name);
    await page.getByRole("button", { name: "Submit" }).click();

    await expect(page.getByText("Value error, device is not managed")).toBeVisible();
    await expect(
      page.getByRole("alert").filter({ hasText: "The workflow input is invalid" })
    ).toContainText("user: Field required");
    // No toast for a mapped validation error.
    await expect(page.locator("div.text-sm.font-semibold", { hasText: "Workflow Failed" })).toHaveCount(0);

    // Changing the device clears its server error.
    await selected(page, first.name).click();
    await page.getByRole("dialog").getByText(second.name, { exact: true }).click();
    await expect(page.getByText("Value error, device is not managed")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Submit" })).toBeEnabled();
  });
});

test.describe("Deploy Config Form - Additional Tests", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(FORM_PATH);
  });

  test("submits correct data to the API", async ({ page }) => {
    const requestPromise = page.waitForRequest((request) =>
      request.url().includes(ENDPOINT)
    );

    await choose(page, SELECT_SITE, SITES_LIST.pdx01);
    await choose(page, SELECT_DEVICE, DEVICES_LIST[SITES_LIST.pdx01][0].name);

    await page.getByRole("button", { name: "Submit" }).click();

    const request = await requestPromise;
    const requestData = JSON.parse((await request.postData()) || "{}");

    expect(requestData).toEqual({
      device_id: DEVICES_LIST[SITES_LIST.pdx01][0].id,
      commit_confirm: true,
    });

    await expect(
      page.getByRole("heading", { name: "Workflow Details" })
    ).toBeVisible({ timeout: WORKFLOW_DETAILS_TIMEOUT });
  });

  test("loads form with URL parameters and performs manual changes", async ({
    page,
  }) => {
    const siteName = SITES_LIST.pdx01;
    const { id: deviceId, name: deviceName } = DEVICES_LIST[siteName][0];
    await page.goto(`${FORM_PATH}?site=${siteName}&device-id=${deviceId}`);

    await expect(selected(page, siteName)).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
    await expect(selected(page, deviceName)).toBeVisible({
      timeout: TEST_TIMEOUT,
    });

    const newSiteName = SITES_LIST.rno1;
    await selected(page, siteName).click();
    await page.getByRole("dialog").getByText(newSiteName, { exact: true }).click();

    const newDeviceName = DEVICES_LIST[newSiteName][0].name;
    await choose(page, SELECT_DEVICE, newDeviceName);

    await expect(selected(page, newSiteName)).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
    await expect(selected(page, newDeviceName)).toBeVisible({
      timeout: TEST_TIMEOUT,
    });

    const requestPromise = page.waitForRequest((request) =>
      request.url().includes(ENDPOINT)
    );
    await page.getByRole("button", { name: "Submit" }).click();
    const request = await requestPromise;
    const requestData = JSON.parse((await request.postData()) || "{}");

    // The manually changed values, not the URL parameter values.
    expect(requestData).toEqual({
      device_id: DEVICES_LIST[newSiteName][0].id,
      commit_confirm: true,
    });

    await expect(
      page.getByRole("heading", { name: "Workflow Details" })
    ).toBeVisible({ timeout: WORKFLOW_DETAILS_TIMEOUT });
  });

  test("submits form directly from URL parameters without changes", async ({
    page,
  }) => {
    const requestPromise = page.waitForRequest((request) =>
      request.url().includes(ENDPOINT)
    );

    const siteName = SITES_LIST.pdx01;
    const { id: deviceId, name: deviceName } = DEVICES_LIST[siteName][0];
    await page.goto(`${FORM_PATH}?site=${siteName}&device-id=${deviceId}`);

    await expect(selected(page, siteName)).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
    await expect(selected(page, deviceName)).toBeVisible({
      timeout: TEST_TIMEOUT,
    });

    await page.getByRole("button", { name: "Submit" }).click();

    const request = await requestPromise;
    const requestData = JSON.parse((await request.postData()) || "{}");

    expect(requestData).toEqual({
      device_id: deviceId,
      commit_confirm: true,
    });

    await expect(
      page.getByRole("heading", { name: "Workflow Details" })
    ).toBeVisible({ timeout: WORKFLOW_DETAILS_TIMEOUT });
  });
});
