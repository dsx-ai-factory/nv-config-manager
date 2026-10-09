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
 * Differences from the legacy page, by design: Submit is enabled before the form is
 * complete and reports "<label> is required"; the UFM device may be linked with
 * `?ufm_device_id=` besides the legacy `?device=`; a failed submission shows the
 * generic toast; a 422 shows inline.
 */
import { expect } from "@playwright/test";

import { DEVICES_LIST, FORBIDDEN_DEVICE_IDS, FORBIDDEN_SITE_ID, SITES_LIST } from "@/mocks/data";

import { mockServerCatalogAndUser } from "./shared/apiMocks";
import { test, TEST_TIMEOUT } from "./shared/utils";
import {
  choose,
  expectFailureToast,
  expectWorkflowDetails,
  formErrors,
  formPath,
  isDeviceOptionsRequest,
  nextPost,
  noFailureToast,
  picker,
  recordPosts,
  SELECT_DEVICE,
  SELECT_SITE,
  selected,
  SITE_FIRST,
  submit,
} from "./shared/workflowFormTests";

const PATH = formPath("InfinibandCableValidationWorkflow");
const TITLE = "New InfiniBand Cable Validation Workflow";
const ENDPOINT = "/v1/workflow/ngc/infiniband_cable_validation";
const SELECT_SWITCHES = "Select Device IDs...";
const SITE = SITES_LIST.pdx01;
const UFM = DEVICES_LIST[SITE].filter((device) => device.platform === "UFM");
const MLNX = DEVICES_LIST[SITE].filter((device) => device.platform === "MLNX-OS");

const pickSwitches = async (page: import("@playwright/test").Page, ...names: string[]) => {
  await picker(page, SELECT_SWITCHES).click();
  for (const name of names) {
    await page.getByRole("dialog").getByRole("option", { name, exact: true }).click();
  }
  await page.keyboard.press("Escape");
};

test.beforeEach(async ({ page }) => {
  await mockServerCatalogAndUser(page, ["reader", "executor"]);
});

