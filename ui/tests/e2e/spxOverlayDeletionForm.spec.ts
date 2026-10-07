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
 * The SpX Overlay Deletion form on its class-name route, rendered by the RJSF form
 * because the legacy `/workflows/spxoverlaydeletionworkflow/form` redirects
 * there. Its wording comes from the server's `ui_schema`.
 */
import { expect, type Request } from "@playwright/test";
import { SITES_LIST, FORBIDDEN_SITE_ID, SPX_OVERLAY_LIST } from "@/mocks/data";
import { mockServerCatalogAndUser } from "./shared/apiMocks";
import { test, TEST_TIMEOUT } from "./shared/utils";

const FORM_PATH = "/workflows/new/SpXOverlayDeletionWorkflow";
const SITE_PICKER = { name: "Select a Site...", exact: true } as const;
// Until a site is chosen the overlay picker is disabled, as on the legacy page.
const OVERLAY_PICKER = { name: "Select a Overlay ID...", exact: true } as const;

test.beforeEach(async ({ page }) => {
  await mockServerCatalogAndUser(page, ["reader", "executor"]);
});

// Sample VPC data for testing
const VPC_DATA = {
  overlay_id: SPX_OVERLAY_LIST.primary,
  namespace_tag: "tenant-a",
  site: SITES_LIST.pdx01,
};

test.describe("New SpX Overlay Deletion Workflow", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(FORM_PATH);
  });

  test("renders form with correct title", async ({ page }) => {
    const title = await page.getByRole("heading", {
      name: "New SpX Overlay Deletion Workflow",
    });
    await expect(title).toBeVisible({ timeout: TEST_TIMEOUT });
  });

  test("shows the legacy fields in order; option lists follow the site like the legacy form", async ({
    page,
  }) => {
    await expect(page.locator("form label")).toHaveText([
      "Site *",
      "Overlay ID *",
      "Namespace Tag",
    ]);
    // Like the legacy page: no help text (schema descriptions) under the fields.
    await expect(page.locator("form p")).toHaveCount(0);

    // Namespace tags load unfiltered, then for the chosen site; overlays only for a site.
    const requests: Request[] = [];
    page.on("request", (request) => {
      const { pathname } = new URL(request.url());
      if (["/v1/parameter/overlay", "/v1/parameter/namespace-tag"].includes(pathname)) {
        requests.push(request);
      }
    });
    await page.goto(FORM_PATH);
    await expect(
      page.getByRole("button", { name: "spectrumx. Open options", exact: true })
    ).toBeVisible({ timeout: TEST_TIMEOUT });
    // Distinct requests as path + sorted query; the legacy page ordered them differently.
    const asSeen = () =>
      [
        ...new Set(
          requests.map((request) => {
            const url = new URL(request.url());
            const query = [...url.searchParams].map(([key, value]) => `${key}=${value}`);
            return [url.pathname, ...query.sort()].join(" ");
          })
        ),
      ].sort();
    expect(asSeen()).toEqual(["/v1/parameter/namespace-tag"]);

    await page.getByRole("button", SITE_PICKER).click();
    await page.getByRole("dialog").getByText(SITES_LIST.pdx01).click();
    await expect(
      page.getByRole("button", OVERLAY_PICKER)
    ).toBeEnabled({ timeout: TEST_TIMEOUT });
    await expect
      .poll(asSeen)
      .toEqual([
        "/v1/parameter/namespace-tag",
        `/v1/parameter/namespace-tag location=${SITES_LIST.pdx01}`,
        `/v1/parameter/overlay isolation_type=spectrum_x_vrf location=${SITES_LIST.pdx01}`,
      ]);
    // The namespace tag stays selected across the site change, as before.
    await expect(
      page.getByRole("button", { name: "spectrumx. Open options", exact: true })
    ).toBeVisible();
  });

  test("displays validation errors for empty submission", async ({ page }) => {
    await expect(
      page.getByRole("button", { name: "Select a Site..." })
    ).toBeEnabled({
      timeout: TEST_TIMEOUT,
    });

    await page.getByRole("button", { name: "Submit" }).click();

    // Check for all required field validations
    await expect(page.getByText("Site is required")).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
    await expect(page.getByText("Overlay ID is required")).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
  });
});

