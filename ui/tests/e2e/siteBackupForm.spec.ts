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
 * The Site Configuration Backup form on its class-name route (the legacy
 * `/workflows/sitebackupworkflow/form` redirects there): a typed Site location, Roles,
 * Device Status, and Tenant pickers, and the backup-enabled checkbox.
 *
 * Differences from the legacy page, by design: the Site's location type is submitted
 * as `site_type`; empty Roles are omitted (the server default is `[]`); `?role=` is an
 * alias of `?roles=`.
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

const PATH = formPath("SiteBackupWorkflow");
const TITLE = "New Site Configuration Backup Workflow";
const ENDPOINT = "/v1/workflow/ngc/site_backup";
const DEFAULT_STATUSES = `${STATUS_LIST.active}, ${STATUS_LIST.provisioned}`;

test.beforeEach(async ({ page }) => {
  await mockServerCatalogAndUser(page, ["reader", "executor"]);
  await mockTypedLocationsEndpoint(page);
});

test.describe("Site Backup Form", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(PATH);
    await expect(page.getByRole("heading", { name: TITLE })).toBeVisible({ timeout: TEST_TIMEOUT });
  });

  test("renders the fields with their defaults", async ({ page }) => {
    await expect(page.locator("form label")).toHaveText([
      "Site *",
      "Roles",
      "Device Status",
      "Tenant",
      "Backup enabled only",
    ]);
    await expect(selected(page, DEFAULT_STATUSES)).toBeVisible({ timeout: TEST_TIMEOUT });
    await expect(page.getByLabel("Backup enabled only")).toBeChecked();
  });

  test("reports a missing site and sends nothing", async ({ page }) => {
    const posts = recordPosts(page, ENDPOINT);
    await submit(page);
    await expect(page.getByText("Site is required", { exact: true })).toBeVisible();
    expect(posts).toEqual([]);
  });

  test("submits the site, its type, and the defaults", async ({ page }) => {
    await choose(page, SELECT_SITE, SITES_LIST.pdx01);
    const post = nextPost(page, ENDPOINT);
    await submit(page);
    expect((await post).postDataJSON()).toEqual({
      site: SITES_LIST.pdx01,
      site_type: "Site",
      status: [STATUS_LIST.active, STATUS_LIST.provisioned],
      backup_enabled_only: true,
    });
    await expectWorkflowDetails(page);
  });

  test("submits backup_enabled_only false when unchecked", async ({ page }) => {
    await choose(page, SELECT_SITE, SITES_LIST.pdx01);
    await page.getByLabel("Backup enabled only").click();
    await expect(page.getByLabel("Backup enabled only")).not.toBeChecked();

    const post = nextPost(page, ENDPOINT);
    await submit(page);
    expect((await post).postDataJSON()).toMatchObject({ backup_enabled_only: false });
  });

  test("requires at least one Device Status", async ({ page }) => {
    const posts = recordPosts(page, ENDPOINT);
    await choose(page, SELECT_SITE, SITES_LIST.pdx01);
    await page.getByRole("button", { name: `Remove ${STATUS_LIST.active}` }).click();
    await page.getByRole("button", { name: `Remove ${STATUS_LIST.provisioned}` }).click();
    await submit(page);
    await expect(page.getByText("At least 1 Device Status is required")).toBeVisible();
    expect(posts).toEqual([]);
  });

  test("shows a forbidden site in the failure toast", async ({ page }) => {
    await choose(page, SELECT_SITE, FORBIDDEN_SITE_ID);
    await submit(page);
    await expectFailureToast(page, "Forbidden: You do not have permission to run this workflow");
  });

  test("shows FastAPI 422 errors on the field and a string detail form-level", async ({ page }) => {
    let detail: unknown = [
      { type: "value_error", loc: ["body", "site_type"], msg: "Value error, not a site" },
    ];
    await page.route(`**${ENDPOINT}`, (route) => route.fulfill({ status: 422, json: { detail } }));
    await choose(page, SELECT_SITE, SITES_LIST.pdx01);
    await submit(page);
    // The hidden type sibling reports on its location field.
    await expect(page.getByText("Value error, not a site")).toBeVisible();
    await expect(formErrors(page)).toHaveCount(0);
    await noFailureToast(page);

    detail = "Backups are paused for this site";
    await submit(page);
    await expect(formErrors(page)).toContainText("Backups are paused for this site");
  });
});

test("Site Backup Form - a legacy link with ?role= prefills every field", async ({ page }) => {
  const query =
    `?site=${SITES_LIST.pdx01}&role=${ROLES_LIST.leaf}&status=${STATUS_LIST.active}` +
    `&tenant=${TENANT_LIST.tenant_a}&backup_enabled_only=false`;
  await page.goto(`/workflows/sitebackupworkflow/form${query}`);
  await expect(page).toHaveURL(`${PATH}${query}`);

  await expect(selected(page, SITES_LIST.pdx01)).toBeVisible({ timeout: TEST_TIMEOUT });
  await expect(selected(page, ROLES_LIST.leaf)).toBeVisible();
  await expect(selected(page, STATUS_LIST.active)).toBeVisible();
  await expect(selected(page, TENANT_LIST.tenant_a)).toBeVisible();
  await expect(page.getByLabel("Backup enabled only")).not.toBeChecked();

  const post = nextPost(page, ENDPOINT);
  await submit(page);
  expect((await post).postDataJSON()).toEqual({
    site: SITES_LIST.pdx01,
    site_type: "Site",
    roles: [ROLES_LIST.leaf],
    status: [STATUS_LIST.active],
    tenant: TENANT_LIST.tenant_a,
    backup_enabled_only: false,
  });
  await expectWorkflowDetails(page);
});
