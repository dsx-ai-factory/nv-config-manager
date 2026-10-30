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

test.describe("Device Configurations Page", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/configs");
  });

  test("renders the configs page with correct title", async ({ page }) => {
    await expect(
      page.getByRole("heading", { name: "Device Configurations", level: 1 })
    ).toBeVisible({ timeout: TEST_TIMEOUT });

    await expect(
      page.getByText("Search and browse device configuration files")
    ).toBeVisible({ timeout: TEST_TIMEOUT });
  });

  test("displays file type tabs for intended and backup configs", async ({
    page,
  }) => {
    // Check tabs are visible
    const intendedTab = page.getByRole("tab", { name: "Intended Configs" });
    const backupTab = page.getByRole("tab", { name: "Backup Configs" });

    await expect(intendedTab).toBeVisible({ timeout: TEST_TIMEOUT });
    await expect(backupTab).toBeVisible({ timeout: TEST_TIMEOUT });

    // Intended should be selected by default
    await expect(intendedTab).toHaveAttribute("data-state", "active");
  });

  test("displays search input and devices table", async ({ page }) => {
    // Check search section
    await expect(
      page.getByRole("heading", { name: "Search Devices" })
    ).toBeVisible({ timeout: TEST_TIMEOUT });

    await expect(
      page.getByPlaceholder("Start typing to search devices...")
    ).toBeVisible({ timeout: TEST_TIMEOUT });

    // Check devices table is visible (headers may vary, just check table exists)
    await expect(page.getByRole("table")).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
    
    // Check for table header row
    await expect(page.locator("thead")).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
  });

  test("displays devices from API in the table", async ({ page }) => {
    // Wait for devices to load - use links for device names
    await expect(page.getByRole("link", { name: "spine-001" })).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
    await expect(page.getByRole("link", { name: "leaf-001" })).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
    await expect(page.getByRole("link", { name: "core-001" })).toBeVisible({
      timeout: TEST_TIMEOUT,
    });

    // Check site badges are displayed (use exact match to avoid duplicates)
    await expect(page.getByText("PDX01", { exact: true }).first()).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
    await expect(page.getByText("RNO1", { exact: true })).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
  });

  test("filters devices when searching", async ({ page }) => {
    const searchInput = page.getByPlaceholder(
      "Start typing to search devices..."
    );

    await searchInput.fill("spine");

    // Wait for filter to apply
    await expect(page.getByText("spine-001")).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
    await expect(page.getByText("leaf-001")).not.toBeVisible({
      timeout: TEST_TIMEOUT,
    });
  });

  test("switches between intended and backup config tabs", async ({ page }) => {
    const intendedTab = page.getByRole("tab", { name: "Intended Configs" });
    const backupTab = page.getByRole("tab", { name: "Backup Configs" });

    // Switch to backup tab
    await backupTab.click();
    await expect(backupTab).toHaveAttribute("data-state", "active");
    await expect(intendedTab).toHaveAttribute("data-state", "inactive");

    // The search description should update
    await expect(
      page.getByText("Search by device name for backup configs")
    ).toBeVisible({ timeout: TEST_TIMEOUT });

    // Switch back to intended
    await intendedTab.click();
    await expect(intendedTab).toHaveAttribute("data-state", "active");
    await expect(
      page.getByText("Search by device name for intended configs")
    ).toBeVisible({ timeout: TEST_TIMEOUT });
  });

  test("navigates to device detail page when clicking device name", async ({
    page,
  }) => {
    // Wait for device to be visible
    const deviceLink = page.getByRole("link", { name: "spine-001" });
    await expect(deviceLink).toBeVisible({ timeout: TEST_TIMEOUT });

    // Click on the device name
    await deviceLink.click();

    // Should navigate to device detail page
    await page.waitForURL("**/device/**");
    await expect(page).toHaveURL(/\/device\/device-uuid-1/);
  });

  test("navigates to device detail page when clicking elsewhere in the row", async ({
    page,
  }) => {
    const deviceRow = page.locator("tbody tr").filter({ hasText: "spine-001" });
    await expect(deviceRow).toBeVisible({ timeout: TEST_TIMEOUT });

    await deviceRow.locator("td").nth(3).click();

    await page.waitForURL("**/device/**");
    await expect(page).toHaveURL(/\/device\/device-uuid-1\?file_type=intended/);
  });

  test("keeps the backup file type when clicking elsewhere in the row", async ({
    page,
  }) => {
    await page.getByRole("tab", { name: "Backup Configs" }).click();
    const deviceRow = page.locator("tbody tr").filter({ hasText: "spine-001" });
    await expect(deviceRow).toBeVisible({ timeout: TEST_TIMEOUT });

    await deviceRow.locator("td").nth(3).click();

    await page.waitForURL("**/device/**");
    await expect(page).toHaveURL(/\/device\/device-uuid-1\?file_type=backup/);
  });

  test("stays on the configs page when selecting text in a row", async ({
    page,
  }) => {
    const authorCell = page
      .locator("tbody tr")
      .filter({ hasText: "spine-001" })
      .locator("td")
      .nth(3);
    await expect(authorCell).toBeVisible({ timeout: TEST_TIMEOUT });
    const box = await authorCell.boundingBox();
    const y = (box?.y ?? 0) + (box?.height ?? 0) / 2;
    const navigated = page
      .waitForURL("**/device/**", { timeout: 5000 })
      .then(
        () => true,
        () => false
      );

    await page.mouse.move((box?.x ?? 0) + 2, y);
    await page.mouse.down();
    await page.mouse.move((box?.x ?? 0) + (box?.width ?? 0) - 2, y, { steps: 5 });
    await page.mouse.up();

    expect(
      await page.evaluate(() => globalThis.getSelection()?.toString())
    ).not.toBe("");
    expect(await navigated).toBe(false);
  });

  test("shows correct result count in table header", async ({ page }) => {
    // Check for "All Devices" heading with count
    await expect(page.getByText(/All Devices \(\d+\)/)).toBeVisible({
      timeout: TEST_TIMEOUT,
    });

    // Search and verify "Search Results" heading appears
    const searchInput = page.getByPlaceholder(
      "Start typing to search devices..."
    );
    await searchInput.fill("spine");

    await expect(page.getByText(/Search Results \(\d+\)/)).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
  });

  test("shows Download All Configs button when devices are listed", async ({
    page,
  }) => {
    // Wait for devices to load
    await expect(page.getByRole("link", { name: "spine-001" })).toBeVisible(
      { timeout: TEST_TIMEOUT }
    );

    // Download All Configs button should be visible
    await expect(
      page.getByRole("button", { name: "Download All Configs" })
    ).toBeVisible({ timeout: TEST_TIMEOUT });
  });

  test("Download All Configs button is clickable and completes", async ({
    page,
  }) => {
    // Wait for devices to load
    await expect(page.getByRole("link", { name: "spine-001" })).toBeVisible(
      { timeout: TEST_TIMEOUT }
    );

    const downloadButton = page.getByRole("button", {
      name: "Download All Configs",
    });
    await downloadButton.click();

    // After click, button should return to enabled state (no error toast)
    await expect(
      page.getByRole("button", { name: "Download All Configs" })
    ).toBeVisible({ timeout: 15000 });
    await expect(
      page.getByRole("button", { name: "Download All Configs" })
    ).toBeEnabled({ timeout: 5000 });
  });

  test("displays Show inactive devices toggle", async ({ page }) => {
    await expect(page.getByLabel("Show inactive devices")).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
  });

  test("inactive devices are hidden by default", async ({ page }) => {
    // Active devices should be visible
    await expect(page.getByRole("link", { name: "spine-001" })).toBeVisible({
      timeout: TEST_TIMEOUT,
    });

    // Inactive device should NOT be visible
    await expect(page.getByRole("link", { name: "decomm-001" })).not.toBeVisible({
      timeout: TEST_TIMEOUT,
    });
  });

  test("toggling Show inactive devices reveals inactive devices with badge", async ({
    page,
  }) => {
    // Wait for initial load
    await expect(page.getByRole("link", { name: "spine-001" })).toBeVisible({
      timeout: TEST_TIMEOUT,
    });

    // Toggle on
    await page.getByLabel("Show inactive devices").click();

    // Inactive device should now appear
    await expect(page.getByRole("link", { name: "decomm-001" })).toBeVisible({
      timeout: TEST_TIMEOUT,
    });

    // Should have an "Inactive" badge
    await expect(page.getByText("Inactive", { exact: true })).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
  });

  test("delete button appears only on inactive devices", async ({ page }) => {
    // Wait for initial load
    await expect(page.getByRole("link", { name: "spine-001" })).toBeVisible({
      timeout: TEST_TIMEOUT,
    });

    // No delete buttons when only active devices are shown
    await expect(page.locator('[data-testid="delete-device-button"]')).not.toBeVisible();

    // Toggle inactive devices on
    await page.getByLabel("Show inactive devices").click();

    // Inactive device should appear
    await expect(page.getByRole("link", { name: "decomm-001" })).toBeVisible({
      timeout: TEST_TIMEOUT,
    });

    // Delete button should be visible (trash icon) on the inactive row
    const inactiveRow = page.locator("tr").filter({ hasText: "decomm-001" });
    await expect(inactiveRow.locator("button").filter({ has: page.locator("svg") }).first()).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
  });

  test("clicking delete button opens confirmation dialog", async ({ page }) => {
    // Toggle inactive devices on
    await page.getByLabel("Show inactive devices").click();

    // Wait for inactive device
    await expect(page.getByRole("link", { name: "decomm-001" })).toBeVisible({
      timeout: TEST_TIMEOUT,
    });

    // Click the delete button on the inactive row
    const inactiveRow = page.locator("tr").filter({ hasText: "decomm-001" });
    await inactiveRow.locator("button").filter({ has: page.locator("svg") }).first().click();

    // Confirmation dialog should appear
    const dialog = page.locator('[role="dialog"]');
    await expect(dialog.getByText("Permanently Delete Device Configs?")).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
    await expect(dialog.getByText("decomm-001")).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
    await expect(page.getByRole("button", { name: "Cancel" })).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
    await expect(page.getByRole("button", { name: "Delete Permanently" })).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
  });

  test("clicking delete button stays on the configs page", async ({ page }) => {
    await page.getByLabel("Show inactive devices").click();
    await expect(page.getByRole("link", { name: "decomm-001" })).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
    const navigated = page
      .waitForURL("**/device/**", { timeout: 5000 })
      .then(
        () => true,
        () => false
      );

    const inactiveRow = page.locator("tr").filter({ hasText: "decomm-001" });
    await inactiveRow.locator("button").filter({ has: page.locator("svg") }).first().click();

    expect(await navigated).toBe(false);
  });

  test("cancel button in delete dialog closes it without deleting", async ({
    page,
  }) => {
    // Toggle inactive devices on
    await page.getByLabel("Show inactive devices").click();

    // Wait for inactive device
    await expect(page.getByRole("link", { name: "decomm-001" })).toBeVisible({
      timeout: TEST_TIMEOUT,
    });

    // Open delete dialog
    const inactiveRow = page.locator("tr").filter({ hasText: "decomm-001" });
    await inactiveRow.locator("button").filter({ has: page.locator("svg") }).first().click();

    // Click cancel
    await page.getByRole("button", { name: "Cancel" }).click();

    // Dialog should close
    await expect(page.getByText("Permanently Delete Device Configs?")).not.toBeVisible({
      timeout: TEST_TIMEOUT,
    });

    // Device should still be in the list
    await expect(page.getByRole("link", { name: "decomm-001" })).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
  });

  test("confirming delete removes device from list", async ({ page }) => {
    // Toggle inactive devices on
    await page.getByLabel("Show inactive devices").click();

    // Wait for inactive device
    await expect(page.getByRole("link", { name: "decomm-001" })).toBeVisible({
      timeout: TEST_TIMEOUT,
    });

    // Open delete dialog
    const inactiveRow = page.locator("tr").filter({ hasText: "decomm-001" });
    await inactiveRow.locator("button").filter({ has: page.locator("svg") }).first().click();

    // Confirm deletion
    await page.getByRole("button", { name: "Delete Permanently" }).click();

    // Device should be removed from the list
    await expect(page.getByRole("link", { name: "decomm-001" })).not.toBeVisible({
      timeout: TEST_TIMEOUT,
    });

    // Dialog should close
    await expect(page.getByText("Permanently Delete Device Configs?")).not.toBeVisible({
      timeout: TEST_TIMEOUT,
    });

    // Active devices should still be there
    await expect(page.getByRole("link", { name: "spine-001" })).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
  });
});