// Tests that handle their own navigation with URL parameters
test.describe("New SpX Overlay Deletion Workflow - URL Parameters", () => {
  test("handles URL parameters correctly and submits with those values", async ({
    page,
  }) => {
    // Navigate with all URL parameters
    await page.goto(
      FORM_PATH +
        `?site=${SITES_LIST.pdx01}` +
        `&overlay_id=${VPC_DATA.overlay_id}` +
        `&namespace=${VPC_DATA.namespace_tag}`
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
        name: `${VPC_DATA.overlay_id}. Open options`,
        exact: true,
      })
    ).toBeVisible({ timeout: TEST_TIMEOUT });
    await expect(
      page.getByRole("button", {
        name: `${VPC_DATA.namespace_tag}. Open options`,
        exact: true,
      })
    ).toBeVisible({ timeout: TEST_TIMEOUT });

    // Set up a listener for the request (after page is loaded)
    const requestPromise = page.waitForRequest((request) => {
      return request.url().includes("/v1/workflow/ngc/spx_overlay_deletion");
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
      namespace_tag: VPC_DATA.namespace_tag,
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
      FORM_PATH +
        `?site=${SITES_LIST.pdx01}` +
        `&overlay_id=${VPC_DATA.overlay_id}` +
        `&namespace=${VPC_DATA.namespace_tag}`
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
      .getByRole("heading", { name: "New SpX Overlay Deletion Workflow" })
      .click();

    // Change the overlay ID
    await page.getByRole("button", { name: "Overlay ID" }).click();
    await page.getByRole("dialog").getByText(SPX_OVERLAY_LIST.modified).click();

    // Change the namespace tag
    await page.getByRole("button", { name: VPC_DATA.namespace_tag }).click();
    await page.getByRole("dialog").getByText("spectrumx").click();

    // Set up a listener for the request (after page is loaded)
    const requestPromise = page.waitForRequest((request) => {
      return request.url().includes("/v1/workflow/ngc/spx_overlay_deletion");
    });

    // Submit the form with the modified values
    await page.getByRole("button", { name: "Submit" }).click();

    // Get the request before navigation completes
    const request = await requestPromise;
    const requestData = JSON.parse((await request.postData()) || "{}");

    // Verify the request data matches the manually changed values
    expect(requestData).toEqual({
      site: SITES_LIST.rno1,
      overlay_id: SPX_OVERLAY_LIST.modified,
      namespace_tag: "spectrumx",
    });

    // Wait for navigation to confirm submission completed
    await expect(
      page.getByRole("heading", { name: "Workflow Details" })
    ).toBeVisible({ timeout: TEST_TIMEOUT });
  });
});

