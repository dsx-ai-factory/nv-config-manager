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
 * The SpX Overlay Creation form on its class-name route (the legacy
 * `/workflows/spxoverlaycreationworkflow/form` redirects there).
 *
 * Differences from the legacy page, by design: the Site's location type is submitted
 * as `site_type`; a cleared RD bound is omitted (the server default applies) instead of
 * "Expected number, received nan"; RD bounds outside 0-65535 report Ajv's messages
 * (a form-only constraint); "RD Min must be less than RD Max" is the model validator's
 * job, so it arrives as an inline 422.
 */
import { expect, type Page } from "@playwright/test";

import { FORBIDDEN_SITE_ID, SITES_LIST, TENANT_LIST } from "@/mocks/data";

import { mockServerCatalogAndUser, mockTypedLocationsEndpoint } from "./shared/apiMocks";
import { test, TEST_TIMEOUT } from "./shared/utils";
import {
  choose,
  expectFailureToast,
  expectWorkflowDetails,
  formErrors,
  formPath,
  nextPost,
  noFailureToast,
  recordPosts,
  SELECT_SITE,
  selected,
  submit,
} from "./shared/workflowFormTests";

const PATH = formPath("SpXOverlayCreationWorkflow");
const TITLE = "New SpX Overlay Creation Workflow";
const ENDPOINT = "/v1/workflow/ngc/spx_overlay_creation";
const SELECT_TENANT = "Select a Tenant...";
const rdMin = (page: Page) => page.getByRole("spinbutton", { name: "RD Min" });
const rdMax = (page: Page) => page.getByRole("spinbutton", { name: "RD Max" });

const SEARCH_TENANTS = [
  { id: "engineering-cloud", name: "Engineering Cloud" },
  { id: "ngc-platform", name: "NGC Platform" },
  { id: "pre-ngc", name: "Pre-NGC Tenant" },
  { id: "ngc", name: "NGC" },
];

const fillRequired = async (page: Page, site: string = SITES_LIST.pdx01) => {
  await choose(page, SELECT_SITE, site);
  await page.getByLabel("Overlay ID").fill("test-overlay");
  await choose(page, SELECT_TENANT, TENANT_LIST.ngc);
};

test.beforeEach(async ({ page }) => {
  await mockServerCatalogAndUser(page, ["reader", "executor"]);
  await mockTypedLocationsEndpoint(page);
});

test.describe("New SpX Overlay Creation Workflow", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(PATH);
    await expect(page.getByRole("heading", { name: TITLE })).toBeVisible({ timeout: TEST_TIMEOUT });
  });

  test("renders the fields with the namespace and RD defaults", async ({ page }) => {
    await expect(page.locator("form label")).toHaveText([
      "Site *",
      "Overlay ID *",
      "Tenant *",
      "Namespace Tag",
      "RD Min",
      "RD Max",
    ]);
    await expect(selected(page, "spectrumx")).toBeVisible({ timeout: TEST_TIMEOUT });
    await expect(rdMin(page)).toHaveValue("60000");
    await expect(rdMax(page)).toHaveValue("65000");
    await expect(page.getByLabel("Overlay ID")).toHaveValue("");
  });

  test("reports the missing inputs and sends nothing", async ({ page }) => {
    const posts = recordPosts(page, ENDPOINT);
    await submit(page);
    for (const label of ["Site", "Overlay ID", "Tenant"]) {
      await expect(page.getByText(`${label} is required`, { exact: true })).toBeVisible();
    }
    expect(posts).toEqual([]);
  });

  test("submits the inputs with the defaults", async ({ page }) => {
    await fillRequired(page);
    const post = nextPost(page, ENDPOINT);
    await submit(page);
    expect((await post).postDataJSON()).toEqual({
      site: SITES_LIST.pdx01,
      site_type: "Site",
      overlay_id: "test-overlay",
      tenant: TENANT_LIST.ngc,
      namespace_tag: "spectrumx",
      rd_min: 60000,
      rd_max: 65000,
    });
    await expectWorkflowDetails(page);
  });

  test("omits cleared RD bounds and checks their range", async ({ page }) => {
    await fillRequired(page);
    await rdMin(page).fill("70000");
    await submit(page);
    await expect(page.getByText("Must be <= 65535")).toBeVisible();

    await rdMin(page).fill("");
    await rdMax(page).fill("");
    const post = nextPost(page, ENDPOINT);
    await submit(page);
    expect((await post).postDataJSON()).toEqual({
      site: SITES_LIST.pdx01,
      site_type: "Site",
      overlay_id: "test-overlay",
      tenant: TENANT_LIST.ngc,
      namespace_tag: "spectrumx",
    });
  });

  test("shows the model's RD ordering error inline", async ({ page }) => {
    await page.route(`**${ENDPOINT}`, (route) =>
      route.fulfill({
        status: 422,
        json: {
          detail: [
            { type: "value_error", loc: ["body"], msg: "Value error, rd_min must be less than rd_max" },
          ],
        },
      })
    );
    await fillRequired(page);
    await rdMin(page).fill("65000");
    await rdMax(page).fill("60000");
    await submit(page);
    await expect(formErrors(page)).toContainText("Value error, rd_min must be less than rd_max");
    await noFailureToast(page);
  });

  test("shows a forbidden site in the failure toast", async ({ page }) => {
    await fillRequired(page, FORBIDDEN_SITE_ID);
    await submit(page);
    await expectFailureToast(page, "Forbidden: You do not have permission to run this workflow");
  });
});

