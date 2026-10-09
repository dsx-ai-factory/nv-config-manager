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
 * The Multi-Configuration Deploy form on its form ID route (the legacy
 * `/workflows/multideployworkflow/form` redirects there).
 *
 * Differences from the legacy page, by design: unset optional inputs are omitted
 * rather than posted as `null`; the Location's type is submitted as `location_type`;
 * the batch-size range (1-100, a form-only constraint) reports Ajv's messages
 * ("Must be >= 1", "Must be <= 100") instead of the legacy wording; a cleared batch
 * size is omitted and the server default (10) applies.
 */
import { expect, type Page } from "@playwright/test";

import { FORBIDDEN_SITE_ID, ROLES_LIST, SITES_LIST, STATUS_LIST, TENANT_LIST } from "@/mocks/data";

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
  picker,
  recordPosts,
  selected,
  submit,
} from "./shared/workflowFormTests";

const PATH = formPath("MultiDeployWorkflow");
const TITLE = "New Multi-Configuration Deploy Workflow";
const ENDPOINT = "/v1/workflow/ngc/multi_deploy";
const batchSize = (page: Page) => page.getByRole("spinbutton", { name: "Max Batch Size" });

test.beforeEach(async ({ page }) => {
  await mockServerCatalogAndUser(page, ["reader", "executor"]);
  await mockTypedLocationsEndpoint(page);
});

test.describe("Multi-Configuration Deploy Form", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(PATH);
    await expect(page.getByRole("heading", { name: TITLE })).toBeVisible({ timeout: TEST_TIMEOUT });
  });

  test("renders the fields with their defaults", async ({ page }) => {
    await expect(page.locator("form label")).toHaveText([
      "Role *",
      "Max Batch Size",
      "Location",
      "Device Status",
      "Tenant",
      "Use commit-confirm",
    ]);
    await expect(batchSize(page)).toHaveValue("10");
    await expect(page.getByRole("checkbox", { name: "Use commit-confirm" })).toBeChecked();
  });

  test("reports a missing role only and sends nothing", async ({ page }) => {
    const posts = recordPosts(page, ENDPOINT);
    await submit(page);
    await expect(page.getByText("Role is required", { exact: true })).toBeVisible();
    await expect(page.getByText(/is required/)).toHaveCount(1);
    expect(posts).toEqual([]);
  });

  test("submits the role with the defaults only", async ({ page }) => {
    await choose(page, "Select a Role...", ROLES_LIST.leaf);
    const post = nextPost(page, ENDPOINT);
    await submit(page);
    expect((await post).postDataJSON()).toEqual({
      role: ROLES_LIST.leaf,
      max_batch_size: 10,
      commit_confirm: true,
    });
    await expectWorkflowDetails(page);
  });

  test("submits every field", async ({ page }) => {
    await choose(page, "Select a Role...", ROLES_LIST.spine);
    await batchSize(page).fill("25");
    await choose(page, "Select a Location...", SITES_LIST.rno1);
    await picker(page, "Select Device Status...").click();
    await page.getByRole("dialog").getByRole("option", { name: STATUS_LIST.active, exact: true }).click();
    await page.getByRole("dialog").getByRole("option", { name: STATUS_LIST.planned, exact: true }).click();
    await page.keyboard.press("Escape");
    await choose(page, "Select a Tenant...", TENANT_LIST.tenant_a);
    await page.getByRole("checkbox", { name: "Use commit-confirm" }).click();

    const post = nextPost(page, ENDPOINT);
    await submit(page);
    expect((await post).postDataJSON()).toEqual({
      role: ROLES_LIST.spine,
      max_batch_size: 25,
      location: SITES_LIST.rno1,
      location_type: "Site",
      status: [STATUS_LIST.active, STATUS_LIST.planned],
      tenant: TENANT_LIST.tenant_a,
      commit_confirm: false,
    });
  });

  test("validates the batch size range and omits a cleared batch size", async ({ page }) => {
    const posts = recordPosts(page, ENDPOINT);
    await choose(page, "Select a Role...", ROLES_LIST.leaf);
    await batchSize(page).fill("0");
    await submit(page);
    await expect(page.getByText("Must be >= 1")).toBeVisible();
    await batchSize(page).fill("101");
    await expect(page.getByText("Must be <= 100")).toBeVisible();
    expect(posts).toEqual([]);

    await batchSize(page).fill("");
    const post = nextPost(page, ENDPOINT);
    await submit(page);
    expect((await post).postDataJSON()).toEqual({ role: ROLES_LIST.leaf, commit_confirm: true });
  });

  test("shows a forbidden location in the failure toast", async ({ page }) => {
    await choose(page, "Select a Role...", ROLES_LIST.leaf);
    await choose(page, "Select a Location...", FORBIDDEN_SITE_ID);
    await submit(page);
    await expectFailureToast(page, "Forbidden: You do not have permission to run this workflow");
  });

  test("shows FastAPI 422 errors on the field and a string detail form-level", async ({ page }) => {
    let detail: unknown = [
      { type: "value_error", loc: ["body", "max_batch_size"], msg: "Value error, too many devices" },
      { type: "value_error", loc: ["body"], msg: "Value error, no devices match" },
    ];
    await page.route(`**${ENDPOINT}`, (route) => route.fulfill({ status: 422, json: { detail } }));
    await choose(page, "Select a Role...", ROLES_LIST.leaf);
    await submit(page);
    await expect(page.getByText("Value error, too many devices")).toBeVisible();
    await expect(formErrors(page)).toContainText("Value error, no devices match");
    await noFailureToast(page);

    // Correcting the field clears its error.
    await batchSize(page).fill("5");
    await expect(page.getByText("Value error, too many devices")).toHaveCount(0);

    detail = "Role cin-leaf has no deployable devices";
    await submit(page);
    await expect(formErrors(page)).toContainText("Role cin-leaf has no deployable devices");
  });
});