// Tests that use beforeEach navigation
test.describe("New SpX Overlay Deletion Workflow - Standard Tests", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(FORM_PATH);
  });

  test("submits correct data to the API", async ({ page }) => {
    // Set up a listener for the request
    const requestPromise = page.waitForRequest((request) => {
      return request.url().includes("/v1/workflow/ngc/spx_overlay_deletion");
    });

    // Fill form with specific test values
    await page.getByRole("button", SITE_PICKER).click();
    await page.getByRole("dialog").getByText(SITES_LIST.pdx01).click();
    // Click outside to close any dropdown that might be open
    await page
      .getByRole("heading", { name: "New SpX Overlay Deletion Workflow" })
      .click();

    await page.getByRole("button", { name: "Overlay ID" }).click();
    await page
      .getByRole("dialog")
      .getByText(SPX_OVERLAY_LIST.submission)
      .click();
    await page.getByRole("button", { name: "spectrumx" }).click();
    await page.getByRole("dialog").getByText("tenant-a").click();

    await page.getByRole("button", { name: "Submit" }).click();

    // Get the request before navigation completes
    const request = await requestPromise;
    const requestData = JSON.parse((await request.postData()) || "{}");

    // Verify the request data
    expect(requestData).toEqual({
      site: SITES_LIST.pdx01,
      overlay_id: SPX_OVERLAY_LIST.submission,
      namespace_tag: "tenant-a",
    });

    // Wait for navigation to confirm submission completed
    await expect(
      page.getByRole("heading", { name: "Workflow Details" })
    ).toBeVisible({ timeout: TEST_TIMEOUT });
  });

  test(`disables form during submission`, async ({ page }) => {
    // Fill form with specific test values
    await page.getByRole("button", SITE_PICKER).click();
    await page.getByRole("dialog").getByText(SITES_LIST.pdx01).click();
    // Click outside to close any dropdown that might be open
    await page
      .getByRole("heading", { name: "New SpX Overlay Deletion Workflow" })
      .click();

    await page.getByRole("button", { name: "Overlay ID" }).click();
    await page
      .getByRole("dialog")
      .getByText(SPX_OVERLAY_LIST.submission)
      .click();

    await page.getByRole("button", { name: "Submit" }).click();

    // Verify all form elements are disabled during submission
    await expect(
      page.getByRole("button", {
        name: `${SITES_LIST.pdx01}. Open options`,
        exact: true,
      })
    ).toBeDisabled();
    await expect(
      page.getByRole("button", {
        name: `${SPX_OVERLAY_LIST.submission}. Open options`,
        exact: true,
      })
    ).toBeDisabled();
    await expect(
      page.getByRole("button", { name: "spectrumx. Open options", exact: true })
    ).toBeDisabled();
    await expect(
      page.getByRole("button", { name: "Submitting..." })
    ).toBeDisabled();
  });

  test("displays forbidden error notification when submitting with forbidden values", async ({
    page,
  }) => {
    // Fill form with forbidden site and other required fields
    await page.getByRole("button", SITE_PICKER).click();
    await page.getByRole("dialog").getByText(FORBIDDEN_SITE_ID).click();

    await page.getByRole("button", { name: "Overlay ID" }).click();
    await page
      .getByRole("dialog")
      .getByText(SPX_OVERLAY_LIST.forbidden, { exact: true })
      .click();

    await page.getByRole("button", { name: "Submit" }).click();

    // NOTE: While not ideal, firefox has a weird bug where the toast notification is not visible unless we force a viewport adjustment.
    const errorTitle = page.locator("div.text-sm.font-semibold", {
      hasText: "Workflow Failed",
    });
    const errorMessage = page.locator("div.text-sm.opacity-90", {
      hasText: "Forbidden: You do not have permission to run this workflow",
    });

    // The generic toast title: per-workflow error titles are gone (plan section 17).
    await expect(errorTitle).toHaveText("Workflow Failed", {
      timeout: TEST_TIMEOUT,
    });
    await expect(errorMessage).toHaveText(
      "Forbidden: You do not have permission to run this workflow",
      { timeout: TEST_TIMEOUT }
    );
  });
});

// Test that needs to be in URL Parameters group
test.describe("New SpX Overlay Deletion Workflow - URL Parameters 2", () => {
  test("submits form directly from URL parameters without changes", async ({
    page,
  }) => {
    // Navigate with all URL parameters
    await page.goto(
      FORM_PATH +
        `?site=${SITES_LIST.pdx01}` +
        `&overlay_id=${VPC_DATA.overlay_id}` +
        `&namespace=${VPC_DATA.namespace_tag}`
    );

    // Verify all fields are pre-populated
    await expect(
      page.getByRole("button", {
        name: `${SITES_LIST.pdx01}. Open options`,
        exact: true,
      })
    ).toBeVisible({ timeout: TEST_TIMEOUT });

    // Set up a listener for the request (after page is loaded)
    const requestPromise = page.waitForRequest((request) => {
      return request.url().includes("/v1/workflow/ngc/spx_overlay_deletion");
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
      namespace_tag: VPC_DATA.namespace_tag,
    });

    // Wait for navigation to confirm submission completed
    await expect(
      page.getByRole("heading", { name: "Workflow Details" })
    ).toBeVisible({ timeout: TEST_TIMEOUT });
  });

  test("populates default values for namespace tag", async ({ page }) => {
    // Navigate to the form without any URL parameters
    await page.goto(FORM_PATH);

    // Verify that namespace tag has the default value "spectrumx"
    await expect(
      page.getByRole("button", { name: "spectrumx. Open options", exact: true })
    ).toBeVisible({ timeout: TEST_TIMEOUT });

    // Verify that Site and VPC are empty (no defaults)
    await expect(page.getByRole("button", SITE_PICKER)).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
    await expect(
      page.getByRole("button", OVERLAY_PICKER)
    ).toBeDisabled();
  });
});
