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
 * The Cumulus Hardware Validation form (`ValidateHardwareWorkflow`) on its class-name
 * route (the legacy `/workflows/cumulushardwarevalidationworkflow/form` redirects
 * there).
 *
 * Differences from the legacy page, by design: it no longer posts
 * `device_type_ids: []` and `raise_for_invalid: false` (not form inputs; the model
 * defaults are the same values); the Site's location type is submitted as `site_type`;
 * empty Roles are omitted (default `[]`); `?role=` is an alias of `?roles=`.
 */
import { expect } from "@playwright/test";

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
  recordPosts,
  SELECT_SITE,
  selected,
  submit,
} from "./shared/workflowFormTests";

const PATH = formPath("ValidateHardwareWorkflow");
const TITLE = "New Cumulus Hardware Validation Workflow";
const ENDPOINT = "/v1/workflow/ngc/cumulus_hardware_validation";
const DEFAULT_STATUSES = `${STATUS_LIST.active}, ${STATUS_LIST.provisioned}`;

test.beforeEach(async ({ page }) => {
  await mockServerCatalogAndUser(page, ["reader", "executor"]);
  await mockTypedLocationsEndpoint(page);
});

test.describe("Cumulus Hardware Validation Form", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(PATH);
    await expect(page.getByRole("heading", { name: TITLE })).toBeVisible({ timeout: TEST_TIMEOUT });
  });

  test("renders the fields with the default statuses", async ({ page }) => {
    await expect(page.locator("form label")).toHaveText(["Site *", "Roles", "Device Status", "Tenant"]);
    await expect(selected(page, DEFAULT_STATUSES)).toBeVisible({ timeout: TEST_TIMEOUT });
  });

  test("reports a missing site only and sends nothing", async ({ page }) => {
    const posts = recordPosts(page, ENDPOINT);
    await submit(page);
    await expect(page.getByText("Site is required", { exact: true })).toBeVisible();
    await expect(page.getByText(/is required/)).toHaveCount(1);
    expect(posts).toEqual([]);
  });

  test("submits several roles and statuses picked by hand", async ({ page }) => {
    await choose(page, SELECT_SITE, SITES_LIST.pdx01);
    await page.getByRole("button", { name: "Select Roles...", exact: true }).click();
    await page.getByRole("dialog").getByRole("option", { name: ROLES_LIST.leaf, exact: true }).click();
    await page.getByRole("dialog").getByRole("option", { name: ROLES_LIST.spine, exact: true }).click();
    await page.keyboard.press("Escape");
    await selected(page, DEFAULT_STATUSES).click();
    await page.getByRole("dialog").getByRole("option", { name: STATUS_LIST.planned, exact: true }).click();
    await page.keyboard.press("Escape");
    await choose(page, "Select a Tenant...", TENANT_LIST.tenant_a);

    const post = nextPost(page, ENDPOINT);
    await submit(page);
    expect((await post).postDataJSON()).toEqual({
      site: SITES_LIST.pdx01,
      site_type: "Site",
      roles: [ROLES_LIST.leaf, ROLES_LIST.spine],
      status: [STATUS_LIST.active, STATUS_LIST.provisioned, STATUS_LIST.planned],
      tenant: TENANT_LIST.tenant_a,
    });
    await expectWorkflowDetails(page);
  });

  test("omits cleared roles and tenant", async ({ page }) => {
    await page.goto(`${PATH}?site=${SITES_LIST.pdx01}&roles=${ROLES_LIST.leaf}&tenant=${TENANT_LIST.ngc}`);
    await expect(selected(page, ROLES_LIST.leaf)).toBeVisible({ timeout: TEST_TIMEOUT });
    await page.getByRole("button", { name: `Remove ${ROLES_LIST.leaf}` }).click();
    // The Tenant picker's clear button comes last.
    await page.getByRole("button", { name: "Clear selection" }).last().click();
    await expect(selected(page, TENANT_LIST.ngc)).toHaveCount(0);

    const post = nextPost(page, ENDPOINT);
    await submit(page);
    expect((await post).postDataJSON()).toEqual({
      site: SITES_LIST.pdx01,
      site_type: "Site",
      status: [STATUS_LIST.active, STATUS_LIST.provisioned],
    });
  });

  test("shows a forbidden site in the failure toast", async ({ page }) => {
    await choose(page, SELECT_SITE, FORBIDDEN_SITE_ID);
    await submit(page);
    await expectFailureToast(page, "Forbidden: You do not have permission to run this workflow");
  });

  test("shows FastAPI 422 errors on the field and a string detail form-level", async ({ page }) => {
    let detail: unknown = [
      { type: "value_error", loc: ["body", "tenant"], msg: "Value error, unknown tenant" },
    ];
    await page.route(`**${ENDPOINT}`, (route) => route.fulfill({ status: 422, json: { detail } }));
    await choose(page, SELECT_SITE, SITES_LIST.pdx01);
    await choose(page, "Select a Tenant...", TENANT_LIST.tenant_a);
    await submit(page);
    await expect(page.getByText("Value error, unknown tenant")).toBeVisible();
    await noFailureToast(page);

    detail = "No Cumulus devices at this site";
    await submit(page);
    await expect(formErrors(page)).toContainText("No Cumulus devices at this site");
  });
});

test("Cumulus Hardware Validation Form - a legacy link with repeated ?role= and ?status= prefills them", async ({
  page,
}) => {
  const query =
    `?site=${SITES_LIST.pdx01}&role=${ROLES_LIST.leaf}&role=${ROLES_LIST.spine}` +
    `&status=${STATUS_LIST.active}&status=${STATUS_LIST.planned}&tenant=${TENANT_LIST.tenant_a}`;
  await page.goto(`/workflows/cumulushardwarevalidationworkflow/form${query}`);
  await expect(page).toHaveURL(`${PATH}${query}`);

  await expect(selected(page, SITES_LIST.pdx01)).toBeVisible({ timeout: TEST_TIMEOUT });
  await expect(selected(page, `${ROLES_LIST.leaf}, ${ROLES_LIST.spine}`)).toBeVisible();
  await expect(selected(page, `${STATUS_LIST.active}, ${STATUS_LIST.planned}`)).toBeVisible();

  const post = nextPost(page, ENDPOINT);
  await submit(page);
  expect((await post).postDataJSON()).toEqual({
    site: SITES_LIST.pdx01,
    site_type: "Site",
    roles: [ROLES_LIST.leaf, ROLES_LIST.spine],
    status: [STATUS_LIST.active, STATUS_LIST.planned],
    tenant: TENANT_LIST.tenant_a,
  });
  await expectWorkflowDetails(page);
});