test.describe("Multi-Configuration Deploy Form - URL prefill", () => {
  test("a legacy link prefills every field and submits them", async ({ page }) => {
    const query =
      `?role=${ROLES_LIST.spine}&max_batch_size=15&location=${SITES_LIST.rno1}` +
      `&status=${STATUS_LIST.active}&status=${STATUS_LIST.provisioning}` +
      `&tenant=${TENANT_LIST.tenant_a}&commit_confirm=false`;
    await page.goto(`/workflows/multideployworkflow/form${query}`);
    await expect(page).toHaveURL(`${PATH}${query}`);

    await expect(selected(page, ROLES_LIST.spine)).toBeVisible({ timeout: TEST_TIMEOUT });
    await expect(batchSize(page)).toHaveValue("15");
    await expect(selected(page, SITES_LIST.rno1)).toBeVisible();
    await expect(selected(page, `${STATUS_LIST.active}, ${STATUS_LIST.provisioning}`)).toBeVisible();
    await expect(selected(page, TENANT_LIST.tenant_a)).toBeVisible();
    await expect(page.getByRole("checkbox", { name: "Use commit-confirm" })).not.toBeChecked();

    const post = nextPost(page, ENDPOINT);
    await submit(page);
    expect((await post).postDataJSON()).toEqual({
      role: ROLES_LIST.spine,
      max_batch_size: 15,
      location: SITES_LIST.rno1,
      location_type: "Site",
      status: [STATUS_LIST.active, STATUS_LIST.provisioning],
      tenant: TENANT_LIST.tenant_a,
      commit_confirm: false,
    });
    await expectWorkflowDetails(page);
  });

  test("a location named in the URL submits its ID", async ({ page }) => {
    await page.route("**/v1/parameter/location*", (route) =>
      route.fulfill({
        status: 200,
        json: [{ id: "location-sjc01-id", name: "SJC01", location_type: "Site" }],
      })
    );
    await page.goto(`${PATH}?role=${ROLES_LIST.leaf}&location=SJC01`);
    await expect(selected(page, "SJC01")).toBeVisible({ timeout: TEST_TIMEOUT });

    const post = nextPost(page, ENDPOINT);
    await submit(page);
    expect((await post).postDataJSON()).toMatchObject({
      location: "location-sjc01-id",
      location_type: "Site",
    });
  });

  test("an out-of-range batch size from the URL is reported on submit", async ({ page }) => {
    await page.goto(`${PATH}?role=${ROLES_LIST.leaf}&max_batch_size=500`);
    await expect(selected(page, ROLES_LIST.leaf)).toBeVisible({ timeout: TEST_TIMEOUT });
    await submit(page);
    await expect(page.getByText("Must be <= 100")).toBeVisible();
  });
});
