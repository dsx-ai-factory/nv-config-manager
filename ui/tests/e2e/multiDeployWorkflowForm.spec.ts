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
import { test, TEST_TIMEOUT } from "./shared/utils";
import {
  SITES_LIST,
  ROLES_LIST,
  STATUS_LIST,
  TENANT_LIST,
  FORBIDDEN_SITE_ID,
} from "@/mocks/data";

test.describe("New Multi-Configuration Deploy Workflow", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/workflows/multideployworkflow/form");
  });

  test("renders Multi-Configuration Deploy form with correct title", async ({
    page,
  }) => {
    await expect(
      page.getByRole("heading", {
        name: "New Multi-Configuration Deploy Workflow",
      })
    ).toBeVisible();
  });

  test("submits form with all fields correctly", async ({ page }) => {
    // Set up request listener
    const requestPromise = page.waitForRequest((request) => {
      return request.url().includes("/v1/workflow/ngc/multi_deploy");
    });

    // Fill in role (single select)
    await page.locator("form").getByRole("button", { name: "Role" }).click();
    await page.getByRole("dialog").getByText(ROLES_LIST.wan).click();

    // Fill in batch size
    const batchSizeInput = page.getByRole("spinbutton", {
      name: "Max Batch Size",
    });
    await batchSizeInput.clear();
    await batchSizeInput.fill("10");

    // Fill in location
    await page.getByRole("button", { name: "Location" }).click();
    await page.getByRole("dialog").getByText(SITES_LIST.pdx01).click();

    // Fill in status (multi-select)
    await page.getByRole("button", { name: "Device Status" }).click();
    await page.getByRole("dialog").getByText(STATUS_LIST.provisioning).click();
    // Click outside to close dropdown
    await page
      .getByRole("heading", { name: "New Multi-Configuration Deploy Workflow" })
      .click();

    // Fill in tenant
    await page.getByRole("button", { name: "Tenant" }).click();
    await page.getByRole("dialog").getByText(TENANT_LIST.ngc).click();

    // Submit form
    await page.getByRole("button", { name: "Submit" }).click();

    // Verify request data
    const request = await requestPromise;
    const requestData = JSON.parse((await request.postData()) || "{}");

    expect(requestData).toEqual({
      role: ROLES_LIST.wan,
      max_batch_size: 10,
      location: SITES_LIST.pdx01,
      status: [STATUS_LIST.provisioning],
      tenant: TENANT_LIST.ngc,
      commit_confirm: true,
    });

    // Verify navigation to workflow details
    await expect(
      page.getByRole("heading", { name: "Workflow Details" })
    ).toBeVisible({ timeout: TEST_TIMEOUT });
  });

  test("validates required fields", async ({ page }) => {
    // Try to submit without filling required fields
    await page.getByRole("button", { name: "Submit" }).click();

    // Check for validation errors - only role is required
    await expect(page.getByText("Role is required")).toBeVisible();

    // Other fields should not show validation errors
    await expect(page.getByText("Location is required")).not.toBeVisible();
    await expect(
      page.getByText("At least one status is required")
    ).not.toBeVisible();
    await expect(page.getByText("Tenant is required")).not.toBeVisible();
  });

  test("submits form with only required field (role)", async ({ page }) => {
    // Set up request listener
    const requestPromise = page.waitForRequest((request) => {
      return request.url().includes("/v1/workflow/ngc/multi_deploy");
    });

    // Fill in only the required field - role
    await page.locator("form").getByRole("button", { name: "Role" }).click();
    await page.getByRole("dialog").getByText(ROLES_LIST.leaf).click();

    // Submit form
    await page.getByRole("button", { name: "Submit" }).click();

    // Verify request data - should have role and default batch size
    const request = await requestPromise;
    const requestData = JSON.parse((await request.postData()) || "{}");

    expect(requestData.role).toBe(ROLES_LIST.leaf);
    expect(requestData.max_batch_size).toBe(10); // Default value
    expect(requestData.commit_confirm).toBe(true); // Default value
    // Optional fields should be null when not provided
    expect(requestData.location).toBeNull();
    expect(requestData.status).toBeNull();
    expect(requestData.tenant).toBeNull();

    // Verify navigation to workflow details
    await expect(
      page.getByRole("heading", { name: "Workflow Details" })
    ).toBeVisible({ timeout: TEST_TIMEOUT });
  });

  test("validates batch size limits", async ({ page }) => {
    // Fill required field first (only role is required)
    await page.locator("form").getByRole("button", { name: "Role" }).click();
    await page.getByRole("dialog").getByText(ROLES_LIST.leaf).click();

    // Test batch size too small
    const batchSizeInput = page.getByRole("spinbutton", {
      name: "Max Batch Size",
    });
    await batchSizeInput.clear();
    await batchSizeInput.fill("0");
    await page.getByRole("button", { name: "Submit" }).click();
    await expect(page.getByText("Batch size must be at least 1")).toBeVisible();

    // Test batch size too large
    await batchSizeInput.clear();
    await batchSizeInput.fill("101");
    await page.getByRole("button", { name: "Submit" }).click();
    await expect(page.getByText("Batch size cannot exceed 100")).toBeVisible();
  });

  test("loads from URL parameters", async ({ page }) => {
    // Navigate with URL parameters
    await page.goto(
      "/workflows/multideployworkflow/form" +
        `?role=${ROLES_LIST.spine}` +
        `&max_batch_size=15` +
        `&location=${SITES_LIST.rno1}` +
        `&status=${STATUS_LIST.active}` +
        `&status=${STATUS_LIST.provisioning}` +
        `&tenant=${TENANT_LIST.tenant_a}`
    );

    // Verify fields are pre-populated
    await expect(
      page.getByRole("button", {
        name: `${ROLES_LIST.spine}. Open options`,
        exact: true,
      })
    ).toBeVisible({ timeout: TEST_TIMEOUT });

    await expect(
      page.getByRole("spinbutton", { name: "Max Batch Size" })
    ).toHaveValue("15");

    await expect(
      page.getByRole("button", {
        name: `${SITES_LIST.rno1}. Open options`,
        exact: true,
      })
    ).toBeVisible();

    // For multi-select status, verify both badges are displayed
    await expect(page.getByText(STATUS_LIST.active)).toBeVisible();
    await expect(page.getByText(STATUS_LIST.provisioning)).toBeVisible();

    await expect(
      page.getByRole("button", {
        name: `${TENANT_LIST.tenant_a}. Open options`,
        exact: true,
      })
    ).toBeVisible();
  });

  test("submits the location ID loaded from a URL parameter", async ({
    page,
  }) => {
    const locationName = "SJC01";
    const locationId = "location-sjc01-id";
    await page.route("**/v1/parameter/location*", async (route) => {
      await route.fulfill({
        status: 200,
        json: [{ id: locationId, name: locationName }],
      });
    });

    const requestPromise = page.waitForRequest((request) =>
      request.url().includes("/v1/workflow/ngc/multi_deploy")
    );

    await page.goto(
      "/workflows/multideployworkflow/form" +
        `?role=${ROLES_LIST.leaf}` +
        `&location=${locationName}`
    );
    await expect(
      page.getByRole("button", {
        name: `${locationName}. Open options`,
        exact: true,
      })
    ).toBeVisible({ timeout: TEST_TIMEOUT });

    await page.getByRole("button", { name: "Submit" }).click();

    const request = await requestPromise;
    const requestData = JSON.parse((await request.postData()) || "{}");
    expect(requestData.location).toBe(locationId);
  });

  test("handles manual changes after URL parameter loading", async ({
    page,
  }) => {
    // Set up request listener
    const requestPromise = page.waitForRequest((request) => {
      return request.url().includes("/v1/workflow/ngc/multi_deploy");
    });

    // Navigate with URL parameters
    await page.goto(
      "/workflows/multideployworkflow/form" +
        `?role=${ROLES_LIST.leaf}` +
        `&max_batch_size=5` +
        `&location=${SITES_LIST.pdx01}` +
        `&status=${STATUS_LIST.active}` +
        `&tenant=${TENANT_LIST.ngc}`
    );

    // Wait for form to load with pre-filled values
    await expect(
      page.getByRole("button", {
        name: `${ROLES_LIST.leaf}. Open options`,
        exact: true,
      })
    ).toBeVisible({ timeout: TEST_TIMEOUT });

    // Manually change role
    await page
      .getByRole("button", { name: `${ROLES_LIST.leaf}. Open options` })
      .click();
    await page.getByRole("dialog").getByText(ROLES_LIST.spine).click();

    // Manually change batch size
    const batchSizeInput = page.getByRole("spinbutton", {
      name: "Max Batch Size",
    });
    await batchSizeInput.clear();
    await batchSizeInput.fill("20");

    // Manually change location
    await page.getByRole("button", { name: SITES_LIST.pdx01 }).click();
    await page.getByRole("dialog").getByText(SITES_LIST.rno1).click();

    // Add another status
    await page
      .getByRole("button", { name: `${STATUS_LIST.active}. Open options` })
      .click();
    await page.getByRole("dialog").getByText(STATUS_LIST.provisioning).click();
    await page
      .getByRole("heading", { name: "New Multi-Configuration Deploy Workflow" })
      .click();

    // Submit form
    await page.getByRole("button", { name: "Submit" }).click();

    // Verify request data has manual changes
    const request = await requestPromise;
    const requestData = JSON.parse((await request.postData()) || "{}");

    expect(requestData).toEqual({
      role: ROLES_LIST.spine,
      max_batch_size: 20,
      location: SITES_LIST.rno1,
      status: [STATUS_LIST.active, STATUS_LIST.provisioning],
      tenant: TENANT_LIST.ngc,
      commit_confirm: true,
    });
  });

  test("handles forbidden location error", async ({ page }) => {
    // Fill in role
    await page.locator("form").getByRole("button", { name: "Role" }).click();
    await page.getByRole("dialog").getByText(ROLES_LIST.leaf).click();

    // Fill in forbidden location
    await page.getByRole("button", { name: "Location" }).click();
    await page.getByRole("dialog").getByText(FORBIDDEN_SITE_ID).click();

    // Submit form
    await page.getByRole("button", { name: "Submit" }).click();

    // Verify error message
    const errorTitle = page.locator("div.text-sm.font-semibold", {
      hasText: "Workflow Failed",
    });
    await expect(errorTitle).toBeVisible({ timeout: TEST_TIMEOUT });
  });

  test("allows only single role selection", async ({ page }) => {
    // Select first role
    await page.locator("form").getByRole("button", { name: "Role" }).click();
    await page.getByRole("dialog").getByText(ROLES_LIST.leaf).click();

    // Try to select another role - it should replace the first one
    await page
      .getByRole("button", { name: `${ROLES_LIST.leaf}. Open options` })
      .click();
    await page.getByRole("dialog").getByText(ROLES_LIST.spine).click();

    // Verify only the second role is selected
    await expect(
      page.getByRole("button", {
        name: `${ROLES_LIST.spine}. Open options`,
        exact: true,
      })
    ).toBeVisible();

    // Verify first role is not shown
    await expect(
      page.getByRole("button", {
        name: `${ROLES_LIST.leaf}. Open options`,
        exact: true,
      })
    ).not.toBeVisible();
  });

  test("allows multiple status selections", async ({ page }) => {
    // Select multiple statuses
    await page.getByRole("button", { name: "Device Status" }).click();
    await page.getByRole("dialog").getByText(STATUS_LIST.active).click();
    await page.getByRole("dialog").getByText(STATUS_LIST.provisioning).click();
    await page.getByRole("dialog").getByText(STATUS_LIST.planned).click();
    await page
      .getByRole("heading", { name: "New Multi-Configuration Deploy Workflow" })
      .click();

    await page.waitForTimeout(200);

    // Verify all status badges are shown
    await expect(page.getByText(STATUS_LIST.active)).toBeVisible();
    await expect(page.getByText(STATUS_LIST.provisioning)).toBeVisible();
    await expect(page.getByText(STATUS_LIST.planned)).toBeVisible();
  });

  test("maintains default batch size value", async ({ page }) => {
    // Verify default batch size is 10
    await expect(
      page.getByRole("spinbutton", { name: "Max Batch Size" })
    ).toHaveValue("10");
  });
});
