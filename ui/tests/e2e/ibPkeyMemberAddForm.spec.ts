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
import { mockServerCatalogAndUser } from "./shared/apiMocks";
import { test, TEST_TIMEOUT, WORKFLOW_DETAILS_TIMEOUT } from "./shared/utils";

const FORM_TITLE = "New InfiniBand PKey Member Add Workflow";
const FORM_PATH = "/workflows/new/IBPKeyMemberAddWorkflow";
const ENDPOINT = "/v1/workflow/ngc/ib_pkey_member_add";

const GUID_A = "0x0011223344556677";
const GUID_B = "0x8899aabbccddeeff";

test.describe("IB PKey Member Add Form", () => {
  test.beforeEach(async ({ page }) => {
    await mockServerCatalogAndUser(page, ["executor"]);
    await page.goto(FORM_PATH);
  });

  test("renders form with correct title", async ({ page }) => {
    await expect(
      page.getByRole("heading", { name: FORM_TITLE }),
    ).toBeVisible({ timeout: TEST_TIMEOUT });
  });

  test("validates required host, pkey, and at least one member row", async ({
    page,
  }) => {
    const posts: string[] = [];
    page.on("request", (request) => {
      if (request.method() === "POST" && request.url().includes(ENDPOINT)) {
        posts.push(request.url());
      }
    });
    await page.getByRole("button", { name: "Add Members" }).click();
    await expect(page.getByText("Host is required")).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
    await expect(page.getByText("PKey is required")).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
    await expect(
      page.getByText("At least 1 row is required."),
    ).toBeVisible({ timeout: TEST_TIMEOUT });
    expect(posts).toEqual([]);
  });

  test("rejects malformed pkey inline", async ({ page }) => {
    await page.getByLabel("UFM Host").fill("ufm-1.lab");
    await page.getByLabel("PKey").fill("0xZZZZ");
    await page.getByRole("button", { name: "Add Members" }).click();
    await expect(
      page.getByText(/must match pattern/i),
    ).toBeVisible({ timeout: TEST_TIMEOUT });
  });

  test("requires a membership type per interface row", async ({ page }) => {
    await page.getByLabel("UFM Host").fill("ufm-1.lab");
    await page.getByLabel("PKey").fill("0x8001");
    await page.getByPlaceholder("device (e.g. hca01)").fill("hca01");
    await page.getByPlaceholder("interface (e.g. mlx5_0)").fill("mlx5_0");

    await page.getByRole("button", { name: "Add Members" }).click();

    await expect(page.getByText("Membership Type is required in row 1.")).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
  });

  test("submits with interfaces (happy path)", async ({ page }) => {
    const requestPromise = page.waitForRequest((r) =>
      r.url().includes(ENDPOINT),
    );

    await page.getByLabel("UFM Host").fill("ufm-1.lab");
    await page.getByLabel("PKey").fill("0x8001");
    await page.getByPlaceholder("device (e.g. hca01)").fill("hca01");
    await page.getByPlaceholder("interface (e.g. mlx5_0)").fill("mlx5_0");

    await page.getByLabel("Membership Type for row 1").click();
    await page.getByRole("option", { name: "full" }).click();

    await page.getByRole("button", { name: "Add Members" }).click();

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

  test("switches to GUIDs mode, disables interface inputs, and submits per-GUID membership", async ({
    page,
  }) => {
    const requestPromise = page.waitForRequest((r) =>
      r.url().includes(ENDPOINT),
    );

    await page.getByLabel("UFM Host").fill("ufm-1.lab");
    await page.getByLabel("PKey").fill("0x8001");

    await page.getByLabel("By GUIDs").click();

    await expect(page.getByPlaceholder("device (e.g. hca01)")).toHaveCount(0);

    await page.getByLabel("GUID 1").fill(GUID_A);
    await page.getByLabel("Membership Type for row 1").click();
    await page.getByRole("option", { name: "limited" }).click();

    await page.getByRole("button", { name: "Add Row" }).click();
    await page.getByLabel("GUID 2").fill(GUID_B);
    await page.getByLabel("Membership Type for row 2").click();
    await page.getByRole("option", { name: "full" }).click();

    await page.getByRole("button", { name: "Add Members" }).click();

    const request = await requestPromise;
    const body = JSON.parse((await request.postData()) || "{}");
    expect(body).toEqual({
      host: "ufm-1.lab",
      pkey: "0x8001",
      guids: [GUID_A, GUID_B],
      guid_memberships: ["limited", "full"],
    });
  });

  test("first GUID row starts empty after switching modes", async ({
    page,
  }) => {
    await page.getByLabel("By GUIDs").click();
    const guid = page.getByLabel("GUID 1");
    await expect(guid).toBeVisible({ timeout: TEST_TIMEOUT });
    // Regression: the interfaces array used to bleed into this field and render
    // as "[object Object]", hiding the placeholder.
    await expect(guid).toHaveValue("");
  });

  test("rejects malformed guids inline", async ({ page }) => {
    await page.getByLabel("UFM Host").fill("ufm-1.lab");
    await page.getByLabel("PKey").fill("0x8001");
    await page.getByLabel("By GUIDs").click();
    await page.getByLabel("GUID 1").fill("0xnope");
    await page.getByRole("button", { name: "Add Members" }).click();

    await expect(page.getByText("GUID in row 1 has an invalid format.")).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
  });

  test("prefills host/pkey from URL params", async ({ page }) => {
    await page.goto(`${FORM_PATH}?host=ufm-2.lab&pkey=0x0100`);

    await expect(page.getByLabel("UFM Host")).toHaveValue("ufm-2.lab");
    await expect(page.getByLabel("PKey")).toHaveValue("0x0100");
  });
});