test("filters tenants by contiguous text and ranks an exact match first", async ({ page }) => {
  await page.route(/.*\/v1\/parameter\/tenant/, (route) =>
    route.fulfill({ status: 200, json: SEARCH_TENANTS })
  );
  await page.goto(PATH);

  await page.getByRole("button", { name: SELECT_TENANT, exact: true }).click();
  const tenantDialog = page.getByRole("dialog");
  const tenantSearch = tenantDialog.getByPlaceholder("Search Tenant");
  await tenantSearch.fill("NGC");

  await expect(tenantDialog.getByText("Engineering Cloud", { exact: true })).toBeHidden();
  await expect(tenantDialog.locator("[cmdk-item]:visible")).toHaveCount(3);

  await tenantSearch.press("Enter");
  await expect(selected(page, "NGC")).toBeVisible();
});

test.describe("New SpX Overlay Creation Workflow - URL Parameters", () => {
  test("a legacy link with ?namespace= prefills every field and submits them", async ({ page }) => {
    const query =
      `?site=${SITES_LIST.pdx01}&overlay_id=test-overlay-1&tenant=${TENANT_LIST.ngc}` +
      "&namespace=tenant-a&rd_min=61000&rd_max=62000";
    await page.goto(`/workflows/spxoverlaycreationworkflow/form${query}`);
    await expect(page).toHaveURL(`${PATH}${query}`);

    await expect(selected(page, SITES_LIST.pdx01)).toBeVisible({ timeout: TEST_TIMEOUT });
    await expect(page.getByLabel("Overlay ID")).toHaveValue("test-overlay-1");
    await expect(selected(page, TENANT_LIST.ngc)).toBeVisible({ timeout: TEST_TIMEOUT });
    await expect(selected(page, "tenant-a")).toBeVisible({ timeout: TEST_TIMEOUT });
    await expect(rdMin(page)).toHaveValue("61000");
    await expect(rdMax(page)).toHaveValue("62000");

    const post = nextPost(page, ENDPOINT);
    await submit(page);
    expect((await post).postDataJSON()).toEqual({
      site: SITES_LIST.pdx01,
      site_type: "Site",
      overlay_id: "test-overlay-1",
      tenant: TENANT_LIST.ngc,
      namespace_tag: "tenant-a",
      rd_min: 61000,
      rd_max: 62000,
    });
    await expectWorkflowDetails(page);
  });

  test("prefilled values can be changed before submitting", async ({ page }) => {
    await page.goto(`${PATH}?site=${SITES_LIST.pdx01}&overlay_id=test-overlay-1&tenant=${TENANT_LIST.ngc}`);
    await expect(selected(page, TENANT_LIST.ngc)).toBeVisible({ timeout: TEST_TIMEOUT });

    await selected(page, SITES_LIST.pdx01).click();
    await page.getByRole("dialog").getByRole("option", { name: SITES_LIST.rno1, exact: true }).click();
    await page.getByLabel("Overlay ID").fill("modified-vpc");
    await selected(page, TENANT_LIST.ngc).click();
    await page.getByRole("dialog").getByRole("option", { name: TENANT_LIST.tenant_a, exact: true }).click();

    const post = nextPost(page, ENDPOINT);
    await submit(page);
    expect((await post).postDataJSON()).toMatchObject({
      site: SITES_LIST.rno1,
      overlay_id: "modified-vpc",
      tenant: TENANT_LIST.tenant_a,
    });
  });
});
