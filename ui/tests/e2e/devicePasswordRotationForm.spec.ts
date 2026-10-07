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
 * The Device Password Rotation form on its class-name route (the legacy
 * `/workflows/devicepasswordrotationworkflow/form` redirects there): a device with a
 * Site filter, and the device's password users (`/v1/parameter/device/{device_id}/
 * password_users`), cleared when the device changes.
 *
 * Differences from the legacy page, by design: Submit is enabled before the form is
 * complete and a submission reports the missing inputs ("<label> is required"); links
 * may name the device with `?device-id=` (Nautobot's parameter) besides the legacy
 * `?device=`, and the secret with `?selected_secret=`.
 */
import { expect } from "@playwright/test";

import { DEVICES_LIST, FORBIDDEN_DEVICE_IDS, FORBIDDEN_SITE_ID, SITES_LIST } from "@/mocks/data";

import { mockServerCatalogAndUser } from "./shared/apiMocks";
import { test, TEST_TIMEOUT } from "./shared/utils";
import {
  choose,
  expectWorkflowDetails,
  formErrors,
  formPath,
  nextPost,
  noFailureToast,
  picker,
  recordPosts,
  reply422,
  SELECT_DEVICE,
  SELECT_SITE,
  selected,
  SITE_FIRST,
  submit,
} from "./shared/workflowFormTests";

const PATH = formPath("DevicePasswordRotationWorkflow");
const TITLE = "New Device Password Rotation Workflow";
const ENDPOINT = "/v1/workflow/ngc/device_password_rotation";
const SELECT_SECRET = "Select a Secret to Rotate...";
const SITE = SITES_LIST.rno1;
const [DEVICE] = DEVICES_LIST.RNO1;
const OTHER_DEVICE = DEVICES_LIST.RNO1[1];

test.describe("Device Password Rotation Form", () => {
  test.beforeEach(async ({ page }) => {
    await mockServerCatalogAndUser(page, ["reader", "executor"]);
    await page.goto(PATH);
    await expect(page.getByRole("heading", { name: TITLE })).toBeVisible({ timeout: TEST_TIMEOUT });
  });

  test("renders Site, Device, and Secret; the secret waits for a device", async ({ page }) => {
    await expect(page.locator("form label")).toHaveText(["Site *", "Device *", "Secret to Rotate *"]);
    await expect(picker(page, SITE_FIRST)).toBeDisabled();
    await expect(picker(page, SELECT_SECRET)).toBeDisabled();

    await choose(page, SELECT_SITE, SITE);
    await expect(picker(page, SELECT_SECRET)).toBeDisabled();
    await choose(page, SELECT_DEVICE, DEVICE.name);
    await expect(picker(page, SELECT_SECRET)).toBeEnabled();
  });

  test("reports every missing input and sends nothing", async ({ page }) => {
    const posts = recordPosts(page, ENDPOINT);
    await submit(page);
    await expect(page.getByText("Device is required", { exact: true })).toBeVisible();
    await expect(page.getByText("Secret to Rotate is required", { exact: true })).toBeVisible();
    expect(posts).toEqual([]);
  });

  test("submits the device and secret", async ({ page }) => {
    await choose(page, SELECT_SITE, SITE);
    await choose(page, SELECT_DEVICE, DEVICE.name);
    await choose(page, SELECT_SECRET, "admin");

    const post = nextPost(page, ENDPOINT);
    await submit(page);
    expect((await post).postDataJSON()).toEqual({ device_id: DEVICE.id, selected_secret: "admin" });
    await expectWorkflowDetails(page);
  });

  test("changing the device clears the secret", async ({ page }) => {
    await choose(page, SELECT_SITE, SITE);
    await choose(page, SELECT_DEVICE, DEVICE.name);
    await choose(page, SELECT_SECRET, "admin");
    await expect(selected(page, "admin")).toBeVisible();

    await selected(page, DEVICE.name).click();
    await page.getByRole("dialog").getByRole("option", { name: OTHER_DEVICE.name, exact: true }).click();
    await expect(picker(page, SELECT_SECRET)).toBeEnabled();
    await expect(selected(page, "admin")).toHaveCount(0);
  });

  test("a device whose secrets cannot be loaded says so", async ({ page }) => {
    await choose(page, SELECT_SITE, FORBIDDEN_SITE_ID);
    const forbidden = DEVICES_LIST[FORBIDDEN_SITE_ID].find(
      (device) => device.id === FORBIDDEN_DEVICE_IDS.ARISTA
    )!;
    await choose(page, SELECT_DEVICE, forbidden.name);
    await expect(page.getByText("Could not load Secret to Rotate options.")).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
  });

  test("shows a FastAPI 422 on the secret", async ({ page }) => {
    await reply422(page, ENDPOINT, [
      { type: "value_error", loc: ["body", "selected_secret"], msg: "Value error, unknown secret" },
    ]);
    await choose(page, SELECT_SITE, SITE);
    await choose(page, SELECT_DEVICE, DEVICE.name);
    await choose(page, SELECT_SECRET, "admin");
    await submit(page);

    await expect(page.getByText("Value error, unknown secret")).toBeVisible();
    await expect(formErrors(page)).toHaveCount(0);
    await noFailureToast(page);

    await selected(page, "admin").click();
    await page.getByRole("dialog").getByRole("option", { name: "cumulus", exact: true }).click();
    await expect(page.getByText("Value error, unknown secret")).toHaveCount(0);
  });
});

test.describe("Device Password Rotation Form - URL prefill", () => {
  test.beforeEach(async ({ page }) => {
    await mockServerCatalogAndUser(page, ["reader", "executor"]);
  });

  for (const param of ["device-id", "device"]) {
    test(`prefills Site, Device (?${param}=), and Secret, and submits them`, async ({ page }) => {
      const query = `?site=${SITE}&${param}=${DEVICE.id}&selected_secret=cumulus`;
      await page.goto(`/workflows/devicepasswordrotationworkflow/form${query}`);
      await expect(page).toHaveURL(`${PATH}${query}`);

      await expect(selected(page, SITE)).toBeVisible({ timeout: TEST_TIMEOUT });
      await expect(selected(page, DEVICE.name)).toBeVisible({ timeout: TEST_TIMEOUT });
      await expect(selected(page, "cumulus")).toBeVisible({ timeout: TEST_TIMEOUT });

      const post = nextPost(page, ENDPOINT);
      await submit(page);
      expect((await post).postDataJSON()).toEqual({ device_id: DEVICE.id, selected_secret: "cumulus" });
    });
  }
});
