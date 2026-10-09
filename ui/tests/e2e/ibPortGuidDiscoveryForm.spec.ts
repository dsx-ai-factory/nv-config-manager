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
 * The InfiniBand Port GUID Discovery form on its form ID route (the legacy
 * `/workflows/ibportguiddiscoveryworkflow/form` redirects there): a UFM device and the
 * switches to update, sharing one Site filter (the "fabric-devices" scope), and a
 * dry-run checkbox on by default. Like the legacy page, only `?site=` prefills.
 */
import { expect } from "@playwright/test";

import { DEVICES_LIST, FORBIDDEN_SITE_ID, SITES_LIST } from "@/mocks/data";

import { mockServerCatalogAndUser } from "./shared/apiMocks";
import { test, TEST_TIMEOUT } from "./shared/utils";
import {
  choose,
  expectFailureToast,
  expectWorkflowDetails,
  formErrors,
  formPath,
  nextPost,
  picker,
  recordPosts,
  SELECT_SITE,
  selected,
  SITE_FIRST,
  submit,
} from "./shared/workflowFormTests";

const PATH = formPath("IBPortGuidDiscoveryWorkflow");
const TITLE = "New InfiniBand Port GUID Discovery Workflow";
const ENDPOINT = "/v1/workflow/ngc/ib_port_guid_discovery";
const SELECT_UFM = "Select a UFM Device...";
const SELECT_SWITCHES = "Select Switch Devices...";
const SITE = SITES_LIST.pdx01;
const UFM = DEVICES_LIST[SITE].filter((device) => "role" in device && device.role === "UFM");
const SWITCHES = DEVICES_LIST[SITE].filter((device) => device.platform === "MLNX-OS");

test.beforeEach(async ({ page }) => {
  await mockServerCatalogAndUser(page, ["reader", "executor"]);
  await page.goto(PATH);
  await expect(page.getByRole("heading", { name: TITLE })).toBeVisible({ timeout: TEST_TIMEOUT });
});

test("renders one Site for both device pickers, and dry run on", async ({ page }) => {
  await expect(page.locator("form label")).toHaveText([
    "Site *",
    "UFM Device *",
    "Switch Devices *",
    "Dry run",
  ]);
  await expect(picker(page, SITE_FIRST)).toHaveCount(2);
  await expect(page.getByRole("checkbox", { name: "Dry run" })).toBeChecked();
});

test("reports both missing device inputs and sends nothing", async ({ page }) => {
  const posts = recordPosts(page, ENDPOINT);
  await submit(page);
  await expect(page.getByText("UFM Device is required", { exact: true })).toBeVisible();
  await expect(page.getByText("Switch Devices is required", { exact: true })).toBeVisible();
  expect(posts).toEqual([]);
});

test("submits the UFM device, switches, and dry run", async ({ page }) => {
  await choose(page, SELECT_SITE, SITE);
  await picker(page, SELECT_UFM).click();
  await expect(page.getByRole("dialog").getByRole("option")).toHaveText(UFM.map((d) => d.name));
  await page.getByRole("dialog").getByRole("option", { name: UFM[0].name, exact: true }).click();
  await picker(page, SELECT_SWITCHES).click();
  await page.getByRole("dialog").getByRole("option", { name: SWITCHES[0].name, exact: true }).click();
  await page.getByRole("dialog").getByRole("option", { name: SWITCHES[1].name, exact: true }).click();
  await page.keyboard.press("Escape");
  await page.getByRole("checkbox", { name: "Dry run" }).click();

  const post = nextPost(page, ENDPOINT);
  await submit(page);
  expect((await post).postDataJSON()).toEqual({
    ufm_device_id: UFM[0].id,
    switch_device_ids: [SWITCHES[0].id, SWITCHES[1].id],
    dry_run: false,
  });
  await expectWorkflowDetails(page);
});

test("?site= prefills the shared Site filter", async ({ page }) => {
  await page.goto(`/workflows/ibportguiddiscoveryworkflow/form?site=${SITE}`);
  await expect(page).toHaveURL(`${PATH}?site=${SITE}`);
  await expect(selected(page, SITE)).toBeVisible({ timeout: TEST_TIMEOUT });
  await expect(picker(page, SELECT_UFM)).toBeEnabled({ timeout: TEST_TIMEOUT });
  await expect(picker(page, SELECT_SWITCHES)).toBeEnabled();
});

test("shows a forbidden UFM device in the failure toast and a string 422 form-level", async ({
  page,
}) => {
  const forbiddenUfm = DEVICES_LIST[FORBIDDEN_SITE_ID].find((d) => d.platform === "UFM")!;
  await choose(page, SELECT_SITE, FORBIDDEN_SITE_ID);
  await choose(page, SELECT_UFM, forbiddenUfm.name);
  await picker(page, SELECT_SWITCHES).click();
  await page.getByRole("dialog").getByRole("option").first().click();
  await page.keyboard.press("Escape");
  await submit(page);
  await expectFailureToast(page, "Forbidden: You do not have permission to run this workflow");

  await page.route(`**${ENDPOINT}`, (route) =>
    route.fulfill({ status: 422, json: { detail: "UFM device is not reachable" } })
  );
  await submit(page);
  await expect(formErrors(page)).toContainText("UFM device is not reachable");
});
