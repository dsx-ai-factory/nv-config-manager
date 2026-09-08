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
import { expect, type Page } from "@playwright/test";
import {
  SITES_LIST,
  ROLES_LIST,
  STATUS_LIST,
  TENANT_LIST,
  FORBIDDEN_SITE_ID,
} from "@/mocks/data";
import { test, TEST_TIMEOUT } from "./shared/utils";

const statusSelectButton = (page: Page) =>
  page
    .getByText("Device Status", { exact: true })
    .locator("..")
    .getByRole("button", { name: /\. Open options$/ });

const openSelectDialog = (page: Page) =>
  page.locator('[role="dialog"][data-state="open"]');

const openStatusSelect = async (page: Page) => {
  await statusSelectButton(page).press("Enter");
  await expect(openSelectDialog(page)).toBeVisible({ timeout: TEST_TIMEOUT });
};

test.describe("Site Cable Validation Form", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/workflows/sitecablevalidationworkflow/form");
  });

  test("renders form with correct title", async ({ page }) => {
    const title = await page.getByRole("heading", {
      name: "New Site Cable Validation Workflow",
    });
    await expect(title).toBeVisible({ timeout: TEST_TIMEOUT });
  });

  test("displays validation errors for empty submission", async ({ page }) => {
    await page.getByRole("button", { name: "Submit" }).click();

    // Check for all required field validations
    await expect(page.getByText("Site is required")).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
    await expect(page.getByText("Roles is required")).not.toBeVisible({
      timeout: TEST_TIMEOUT,
    });
    await expect(page.getByText("Device Status is required")).not.toBeVisible();
    await expect(page.getByText("Tenant is required")).not.toBeVisible();
    await expect(
      page.getByRole("button", { name: `Remove ${STATUS_LIST.active}` })
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: `Remove ${STATUS_LIST.provisioned}` })
    ).toBeVisible();
  });

  test("handles URL parameters correctly and submits with those values", async ({
    page,
  }) => {
    // Set up a listener for the request
    const requestPromise = page.waitForRequest((request) => {
      return request.url().includes("/v1/workflow/ngc/site_cable_validation");
    });

    // Navigate with all URL parameters
    await page.goto(
      "/workflows/sitecablevalidationworkflow/form" +
        `?site=${SITES_LIST.pdx01}` +
        `&role=${ROLES_LIST.leaf}` +
        `&status=${STATUS_LIST.active}` +
        `&tenant=${TENANT_LIST.tenant_a}`
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
        name: `${ROLES_LIST.leaf}. Open options`,
        exact: true,
      })
    ).toBeVisible({ timeout: TEST_TIMEOUT });
    await expect(
      page.getByRole("button", {
        name: `${STATUS_LIST.active}. Open options`,
        exact: true,
      })
    ).toBeVisible({ timeout: TEST_TIMEOUT });
    await expect(
      page.getByRole("button", {
        name: `${TENANT_LIST.tenant_a}. Open options`,
        exact: true,
      })
    ).toBeVisible({ timeout: TEST_TIMEOUT });

    // Submit the form with the URL parameters
    await page.getByRole("button", { name: "Submit" }).click();

    // Get the request before navigation completes
    const request = await requestPromise;
    const requestData = JSON.parse((await request.postData()) || "{}");

    // Verify the request data matches the URL parameters
    expect(requestData).toEqual({
      site: SITES_LIST.pdx01,
      roles: [ROLES_LIST.leaf],
      status: [STATUS_LIST.active],
      tenant: TENANT_LIST.tenant_a,
      device_type_ids: [],
      raise_for_invalid: false,
    });

    // Wait for navigation to confirm submission completed
    await expect(
      page.getByRole("heading", { name: "Workflow Details" })
    ).toBeVisible({ timeout: TEST_TIMEOUT });
  });

  test("loads from URL parameters then do manual changes before submission", async ({
    page,
  }) => {
    // Set up a listener for the request
    const requestPromise = page.waitForRequest((request) => {
      return request.url().includes("/v1/workflow/ngc/site_cable_validation");
    });

    // Navigate with initial URL parameters
    await page.goto(
      "/workflows/sitecablevalidationworkflow/form" +
        `?site=${SITES_LIST.pdx01}` +
        `&role=${ROLES_LIST.leaf}` +
        `&status=${STATUS_LIST.active}` +
        `&tenant=${TENANT_LIST.tenant_a}`
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
      .getByRole("heading", { name: "New Site Cable Validation Workflow" })
      .click();

    // Add another role
    await page
      .getByRole("button", { name: `${ROLES_LIST.leaf}. Open options` })
      .click();
    await page.getByRole("dialog").getByText(ROLES_LIST.spine).click();
    // Click outside to close any dropdown that might be open
    await page
      .getByRole("heading", { name: "New Site Cable Validation Workflow" })
      .click();

    // Change the tenant
    await page.getByRole("button", { name: TENANT_LIST.tenant_a }).click();
    await page.getByRole("dialog").getByText(TENANT_LIST.ngc).click();
    // Click outside to close any dropdown that might be open
    await page
      .getByRole("heading", { name: "New Site Cable Validation Workflow" })
      .click();

    // Submit the form with the modified values
    await page.getByRole("button", { name: "Submit" }).click();

    // Get the request before navigation completes
    const request = await requestPromise;
    const requestData = JSON.parse((await request.postData()) || "{}");

    // Verify the request data matches the manually changed values
    expect(requestData).toEqual({
      site: SITES_LIST.rno1,
      roles: [ROLES_LIST.leaf, ROLES_LIST.spine],
      status: [STATUS_LIST.active],
      tenant: TENANT_LIST.ngc,
      device_type_ids: [],
      raise_for_invalid: false,
    });

    // Wait for navigation to confirm submission completed
    await expect(
      page.getByRole("heading", { name: "Workflow Details" })
    ).toBeVisible({ timeout: TEST_TIMEOUT });
  });

  test("handles multiple selections for multi-select fields", async ({
    page,
  }) => {
    // Test multiple selections for Roles
    await page.locator("form").getByRole("button", { name: "Roles" }).click();
    await page.getByRole("dialog").getByText(ROLES_LIST.leaf).click();
    await page.getByRole("dialog").getByText(ROLES_LIST.spine).click();
    // Click outside to close any dropdown that might be open
    await page
      .getByRole("heading", { name: "New Site Cable Validation Workflow" })
      .click();

    await expect(
      page.getByRole("button", { name: `Remove ${ROLES_LIST.leaf}` })
    ).toBeVisible({ timeout: TEST_TIMEOUT });
    await expect(
      page.getByRole("button", { name: `Remove ${ROLES_LIST.spine}` })
    ).toBeVisible({ timeout: TEST_TIMEOUT });

    // Test multiple selections for Device Status
    await openStatusSelect(page);
    await page.getByRole("dialog").getByText(STATUS_LIST.planned).click();
    await page.getByRole("dialog").getByText(STATUS_LIST.staged).click();
    // Click outside to close any dropdown that might be open
    await page
      .getByRole("heading", { name: "New Site Cable Validation Workflow" })
      .click();

    await expect(
      page.getByRole("button", { name: `Remove ${STATUS_LIST.active}` })
    ).toBeVisible({ timeout: TEST_TIMEOUT });
    await expect(
      page.getByRole("button", { name: `Remove ${STATUS_LIST.provisioned}` })
    ).toBeVisible({ timeout: TEST_TIMEOUT });
    await expect(
      page.getByRole("button", { name: `Remove ${STATUS_LIST.planned}` })
    ).toBeVisible({ timeout: TEST_TIMEOUT });
    await expect(
      page.getByRole("button", { name: `Remove ${STATUS_LIST.staged}` })
    ).toBeVisible({ timeout: TEST_TIMEOUT });
  });

  test("submits correct data to the API", async ({ page }) => {
    // Set up a listener for the request
    const requestPromise = page.waitForRequest((request) => {
      return request.url().includes("/v1/workflow/ngc/site_cable_validation");
    });

    await page.getByRole("button", { name: "Site" }).click();
    await page.getByRole("dialog").getByText(SITES_LIST.pdx01).click();
    // Click outside to close any dropdown that might be open
    await page
      .getByRole("heading", { name: "New Site Cable Validation Workflow" })
      .click();

    await page.locator("form").getByRole("button", { name: "Roles" }).click();
    await page.getByRole("dialog").getByText(ROLES_LIST.leaf).click();
    await page.getByRole("dialog").getByText(ROLES_LIST.spine).click();
    // Click outside to close any dropdown that might be open
    await page
      .getByRole("heading", { name: "New Site Cable Validation Workflow" })
      .click();

    await openStatusSelect(page);
    await page.getByRole("dialog").getByText(STATUS_LIST.planned).click();
    // Click outside to close any dropdown that might be open
    await page
      .getByRole("heading", { name: "New Site Cable Validation Workflow" })
      .click();

    await page.getByRole("button", { name: "Tenant" }).click();
    await page.getByRole("dialog").getByText(TENANT_LIST.tenant_a).click();
    // Click outside to close any dropdown that might be open
    await page
      .getByRole("heading", { name: "New Site Cable Validation Workflow" })
      .click();

    await page.getByRole("button", { name: "Submit" }).click();

    const request = await requestPromise;
    const requestData = JSON.parse((await request.postData()) || "{}");

    // Verify the request data
    expect(requestData).toEqual({
      site: SITES_LIST.pdx01,
      roles: [ROLES_LIST.leaf, ROLES_LIST.spine],
      status: [
        STATUS_LIST.active,
        STATUS_LIST.provisioned,
        STATUS_LIST.planned,
      ],
      tenant: TENANT_LIST.tenant_a,
      device_type_ids: [],
      raise_for_invalid: false,
    });

    // Wait for navigation to confirm submission completed with a 30-second timeout
    await expect(
      page.getByRole("heading", { name: "Workflow Details" })
    ).toBeVisible({ timeout: TEST_TIMEOUT });
  });

  test(`disables form during submission`, async ({ page }) => {
    let markSubmissionStarted: () => void;
    const submissionStarted = new Promise<void>((resolve) => {
      markSubmissionStarted = resolve;
    });
    let releaseSubmission: () => void;
    const submissionReleased = new Promise<void>((resolve) => {
      releaseSubmission = resolve;
    });
    await page.route(
      "**/v1/workflow/ngc/site_cable_validation",
      async (route) => {
        markSubmissionStarted();
        await submissionReleased;
        await route.fallback();
      }
    );

    await page.getByRole("button", { name: "Site" }).click();
    await page.getByRole("dialog").getByText(SITES_LIST.pdx01).click();
    // Click outside to close any dropdown that might be open
    await page
      .getByRole("heading", { name: "New Site Cable Validation Workflow" })
      .click();

    await page.locator("form").getByRole("button", { name: "Roles" }).click();
    await page.getByRole("dialog").getByText(ROLES_LIST.leaf).click();
    await page.getByRole("dialog").getByText(ROLES_LIST.spine).click();
    // Click outside to close any dropdown that might be open
    await page
      .getByRole("heading", { name: "New Site Cable Validation Workflow" })
      .click();

    await openStatusSelect(page);
    await page.getByRole("dialog").getByText(STATUS_LIST.planned).click();
    // Click outside to close any dropdown that might be open
    await page
      .getByRole("heading", { name: "New Site Cable Validation Workflow" })
      .click();

    await page.getByRole("button", { name: "Tenant" }).click();
    await page.getByRole("dialog").getByText(TENANT_LIST.tenant_a).click();
    // Click outside to close any dropdown that might be open
    await page
      .getByRole("heading", { name: "New Site Cable Validation Workflow" })
      .click();

    await page.getByRole("button", { name: "Submit" }).click();
    await submissionStarted;

    await expect(
      page.getByRole("button", {
        name: `${SITES_LIST.pdx01}. Open options`,
        exact: true,
      })
    ).toBeDisabled();
    await expect(
      page.getByRole("button", { name: `Remove ${ROLES_LIST.leaf}` })
    ).toBeDisabled();
    await expect(
      page.getByRole("button", { name: `Remove ${ROLES_LIST.spine}` })
    ).toBeDisabled();
    await expect(
      page.getByRole("button", { name: `Remove ${STATUS_LIST.active}` })
    ).toBeDisabled();
    await expect(
      page.getByRole("button", { name: `Remove ${STATUS_LIST.provisioned}` })
    ).toBeDisabled();
    await expect(
      page.getByRole("button", { name: `Remove ${STATUS_LIST.planned}` })
    ).toBeDisabled();
    await expect(
      page.getByRole("button", {
        name: `${TENANT_LIST.tenant_a}. Open options`,
        exact: true,
      })
    ).toBeDisabled();
    await expect(
      page.getByRole("button", { name: "Submitting..." })
    ).toBeDisabled();

    releaseSubmission();
    await expect(
      page.getByRole("heading", { name: "Workflow Details" })
    ).toBeVisible({ timeout: TEST_TIMEOUT });
  });

  test("displays forbidden error notification when submitting with forbidden values", async ({
    page,
  }) => {
    // Fill form with forbidden site
    await page.getByRole("button", { name: "Site" }).click();
    await page.getByRole("dialog").getByText(FORBIDDEN_SITE_ID).click();

    // Fill other required fields
    await page.locator("form").getByRole("button", { name: "Roles" }).click();
    await page.getByRole("dialog").getByText(ROLES_LIST.leaf).click();
    await page
      .getByRole("heading", { name: "New Site Cable Validation Workflow" })
      .click();

    await page.getByRole("button", { name: "Tenant" }).click();
    await page.getByRole("dialog").getByText(TENANT_LIST.tenant_a).click();

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
