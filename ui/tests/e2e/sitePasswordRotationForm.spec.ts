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
 * The Site Password Rotation generic form on its class-name route (the legacy
 * `/workflows/sitepasswordrotationworkflow/form` redirects there). Its secret picker
 * is populated by a direct option source from the selected location and filters.
 */
import { expect } from "@playwright/test";
import { ROLES_LIST, SITES_LIST, STATUS_LIST, TENANT_LIST } from "@/mocks/data";
import {
  mockServerCatalogAndUser,
  mockTypedLocationsEndpoint,
} from "./shared/apiMocks";
import { test, TEST_TIMEOUT } from "./shared/utils";
import { formPath, nextPost, submit } from "./shared/workflowFormTests";

const PATH = formPath("SitePasswordRotationWorkflow");

test.describe("Site Password Rotation Form", () => {
  test.beforeEach(async ({ page }) => {
    await mockServerCatalogAndUser(page, ["reader", "executor"]);
    await mockTypedLocationsEndpoint(page);
    await page.goto("/workflows/sitepasswordrotationworkflow/form");
    await expect(page).toHaveURL(PATH);
  });

  test("renders form with correct title", async ({ page }) => {
    await expect(
      page.getByRole("heading", { name: "New Site Password Rotation Workflow" })
    ).toBeVisible();
  });

  test("secret field requires location selection first", async ({ page }) => {
    await expect(
      page.getByRole("button", { name: /select a secret to rotate/i })
    ).toBeDisabled();

    await page.getByRole("button", { name: /Select a Location/i }).click();
    await page.getByRole("dialog").getByRole("option", { name: SITES_LIST.rno1 }).click();

    await expect(
      page.getByRole("button", { name: /select a secret to rotate/i })
    ).toBeEnabled({ timeout: TEST_TIMEOUT });
  });

  test("shows device count feedback", async ({ page }) => {
    await page.getByRole("button", { name: /Select a Location/i }).click();
    await page.getByRole("dialog").getByRole("option", { name: SITES_LIST.rno1 }).click();

    await expect(
      page.getByText(/Matching devices: \d+/)
    ).toBeVisible({ timeout: TEST_TIMEOUT });
  });

  test("disables the secret picker when no devices match", async ({ page }) => {
    await page.getByRole("button", { name: /Select a Location/i }).click();
    await page.getByRole("dialog").getByRole("option", { name: SITES_LIST.rno1 }).click();

    await page.locator("form").getByRole("button", { name: /Select Roles/i }).click();
    await page
      .getByRole("dialog")
      .getByRole("option", { name: ROLES_LIST.leaf, exact: true })
      .click();
    await page.keyboard.press("Escape");

    await expect(page.getByText("Matching devices: 0")).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
    await expect(
      page.getByRole("button", { name: /select a secret to rotate/i })
    ).toBeDisabled();
  });

  test("submits the location, filters, and secret", async ({ page }) => {
    await page.getByRole("button", { name: /Select a Location/i }).click();
    await page.getByRole("dialog").getByRole("option", { name: SITES_LIST.rno1 }).click();
    const filteredOptions = page.waitForRequest((request) => {
      const url = new URL(request.url());
      return (
        url.pathname === "/v1/parameter/password-users" &&
        url.searchParams.get("location") === SITES_LIST.rno1 &&
        url.searchParams.get("tenant") === TENANT_LIST.tenant_a
      );
    });
    await page.locator("form").getByRole("button", { name: /Select a Tenant/i }).click();
    await page
      .getByRole("dialog")
      .getByRole("option", { name: TENANT_LIST.tenant_a, exact: true })
      .click();
    await page.keyboard.press("Escape");
    await filteredOptions;
    await page.getByRole("button", { name: /select a secret to rotate/i }).click();
    await page.getByRole("dialog").getByText("admin", { exact: true }).click();

    const post = nextPost(page, "/v1/workflow/ngc/site_password_rotation");
    await submit(page);
    expect((await post).postDataJSON()).toEqual({
      location: SITES_LIST.rno1,
      location_type: "Site",
      selected_secret: "admin",
      status: [STATUS_LIST.active, STATUS_LIST.provisioned],
      tenant: TENANT_LIST.tenant_a,
    });
  });
});
