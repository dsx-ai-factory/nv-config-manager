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
 * The IB PKey Member Update generic form on its class-name route. The Python form
 * declaration supplies its mutually exclusive interface/GUID row variants.
 */
import { expect } from "@playwright/test";
import { mockServerCatalogAndUser } from "./shared/apiMocks";
import { test, TEST_TIMEOUT, WORKFLOW_DETAILS_TIMEOUT } from "./shared/utils";

const FORM_TITLE = "New InfiniBand PKey Member Update Workflow";
const FORM_PATH = "/workflows/new/IBPKeyMemberUpdateWorkflow";
const ENDPOINT = "/v1/workflow/ngc/ib_pkey_member_update";

test.describe("IB PKey Member Update Form", () => {
  test.beforeEach(async ({ page }) => {
    await mockServerCatalogAndUser(page, ["reader", "executor"]);
    await page.goto(FORM_PATH);
  });

  test("renders form with destructive warning", async ({ page }) => {
    await expect(
      page.getByRole("heading", { name: FORM_TITLE }),
    ).toBeVisible({ timeout: TEST_TIMEOUT });
    await expect(
      page.getByText(/reconciles PKey membership/i),
    ).toBeVisible({ timeout: TEST_TIMEOUT });
    await expect(page.getByText(/Removals require approval/i)).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
  });

  test("submits with interfaces and per-interface membership", async ({
    page,
  }) => {
    const requestPromise = page.waitForRequest(
      (r) => r.url().includes(ENDPOINT) && r.method() === "POST",
    );

    await page.getByLabel("UFM Host").fill("ufm-1.lab");
    await page.getByLabel("PKey").fill("0x8001");
    await page.getByPlaceholder("device (e.g. hca01)").fill("hca01");
    await page.getByPlaceholder("interface (e.g. mlx5_0)").fill("mlx5_0");

    await page.getByLabel("Membership Type for row 1").click();
    await page.getByRole("option", { name: "full" }).click();

    await page.getByRole("button", { name: "Replace Members" }).click();

    const request = await requestPromise;
    const body = JSON.parse((await request.postData()) || "{}");
    expect(body).toEqual({
      host: "ufm-1.lab",
      pkey: "0x8001",
      interfaces: [{ device: "hca01", interface: "mlx5_0", membership: "full" }],
    });

    await expect(
      page.getByRole("heading", { name: "Workflow Details" }),
    ).toBeVisible({ timeout: WORKFLOW_DETAILS_TIMEOUT });
  });

  test("submits per-interface membership override", async ({ page }) => {
    const requestPromise = page.waitForRequest(
      (r) => r.url().includes(ENDPOINT) && r.method() === "POST",
    );

    await page.getByLabel("UFM Host").fill("ufm-1.lab");
    await page.getByLabel("PKey").fill("0x8001");
    await page.getByPlaceholder("device (e.g. hca01)").fill("hca01");
    await page.getByPlaceholder("interface (e.g. mlx5_0)").fill("mlx5_0");

    await page.getByLabel("Membership Type for row 1").click();
    await page.getByRole("option", { name: "limited" }).click();

    await page.getByRole("button", { name: "Replace Members" }).click();

    const request = await requestPromise;
    const body = JSON.parse((await request.postData()) || "{}");
    expect(body).toEqual({
      host: "ufm-1.lab",
      pkey: "0x8001",
      interfaces: [
        { device: "hca01", interface: "mlx5_0", membership: "limited" },
      ],
    });

    await expect(
      page.getByRole("heading", { name: "Workflow Details" }),
    ).toBeVisible({ timeout: WORKFLOW_DETAILS_TIMEOUT });
  });

  test("submits per-GUID membership", async ({ page }) => {
    const requestPromise = page.waitForRequest(
      (r) => r.url().includes(ENDPOINT) && r.method() === "POST",
    );

    await page.getByLabel("UFM Host").fill("ufm-1.lab");
    await page.getByLabel("PKey").fill("0x8001");
    await page.getByLabel("By GUIDs").click();

    await page.getByLabel("GUID 1").fill("0x0011223344556677");
    await page.getByLabel("Membership Type for row 1").click();
    await page.getByRole("option", { name: "limited" }).click();

    await page.getByRole("button", { name: "Add Row" }).click();
    await page.getByLabel("GUID 2").fill("0x8899aabbccddeeff");
    await page.getByLabel("Membership Type for row 2").click();
    await page.getByRole("option", { name: "full" }).click();

    await page.getByRole("button", { name: "Replace Members" }).click();

    const request = await requestPromise;
    const body = JSON.parse((await request.postData()) || "{}");
    expect(body).toEqual({
      host: "ufm-1.lab",
      pkey: "0x8001",
      guids: ["0x0011223344556677", "0x8899aabbccddeeff"],
      guid_memberships: ["limited", "full"],
    });

    await expect(
      page.getByRole("heading", { name: "Workflow Details" }),
    ).toBeVisible({ timeout: WORKFLOW_DETAILS_TIMEOUT });
  });
});
