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
import { FORBIDDEN_SITE_ID, SITES_LIST, TENANT_LIST } from "@/mocks/data";
import { test, TEST_TIMEOUT } from "./shared/utils";

// Sample VPC data for testing
const VPC_DATA = {
  overlay_id: "test-overlay-1",
  tenant: TENANT_LIST.ngc,
  namespace_tag: "spectrumx",
  rd_min: 60000,
  rd_max: 65000,
};

const SEARCH_TENANTS = [
  { id: "engineering-cloud", name: "Engineering Cloud" },
  { id: "ngc-platform", name: "NGC Platform" },
  { id: "pre-ngc", name: "Pre-NGC Tenant" },
  { id: "ngc", name: "NGC" },
];

test.describe("New SpX Overlay Creation Workflow", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/workflows/spxoverlaycreationworkflow/form");
  });

  test("renders form with correct title", async ({ page }) => {
    const title = await page.getByRole("heading", {
      name: "New SpX Overlay Creation Workflow",
    });
    await expect(title).toBeVisible({ timeout: TEST_TIMEOUT });
  });

  test("displays validation errors for empty submission", async ({ page }) => {
    // Clear the default values that are auto-populated
    await page.getByLabel("RD Min").fill("");
    await page.getByLabel("RD Max").fill("");

    await page.getByRole("button", { name: "Submit" }).click();

    // Check for all required field validations
    await expect(page.getByText("Site is required")).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
    await expect(page.getByText("Overlay ID is required")).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
    await expect(page.getByText("Tenant is required")).toBeVisible({
      timeout: TEST_TIMEOUT,
    });

    // Verify that "Expected number, received nan" appears exactly twice
    const nanErrorElements = await page
      .getByText("Expected number, received nan")
      .all();
    expect(nanErrorElements.length).toBe(2);
  });

  test("displays validation error when RD Min is greater than RD Max", async ({
    page,
  }) => {
    // Fill in required fields
    await page.getByRole("button", { name: "Site" }).click();
    await page.getByRole("dialog").getByText(SITES_LIST.pdx01).click();
    // Click outside to close any dropdown that might be open
    await page
      .getByRole("heading", { name: "New SpX Overlay Creation Workflow" })
      .click();

    await page.getByLabel("Overlay ID").fill("test-overlay");
    await page.getByRole("button", { name: "Tenant" }).click();
    await page.getByRole("dialog").getByText(TENANT_LIST.ngc).click();

    // Set RD Min greater than RD Max
    await page.getByLabel("RD Min").fill("65000");
    await page.getByLabel("RD Max").fill("60000");

    await page.getByRole("button", { name: "Submit" }).click();

    // Check for validation error
    await expect(page.getByText("RD Min must be less than RD Max")).toBeVisible(
      { timeout: TEST_TIMEOUT }
    );
  });
});

test("filters tenants by contiguous text and ranks an exact match first", async ({
  page,
}) => {
  await page.unroute(/.*\/v1\/parameter\/tenant/);
  await page.route(/.*\/v1\/parameter\/tenant/, async (route) => {
    await route.fulfill({ status: 200, json: SEARCH_TENANTS });
  });
  await page.goto("/workflows/spxoverlaycreationworkflow/form");

  await page.getByRole("button", { name: "Tenant" }).click();
  const tenantDialog = page.getByRole("dialog");
  const tenantSearch = tenantDialog.getByPlaceholder("Search Tenant");
  await tenantSearch.fill("NGC");

  await expect(
    tenantDialog.getByText("Engineering Cloud", { exact: true }),
  ).toBeHidden();
  await expect(tenantDialog.locator("[cmdk-item]:visible")).toHaveCount(3);

  await tenantSearch.press("Enter");
  await expect(
    page.getByRole("button", { name: "NGC. Open options", exact: true }),
  ).toBeVisible();
});

