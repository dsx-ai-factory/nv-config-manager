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
 * The IB PKey Member Delete generic form on its form ID route (the legacy route
 * redirects there). Its Python-declared row variants intentionally have no membership
 * column.
 */
import { expect } from "@playwright/test";
import { mockServerCatalogAndUser } from "./shared/apiMocks";
import { test, TEST_TIMEOUT, WORKFLOW_DETAILS_TIMEOUT } from "./shared/utils";

const FORM_TITLE = "New InfiniBand PKey Member Delete Workflow";
const FORM_PATH = "/workflows/new/ib-pkey-member-delete";
const ENDPOINT = "/v1/workflow/ngc/ib_pkey_member_delete";

test.describe("IB PKey Member Delete Form", () => {
  test.beforeEach(async ({ page }) => {
    await mockServerCatalogAndUser(page, ["reader", "executor"]);
    await page.goto(FORM_PATH);
  });

  test("renders form with correct title and submit label", async ({ page }) => {
    await expect(
      page.getByRole("heading", { name: FORM_TITLE }),
    ).toBeVisible({ timeout: TEST_TIMEOUT });
    await expect(
      page.getByRole("button", { name: "Remove Members" }),
    ).toBeVisible({ timeout: TEST_TIMEOUT });
  });

  test("does not render Membership Type (delete-only contract)", async ({
    page,
  }) => {
    await expect(
      page.getByRole("button", { name: /Membership Type for row/ }),
    ).toHaveCount(0);
  });

  test("submits with interfaces and omits membership_type", async ({
    page,
  }) => {
    const requestPromise = page.waitForRequest((r) =>
      r.url().includes(ENDPOINT),
    );

    await page.getByLabel("UFM Host").fill("ufm-1.lab");
    await page.getByLabel("PKey").fill("0x8001");
    await page.getByPlaceholder("device (e.g. hca01)").fill("hca01");
    await page.getByPlaceholder("interface (e.g. mlx5_0)").fill("mlx5_0");

    await page.getByRole("button", { name: "Remove Members" }).click();

    const request = await requestPromise;
    const body = JSON.parse((await request.postData()) || "{}");
    expect(body).toEqual({
      host: "ufm-1.lab",
      pkey: "0x8001",
      interfaces: [{ device: "hca01", interface: "mlx5_0" }],
    });

    await expect(
      page.getByRole("heading", { name: "Workflow Details" }),
    ).toBeVisible({ timeout: WORKFLOW_DETAILS_TIMEOUT });
  });
});
