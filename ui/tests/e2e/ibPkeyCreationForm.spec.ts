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
 * The IB PKey Creation form, rendered by the RJSF form on its form ID route because
 * the legacy `/workflows/ibpkeycreationworkflow/form` redirects there. Its wording
 * comes from the server's `ui_schema` (titles, placeholders, `ui:help`, submit text,
 * hidden schema descriptions).
 *
 * The legacy page's value-dependent PKey hint is replaced by static `ui:help` (plan
 * section 17): the help never changes with the value, and PKey format errors come
 * from the server (its canonicalization 422 shows inline as a form-level error).
 */
import { expect } from "@playwright/test";
import { mockServerCatalogAndUser } from "./shared/apiMocks";
import { test, TEST_TIMEOUT, WORKFLOW_DETAILS_TIMEOUT } from "./shared/utils";

const FORM_TITLE = "New InfiniBand PKey Creation Workflow";
const FORM_PATH = "/workflows/new/ib-pkey-creation";
const ENDPOINT = "/v1/workflow/ngc/ib_pkey_creation";
const SUBMIT = "Create PKey";
const PKEY_HELP =
  "Leave blank to auto-assign the next free PKey; otherwise use 0x followed by 1-4 " +
  "hexadecimal digits, for example 0x8001.";

test.describe("IB PKey Creation Form", () => {
  test.beforeEach(async ({ page }) => {
    await mockServerCatalogAndUser(page, ["reader", "executor"]);
    await page.goto(FORM_PATH);
  });

  test("renders form with correct title", async ({ page }) => {
    await expect(
      page.getByRole("heading", { name: FORM_TITLE }),
    ).toBeVisible({ timeout: TEST_TIMEOUT });
  });

  test("shows only UFM Host and PKey, in that order", async ({ page }) => {
    await expect(page.locator("form label")).toHaveText([
      "UFM Host *",
      "PKey (optional)",
    ]);
    await expect(page.getByLabel("UFM Host")).toHaveAttribute(
      "placeholder",
      "ufm.example.com",
    );
    await expect(page.getByLabel("PKey (optional)")).toHaveAttribute(
      "placeholder",
      "0x8001 (leave blank to auto-assign)",
    );
    // No schema descriptions, only the static PKey help.
    await expect(page.locator("form p")).toHaveText([PKEY_HELP]);
    await expect(page.getByRole("button", { name: SUBMIT })).toBeEnabled();
  });

  test("requires host before submit", async ({ page }) => {
    const posts: string[] = [];
    page.on("request", (r) => {
      if (r.url().includes(ENDPOINT)) posts.push(r.url());
    });
    await page.getByRole("button", { name: SUBMIT }).click();
    await expect(page.getByText("UFM Host is required", { exact: true })).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
    // Whitespace only is still missing.
    await page.getByLabel("UFM Host").fill("   ");
    await page.getByRole("button", { name: SUBMIT }).click();
    await expect(page.getByText("UFM Host is required", { exact: true })).toBeVisible();
    expect(posts).toEqual([]);

    // Validation is live after the failed submit.
    await page.getByLabel("UFM Host").fill("ufm-1.lab");
    await expect(page.getByText("UFM Host is required", { exact: true })).toHaveCount(0);
  });

  test("prefills from URL params and submits with them", async ({ page }) => {
    const requestPromise = page.waitForRequest((r) =>
      r.url().includes(ENDPOINT),
    );

    await page.goto(`${FORM_PATH}?host=ufm.example.com&pkey=0x0100`);

    await expect(page.getByLabel("UFM Host")).toHaveValue("ufm.example.com");
    await expect(page.getByLabel("PKey (optional)")).toHaveValue("0x0100");

    await page.getByRole("button", { name: SUBMIT }).click();

    const request = await requestPromise;
    const body = JSON.parse((await request.postData()) || "{}");
    expect(body).toEqual({
      host: "ufm.example.com",
      pkey: "0x0100",
    });

    await expect(
      page.getByRole("heading", { name: "Workflow Details" }),
    ).toBeVisible({ timeout: WORKFLOW_DETAILS_TIMEOUT });
  });

  test("omits empty optional fields and trims values in the request body", async ({
    page,
  }) => {
    const requestPromise = page.waitForRequest((r) =>
      r.url().includes(ENDPOINT),
    );

    await page.getByLabel("UFM Host").fill("  ufm-1.lab ");
    await page.getByLabel("PKey (optional)").fill("   ");
    await page.getByRole("button", { name: SUBMIT }).click();

    const request = await requestPromise;
    const body = JSON.parse((await request.postData()) || "{}");
    expect(body).toEqual({ host: "ufm-1.lab" });

    await expect(
      page.getByRole("heading", { name: "Workflow Details" }),
    ).toBeVisible({ timeout: WORKFLOW_DETAILS_TIMEOUT });
  });

  test("shows the static PKey help for any value and the server's canonicalization error inline", async ({
    page,
  }) => {
    // `canonicalize_input` failures answer 422 with a string detail.
    const DETAIL = "Invalid PKey 'not-a-pkey': expected 0x followed by 1-4 hex digits";
    await page.route(`**${ENDPOINT}`, (route) =>
      route.fulfill({ status: 422, json: { detail: DETAIL } }),
    );

    await expect(page.getByText(PKEY_HELP)).toBeVisible({ timeout: TEST_TIMEOUT });
    await page.getByLabel("UFM Host").fill("ufm-1.lab");
    await page.getByLabel("PKey (optional)").fill("0x8001");
    await expect(page.getByText(PKEY_HELP)).toBeVisible();
    // No client-side pattern: the server decides.
    await page.getByLabel("PKey (optional)").fill("not-a-pkey");
    await expect(page.getByText(PKEY_HELP)).toBeVisible();

    const submitted = page.waitForRequest((r) => r.url().includes(ENDPOINT));
    await page.getByRole("button", { name: SUBMIT }).click();
    expect((await submitted).postDataJSON()).toEqual({ host: "ufm-1.lab", pkey: "not-a-pkey" });

    await expect(
      page.getByRole("alert").filter({ hasText: "The workflow input is invalid" }),
    ).toContainText(DETAIL);
    await expect(
      page.locator("div.text-sm.font-semibold", { hasText: "Workflow Failed" }),
    ).toHaveCount(0);
    await expect(page.getByRole("button", { name: SUBMIT })).toBeEnabled();

    // Correcting the input clears the form-level server error.
    await page.getByLabel("PKey (optional)").fill("0x8001");
    await expect(page.getByText(DETAIL)).toHaveCount(0);
  });
});