// Tests that handle their own navigation with URL parameters
test.describe("New SpX Overlay Creation Workflow - URL Parameters", () => {
  test("handles URL parameters correctly and submits with those values", async ({
    page,
  }) => {
    // Navigate with all URL parameters
    await page.goto(
      "/workflows/spxoverlaycreationworkflow/form" +
        `?site=${SITES_LIST.pdx01}` +
        `&overlay_id=${VPC_DATA.overlay_id}` +
        `&tenant=${VPC_DATA.tenant}` +
        `&namespace=${VPC_DATA.namespace_tag}` +
        `&rd_min=${VPC_DATA.rd_min}` +
        `&rd_max=${VPC_DATA.rd_max}`
    );

    // Verify all fields are pre-populated
    await expect(
      page.getByRole("button", {
        name: `${SITES_LIST.pdx01}. Open options`,
        exact: true,
      })
    ).toBeVisible({ timeout: TEST_TIMEOUT });
    await expect(page.getByLabel("Overlay ID")).toHaveValue(
      VPC_DATA.overlay_id
    );
    await expect(
      page.getByRole("button", {
        name: `${VPC_DATA.tenant}. Open options`,
        exact: true,
      })
    ).toBeVisible({ timeout: TEST_TIMEOUT });
    await expect(
      page.getByRole("button", {
        name: `${VPC_DATA.namespace_tag}. Open options`,
        exact: true,
      })
    ).toBeVisible({ timeout: TEST_TIMEOUT });
    await expect(page.getByLabel("RD Min")).toHaveValue(
      VPC_DATA.rd_min.toString()
    );
    await expect(page.getByLabel("RD Max")).toHaveValue(
      VPC_DATA.rd_max.toString()
    );

    // Set up a listener for the request (after page is loaded)
    const requestPromise = page.waitForRequest((request) => {
      return request.url().includes("/v1/workflow/ngc/spx_overlay_creation");
    });

    // Submit the form with the URL parameters
    await page.getByRole("button", { name: "Submit" }).click();

    // Get the request before navigation completes
    const request = await requestPromise;
    const requestData = JSON.parse((await request.postData()) || "{}");

    // Verify the request data matches the URL parameters
    expect(requestData).toEqual({
      site: SITES_LIST.pdx01,
      overlay_id: VPC_DATA.overlay_id,
      tenant: VPC_DATA.tenant,
      namespace_tag: VPC_DATA.namespace_tag,
      rd_min: VPC_DATA.rd_min,
      rd_max: VPC_DATA.rd_max,
    });

    // Wait for navigation to confirm submission completed
    await expect(
      page.getByRole("heading", { name: "Workflow Details" })
    ).toBeVisible({ timeout: TEST_TIMEOUT });
  });

  test("loads from URL parameters then do manual changes before submission", async ({
    page,
  }) => {
    // Navigate with initial URL parameters
    await page.goto(
      "/workflows/spxoverlaycreationworkflow/form" +
        `?site=${SITES_LIST.pdx01}` +
        `&overlay_id=${VPC_DATA.overlay_id}` +
        `&tenant=${VPC_DATA.tenant}` +
        `&namespace=${VPC_DATA.namespace_tag}` +
        `&rd_min=${VPC_DATA.rd_min}` +
        `&rd_max=${VPC_DATA.rd_max}`
    );

    // Verify initial values are pre-populated
    await expect(
      page.getByRole("button", {
        name: `${SITES_LIST.pdx01}. Open options`,
        exact: true,
      })
    ).toBeVisible({ timeout: TEST_TIMEOUT });

    // Change the site
    await page.getByRole("button", { name: SITES_LIST.pdx01 }).click();
    await page.getByRole("dialog").getByText(SITES_LIST.rno1).click();
    // Click outside to close any dropdown that might be open
    await page
      .getByRole("heading", { name: "New SpX Overlay Creation Workflow" })
      .click();

    // Change the VPC ID
    await page.getByLabel("Overlay ID").fill("modified-vpc");

    // Change the tenant
    await page
      .getByRole("button", {
        name: `${VPC_DATA.tenant}. Open options`,
        exact: true,
      })
      .click();
    await page.getByRole("dialog").getByText(TENANT_LIST.tenant_a).click();

    // Change the namespace tag
    await page.getByRole("button", { name: VPC_DATA.namespace_tag }).click();
    await page.getByRole("dialog").getByText("tenant-a").click();

    // Change RD Min and RD Max
    await page.getByLabel("RD Min").fill("61000");
    await page.getByLabel("RD Max").fill("64000");

    // Set up a listener for the request (after page is loaded)
    const requestPromise = page.waitForRequest((request) => {
      return request.url().includes("/v1/workflow/ngc/spx_overlay_creation");
    });

    // Submit the form with the modified values
    await page.getByRole("button", { name: "Submit" }).click();

    // Get the request before navigation completes
    const request = await requestPromise;
    const requestData = JSON.parse((await request.postData()) || "{}");

    // Verify the request data matches the manually changed values
    expect(requestData).toEqual({
      site: SITES_LIST.rno1,
      overlay_id: "modified-vpc",
      tenant: TENANT_LIST.tenant_a,
      namespace_tag: "tenant-a",
      rd_min: 61000,
      rd_max: 64000,
    });

    // Wait for navigation to confirm submission completed
    await expect(
      page.getByRole("heading", { name: "Workflow Details" })
    ).toBeVisible({ timeout: TEST_TIMEOUT });
  });

  test("submits form directly from URL parameters without changes", async ({
    page,
  }) => {
    // Navigate with all URL parameters
    await page.goto(
      "/workflows/spxoverlaycreationworkflow/form" +
        `?site=${SITES_LIST.pdx01}` +
        `&overlay_id=${VPC_DATA.overlay_id}` +
        `&tenant=${VPC_DATA.tenant}` +
        `&namespace=${VPC_DATA.namespace_tag}` +
        `&rd_min=${VPC_DATA.rd_min}` +
        `&rd_max=${VPC_DATA.rd_max}`
    );

    // Verify all fields are pre-populated
    await expect(
      page.getByRole("button", {
        name: `${SITES_LIST.pdx01}. Open options`,
        exact: true,
      })
    ).toBeVisible({ timeout: TEST_TIMEOUT });
    await expect(
      page.getByRole("button", {
        name: `${VPC_DATA.tenant}. Open options`,
        exact: true,
      })
    ).toBeVisible({ timeout: TEST_TIMEOUT });

    // Set up a listener for the request (after page is loaded)
    const requestPromise = page.waitForRequest((request) => {
      return request.url().includes("/v1/workflow/ngc/spx_overlay_creation");
    });

    // Submit the form directly without making any changes
    await page.getByRole("button", { name: "Submit" }).click();

    // Get the request before navigation completes
    const request = await requestPromise;
    const requestData = JSON.parse((await request.postData()) || "{}");

    // Verify the request data contains the URL parameter values
    expect(requestData).toEqual({
      site: SITES_LIST.pdx01,
      overlay_id: VPC_DATA.overlay_id,
      tenant: VPC_DATA.tenant,
      namespace_tag: VPC_DATA.namespace_tag,
      rd_min: VPC_DATA.rd_min,
      rd_max: VPC_DATA.rd_max,
    });

    // Wait for navigation to confirm submission completed
    await expect(
      page.getByRole("heading", { name: "Workflow Details" })
    ).toBeVisible({ timeout: TEST_TIMEOUT });
  });
});