test.describe("InfiniBand Cable Validation Form", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(PATH);
    await expect(page.getByRole("heading", { name: TITLE })).toBeVisible({ timeout: TEST_TIMEOUT });
  });

  test("renders one Site for both device pickers", async ({ page }) => {
    await expect(page.locator("form label")).toHaveText(["Site *", "Device *", "Device IDs *"]);
    await expect(picker(page, SITE_FIRST)).toHaveCount(2);
  });

  test("reports both missing device inputs and sends nothing", async ({ page }) => {
    const posts = recordPosts(page, ENDPOINT);
    await submit(page);
    await expect(page.getByText("Device is required", { exact: true })).toBeVisible();
    await expect(page.getByText("Device IDs is required", { exact: true })).toBeVisible();
    expect(posts).toEqual([]);
  });

  test("lists UFM devices and MLNX-OS switches of the Site, and submits several switches", async ({
    page,
  }) => {
    const requests = page.waitForRequest(
      (request) =>
        isDeviceOptionsRequest(request) &&
        new URL(request.url()).searchParams.get("platform") === "MLNX-OS" &&
        new URL(request.url()).searchParams.get("site") === SITE
    );
    await choose(page, SELECT_SITE, SITE);
    await requests;

    await picker(page, SELECT_DEVICE).click();
    await expect(page.getByRole("dialog").getByRole("option")).toHaveText(UFM.map((d) => d.name));
    await page.getByRole("dialog").getByRole("option", { name: UFM[0].name, exact: true }).click();

    await picker(page, SELECT_SWITCHES).click();
    await expect(page.getByRole("dialog").getByRole("option")).toHaveText(MLNX.map((d) => d.name));
    await page.keyboard.press("Escape");
    await pickSwitches(page, MLNX[0].name, MLNX[1].name);

    const post = nextPost(page, ENDPOINT);
    await submit(page);
    expect((await post).postDataJSON()).toEqual({
      ufm_device_id: UFM[0].id,
      switch_device_ids: [MLNX[0].id, MLNX[1].id],
    });
    await expectWorkflowDetails(page);
  });

  test("a Site change clears both device inputs", async ({ page }) => {
    await choose(page, SELECT_SITE, SITE);
    await choose(page, SELECT_DEVICE, UFM[0].name);
    await pickSwitches(page, MLNX[0].name);

    await selected(page, SITE).click();
    await page.getByRole("dialog").getByRole("option", { name: SITES_LIST.rno1, exact: true }).click();
    await expect(picker(page, SELECT_DEVICE)).toBeVisible({ timeout: TEST_TIMEOUT });
    await expect(picker(page, SELECT_SWITCHES)).toBeVisible();

    await page.getByRole("button", { name: "Clear selection" }).first().click();
    await expect(picker(page, SITE_FIRST)).toHaveCount(2);
  });

  test("shows a forbidden switch in the failure toast", async ({ page }) => {
    const forbiddenUfm = DEVICES_LIST[FORBIDDEN_SITE_ID].find((d) => d.platform === "UFM")!;
    const forbiddenSwitch = DEVICES_LIST[FORBIDDEN_SITE_ID].find(
      (d) => d.id === FORBIDDEN_DEVICE_IDS.MLNX
    )!;
    await choose(page, SELECT_SITE, FORBIDDEN_SITE_ID);
    await choose(page, SELECT_DEVICE, forbiddenUfm.name);
    await pickSwitches(page, forbiddenSwitch.name);
    await submit(page);
    await expectFailureToast(page, "Forbidden: You do not have permission");
  });

  test("shows a 422 on a switch item inline", async ({ page }) => {
    await page.route(`**${ENDPOINT}`, (route) =>
      route.fulfill({
        status: 422,
        json: {
          detail: [
            { type: "value_error", loc: ["body", "switch_device_ids", 1], msg: "Value error, not cabled" },
          ],
        },
      })
    );
    await choose(page, SELECT_SITE, SITE);
    await choose(page, SELECT_DEVICE, UFM[0].name);
    await pickSwitches(page, MLNX[0].name, MLNX[1].name);
    await submit(page);
    await expect(page.getByText("Item 2: Value error, not cabled")).toBeVisible();
    await expect(formErrors(page)).toHaveCount(0);
    await noFailureToast(page);
  });
});

test.describe("InfiniBand Cable Validation Form - URL prefill", () => {
  for (const param of ["device", "ufm_device_id"]) {
    test(`?site=, ?${param}=, and ?device-id= prefill the form`, async ({ page }) => {
      const query = `?site=${SITE}&${param}=${UFM[0].id}&device-id=${MLNX[0].id}&device-id=${MLNX[2].id}`;
      await page.goto(`/workflows/infinibandcablevalidationworkflow/form${query}`);
      await expect(page).toHaveURL(`${PATH}${query}`);

      await expect(selected(page, SITE)).toBeVisible({ timeout: TEST_TIMEOUT });
      await expect(selected(page, UFM[0].name)).toBeVisible({ timeout: TEST_TIMEOUT });
      await expect(selected(page, `${MLNX[0].name}, ${MLNX[2].name}`)).toBeVisible();

      const post = nextPost(page, ENDPOINT);
      await submit(page);
      expect((await post).postDataJSON()).toEqual({
        ufm_device_id: UFM[0].id,
        switch_device_ids: [MLNX[0].id, MLNX[2].id],
      });
      await expectWorkflowDetails(page);
    });
  }

  test("devices absent from the linked Site are dropped", async ({ page }) => {
    await page.goto(`${PATH}?site=${SITES_LIST.rno1}&device=${UFM[0].id}&device-id=${MLNX[2].id}`);
    await expect(selected(page, SITES_LIST.rno1)).toBeVisible({ timeout: TEST_TIMEOUT });
    await expect(picker(page, SELECT_DEVICE)).toBeEnabled({ timeout: TEST_TIMEOUT });
    await expect(picker(page, SELECT_SWITCHES)).toBeEnabled();
  });
});
