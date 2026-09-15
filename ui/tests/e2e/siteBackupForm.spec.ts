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
import {
  SITES_LIST,
  ROLES_LIST,
  STATUS_LIST,
  TENANT_LIST,
  FORBIDDEN_SITE_ID,
} from "@/mocks/data";
import { test, TEST_TIMEOUT } from "./shared/utils";

test.describe("Site Backup Form", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/workflows/sitebackupworkflow/form");
  });

  test("renders form with correct title", async ({ page }) => {
    const title = await page.getByRole("heading", {
      name: "New Site Configuration Backup Workflow",
    });
    await expect(title).toBeVisible({ timeout: TEST_TIMEOUT });
  });

  test("displays validation errors for empty submission", async ({ page }) => {
    await page.getByRole("button", { name: "Submit" }).click();

    await expect(page.getByText("Site is required")).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
    await expect(
      page.getByRole("button", { name: `Remove ${STATUS_LIST.active}` })
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: `Remove ${STATUS_LIST.provisioned}` })
    ).toBeVisible();
    await expect(page.getByLabel("Backup enabled only")).toBeChecked();
  });

  test("handles URL parameters correctly and submits with those values", async ({
    page,
  }) => {
    const requestPromise = page.waitForRequest((request) => {
      return request.url().includes("/v1/workflow/ngc/site_backup");
    });

    await page.goto(
      "/workflows/sitebackupworkflow/form" +
        `?site=${SITES_LIST.pdx01}` +
        `&role=${ROLES_LIST.leaf}` +
        `&status=${STATUS_LIST.active}` +
        `&tenant=${TENANT_LIST.tenant_a}` +
        "&backup_enabled_only=false"
    );

    await expect(
      page.getByRole("button", {
        name: `${SITES_LIST.pdx01}. Open options`,
        exact: true,
      })
    ).toBeVisible({ timeout: TEST_TIMEOUT });
    await expect(page.getByLabel("Backup enabled only")).not.toBeChecked();

    await page.getByRole("button", { name: "Submit" }).click();

    const request = await requestPromise;
    const requestData = JSON.parse((await request.postData()) || "{}");

    expect(requestData).toEqual({
      site: SITES_LIST.pdx01,
      roles: [ROLES_LIST.leaf],
      status: [STATUS_LIST.active],
      tenant: TENANT_LIST.tenant_a,
      backup_enabled_only: false,
    });

    await expect(
      page.getByRole("heading", { name: "Workflow Details" })
    ).toBeVisible({ timeout: TEST_TIMEOUT });
  });

  test("submits backup_enabled_only when checkbox is checked", async ({
    page,
  }) => {
    const requestPromise = page.waitForRequest((request) => {
      return request.url().includes("/v1/workflow/ngc/site_backup");
    });

    await page.goto(
      "/workflows/sitebackupworkflow/form?backup_enabled_only=false"
    );

    await expect(page.getByLabel("Backup enabled only")).not.toBeChecked();

    await page.locator("form").getByRole("button", { name: "Site" }).click();
    await page.getByRole("dialog").getByText(SITES_LIST.pdx01).click();
    await page
      .getByRole("heading", { name: "New Site Configuration Backup Workflow" })
      .click();

    await page.getByLabel("Backup enabled only").click();
    await expect(page.getByLabel("Backup enabled only")).toBeChecked();

    await page.getByRole("button", { name: "Submit" }).click();

    const request = await requestPromise;
    const requestData = JSON.parse((await request.postData()) || "{}");

    expect(requestData.site).toBe(SITES_LIST.pdx01);
    expect(requestData.backup_enabled_only).toBe(true);
  });

  test("shows forbidden error for unauthorized site", async ({ page }) => {
    await page.locator("form").getByRole("button", { name: "Site" }).click();
    await page.getByRole("dialog").getByText(FORBIDDEN_SITE_ID).click();
    await page
      .getByRole("heading", { name: "New Site Configuration Backup Workflow" })
      .click();

    await page.getByRole("button", { name: "Submit" }).click();

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