// Tests that use beforeEach navigation
test.describe("New SpX Overlay Creation Workflow - Standard Tests", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/workflows/spxoverlaycreationworkflow/form");
  });

  test(`disables form during submission`, async ({ page }) => {
    // Fill form with specific test values
    await page.getByRole("button", { name: "Site" }).click();
    await page.getByRole("dialog").getByText(SITES_LIST.pdx01).click();
    // Click outside to close any dropdown that might be open
    await page
      .getByRole("heading", { name: "New SpX Overlay Creation Workflow" })
      .click();

    await page.getByLabel("Overlay ID").fill("test-overlay-submission");
    await page.getByRole("button", { name: "Tenant" }).click();
    await page.getByRole("dialog").getByText(TENANT_LIST.ngc).click();
    await page.getByLabel("RD Min").fill("62000");
    await page.getByLabel("RD Max").fill("63000");

    await page.getByRole("button", { name: "Submit" }).click();

    // Verify all form elements are disabled during submission
    await expect(
      page.getByRole("button", {
        name: `${SITES_LIST.pdx01}. Open options`,
        exact: true,
      })
    ).toBeDisabled();
    await expect(page.getByLabel("Overlay ID")).toBeDisabled();
    await expect(
      page.getByRole("button", {
        name: `${TENANT_LIST.ngc}. Open options`,
        exact: true,
      })
    ).toBeDisabled();
    await expect(
      page.getByRole("button", {
        name: `${VPC_DATA.namespace_tag}. Open options`,
        exact: true,
      })
    ).toBeDisabled();
    await expect(page.getByLabel("RD Min")).toBeDisabled();
    await expect(page.getByLabel("RD Max")).toBeDisabled();
    await expect(
      page.getByRole("button", { name: "Submitting..." })
    ).toBeDisabled();
  });

  test("populates default values for namespace tag, rd_min, and rd_max", async ({
    page,
  }) => {
    // Navigate to the form without any URL parameters
    await page.goto("/workflows/spxoverlaycreationworkflow/form");

    // Verify that namespace tag has the default value "spectrumx"
    await expect(
      page.getByRole("button", {
        name: `${VPC_DATA.namespace_tag}. Open options`,
        exact: true,
      })
    ).toBeVisible({ timeout: TEST_TIMEOUT });

    // Verify that RD Min has the default value "60000"
    await expect(page.getByLabel("RD Min")).toHaveValue("60000");

    // Verify that RD Max has the default value "65000"
    await expect(page.getByLabel("RD Max")).toHaveValue("65000");

    // Verify that Site, VPC, and Tenant are empty (no defaults)
    await expect(page.getByRole("button", { name: "Site" })).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
    await expect(page.getByLabel("Overlay ID")).toHaveValue("");
    await expect(page.getByRole("button", { name: "Tenant" })).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
  });

  test("displays forbidden error notification when submitting with forbidden values", async ({
    page,
  }) => {
    // Fill form with forbidden site and other required fields
    await page.getByRole("button", { name: "Site" }).click();
    await page.getByRole("dialog").getByText(FORBIDDEN_SITE_ID).click();

    await page.getByLabel("Overlay ID").fill("test-overlay");
    await page.getByRole("button", { name: "Tenant" }).click();
    await page.getByRole("dialog").getByText(TENANT_LIST.ngc).click();
    await page.getByLabel("RD Min").fill("60000");
    await page.getByLabel("RD Max").fill("65000");

    await page.getByRole("button", { name: "Submit" }).click();

    // NOTE: While not ideal, firefox has a weird bug where the toast notification is not visible unless we force a viewport adjustment.
    const errorTitle = page.locator("div.text-sm.font-semibold", {
      hasText: "Workflow Failed",
    });
    const errorMessage = page.locator("div.text-sm.opacity-90", {
      hasText: "Forbidden: You do not have permission to run this workflow",
    });

    await expect(errorTitle).toBeVisible({ timeout: TEST_TIMEOUT });
    await expect(errorMessage).toBeVisible({ timeout: TEST_TIMEOUT });
  });
});
