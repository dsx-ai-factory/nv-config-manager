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
 * Helpers for the schema-driven (RJSF) workflow forms on `/workflows/new/<ClassName>`,
 * and the shared suite of the single-device forms (a `device` core field with its
 * implicit Site/Tenant/Status filter scope, Nautobot's `?site=&device-id=` links).
 */
import { expect, type Page, type Request } from "@playwright/test";

import { DEVICES_LIST, FORBIDDEN_SITE_ID, SITES_LIST, TENANT_LIST } from "@/mocks/data";

import { mockServerCatalogAndUser } from "./apiMocks";
import { test, TEST_TIMEOUT, WORKFLOW_DETAILS_TIMEOUT } from "./utils";

type Device = (typeof DEVICES_LIST)[keyof typeof DEVICES_LIST][number];

export const formPath = (workflow: string) => `/workflows/new/${workflow}`;

/** A SelectBox trigger by its exact accessible name: the selection, or the placeholder. */
export const picker = (page: Page, name: string) =>
  page.getByRole("button", { name, exact: true });

/** A SelectBox trigger showing `label` as its selection. */
export const selected = (page: Page, label: string) => picker(page, `${label}. Open options`);

/** Open the picker named `trigger` and pick `option`. */
export const choose = async (page: Page, trigger: string, option: string) => {
  await picker(page, trigger).click();
  await page.getByRole("dialog").getByRole("option", { name: option, exact: true }).click();
  // A multi-select stays open.
  if (await page.getByRole("dialog").isVisible()) await page.keyboard.press("Escape");
};

export const SELECT_SITE = "Select a Site...";
export const SELECT_DEVICE = "Select a Device...";
export const SITE_FIRST = "Select a Site first";

/** The next POST to `endpoint`. */
export const nextPost = (page: Page, endpoint: string): Promise<Request> =>
  page.waitForRequest(
    (request) => request.method() === "POST" && new URL(request.url()).pathname === endpoint
  );

/** Collects every POST to `endpoint` (to assert that none was sent). */
export const recordPosts = (page: Page, endpoint: string): string[] => {
  const posts: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && new URL(request.url()).pathname === endpoint) {
      posts.push(request.url());
    }
  });
  return posts;
};

export const submit = (page: Page, text = "Submit") =>
  page.getByRole("button", { name: text }).click();

export const expectWorkflowDetails = (page: Page) =>
  expect(page.getByRole("heading", { name: "Workflow Details" })).toBeVisible({
    timeout: WORKFLOW_DETAILS_TIMEOUT,
  });

/** The generic failure toast (non-validation errors). */
export const expectFailureToast = async (page: Page, message: string) => {
  await expect(page.locator("div.text-sm.font-semibold", { hasText: "Workflow Failed" })).toBeVisible({
    timeout: TEST_TIMEOUT,
  });
  await expect(page.locator("div.text-sm.opacity-90", { hasText: message })).toBeVisible();
};

export const noFailureToast = (page: Page) =>
  expect(page.locator("div.text-sm.font-semibold", { hasText: "Workflow Failed" })).toHaveCount(0);

/** The form-level error box (422 errors no field owns). */
export const formErrors = (page: Page) =>
  page.getByRole("alert").filter({ hasText: "The workflow input is invalid" });

/** Answer the next POSTs to `endpoint` with a 422 and `detail`. */
export const reply422 = (page: Page, endpoint: string, detail: unknown) =>
  page.route(`**${endpoint}`, (route) =>
    route.request().method() === "POST"
      ? route.fulfill({ status: 422, json: { detail } })
      : route.fallback()
  );

/** `/v1/parameter/device` requests (not the per-device sub-resources). */
export const isDeviceOptionsRequest = (request: Request) =>
  /\/v1\/parameter\/device$/.test(new URL(request.url()).pathname);

export interface DeviceFormConfig {
  /** Workflow class name. */
  workflow: string;
  /** Legacy page slug, still linked from Nautobot; it redirects to the class-name route. */
  legacySlug: string;
  formTitle: string;
  /** Submit endpoint path. */
  endpoint: string;
  /** Payload keys besides `device_id`, e.g. a hidden `trigger` or a default. */
  extraPayload?: Record<string, unknown>;
  /** The device to use from a site's list (e.g. one of the platform the form loads). */
  deviceFilter?: (devices: readonly Device[]) => Device;
  /** The forbidden device to submit (its platform must be in the form's options). */
  forbiddenFilter?: (devices: readonly Device[]) => Device;
  /** The devices of a site the device picker lists, when the form narrows them. */
  listedDevices?: (devices: readonly Device[]) => Device[];
}

/**
 * The suite every single-device form shares. Differences from the legacy pages, by
 * design: Site, Tenant, and Status are device filters rather than workflow inputs, so
 * an empty submission reports only "Device is required" (the device picker says
 * "Select a Site first"); required labels carry a visible " *"; a failed submission
 * shows the generic "Workflow Failed" toast with the server's message; a 422 shows
 * inline. The payload holds exactly the projected inputs (no `user`, `user_domain`,
 * `workflow_id`, or empty strings).
 */
export const runWorkflowFormTests = (config: DeviceFormConfig) => {
  const {
    workflow,
    legacySlug,
    formTitle,
    endpoint,
    extraPayload = {},
    deviceFilter = (devices) => devices[0],
    forbiddenFilter = (devices) => devices[0],
    listedDevices,
  } = config;
  const path = formPath(workflow);
  const site = SITES_LIST.pdx01;
  const otherSite = SITES_LIST.rno1;
  const device = deviceFilter(DEVICES_LIST[site]);
  const otherDevice = deviceFilter(DEVICES_LIST[otherSite]);
  const secondDevice = deviceFilter(DEVICES_LIST[site].filter((item) => item.id !== device.id));
  const payload = (id: string) => ({ device_id: id, ...extraPayload });

  test.describe(`${formTitle} Form`, () => {
    test.beforeEach(async ({ page }) => {
      await mockServerCatalogAndUser(page, ["reader", "executor"]);
      await page.goto(path);
      await expect(page.getByRole("heading", { name: formTitle })).toBeVisible({
        timeout: TEST_TIMEOUT,
      });
    });

    test("renders the device and its filters; no device list before a site", async ({ page }) => {
      await expect(page.locator("form label")).toHaveText([
        "Site *",
        "Tenant (optional)",
        "Status (optional)",
        "Device *",
      ]);
      await expect(picker(page, SITE_FIRST)).toBeDisabled();
      await expect(page.getByRole("button", { name: "Submit" })).toBeEnabled();
    });

    test("reports a missing device and sends nothing", async ({ page }) => {
      const posts = recordPosts(page, endpoint);
      await submit(page);
      await expect(page.getByText("Device is required", { exact: true })).toBeVisible({
        timeout: TEST_TIMEOUT,
      });
      await expect(page.getByText("Site is required")).toHaveCount(0);

      // After the failed submit, validation is live.
      await choose(page, SELECT_SITE, site);
      await expect(page.getByText("Device is required", { exact: true })).toBeVisible();
      await choose(page, SELECT_DEVICE, device.name);
      await expect(page.getByText("Device is required", { exact: true })).toHaveCount(0);
      expect(posts).toEqual([]);
    });

    test("submits the selected device and opens the run", async ({ page }) => {
      const deviceRequest = page.waitForRequest(
        (request) =>
          isDeviceOptionsRequest(request) && new URL(request.url()).searchParams.get("site") === site
      );
      await choose(page, SELECT_SITE, site);
      await deviceRequest;
      await choose(page, SELECT_DEVICE, device.name);

      const post = nextPost(page, endpoint);
      await submit(page);
      expect((await post).postDataJSON()).toEqual(payload(device.id));
      await expectWorkflowDetails(page);
    });

    test("a site change or clear resets the device", async ({ page }) => {
      await choose(page, SELECT_SITE, site);
      await choose(page, SELECT_DEVICE, device.name);

      await selected(page, site).click();
      await page.getByRole("dialog").getByRole("option", { name: otherSite, exact: true }).click();
      await expect(picker(page, SELECT_DEVICE)).toBeVisible({ timeout: TEST_TIMEOUT });

      await choose(page, SELECT_DEVICE, otherDevice.name);
      await expect(selected(page, otherDevice.name)).toBeVisible();

      // The Site picker's clear button comes first.
      await page.getByRole("button", { name: "Clear selection" }).first().click();
      await expect(picker(page, SELECT_SITE)).toBeVisible();
      await expect(picker(page, SITE_FIRST)).toBeDisabled({ timeout: TEST_TIMEOUT });
    });

    if (listedDevices) {
      test("lists only the devices the form loads", async ({ page }) => {
        await choose(page, SELECT_SITE, site);
        await picker(page, SELECT_DEVICE).click();
        await expect(page.getByRole("dialog").getByRole("option")).toHaveText(
          listedDevices(DEVICES_LIST[site]).map((item) => item.name)
        );
      });
    }

    test("a tenant filter narrows the device request", async ({ page }) => {
      await choose(page, SELECT_SITE, site);
      const filtered = page.waitForRequest(
        (request) =>
          isDeviceOptionsRequest(request) &&
          new URL(request.url()).searchParams.getAll("tenant").includes(TENANT_LIST.tenant_a)
      );
      await choose(page, "Select Tenant (optional)...", TENANT_LIST.tenant_a);
      await filtered;
    });

    test("disables the form while submitting", async ({ page }) => {
      await page.route(`**${endpoint}`, async (route) => {
        await new Promise((resolve) => setTimeout(resolve, 500));
        await route.fallback();
      });
      await choose(page, SELECT_SITE, site);
      await choose(page, SELECT_DEVICE, device.name);
      await submit(page);

      await expect(selected(page, site)).toBeDisabled();
      await expect(selected(page, device.name)).toBeDisabled();
      await expect(page.getByRole("button", { name: "Submitting..." })).toBeDisabled();
      await expectWorkflowDetails(page);
    });
  });

  test.describe(`${formTitle} - URL prefill`, () => {
    test.beforeEach(async ({ page }) => {
      await mockServerCatalogAndUser(page, ["reader", "executor"]);
    });

    test("a Nautobot link to the legacy page prefills Site, Tenant, and Device", async ({
      page,
    }) => {
      const query = `?site=${site}&tenant=${device.tenant}&device-id=${device.id}`;
      await page.goto(`/workflows/${legacySlug}/form${query}`);
      await expect(page).toHaveURL(`${path}${query}`);

      await expect(selected(page, site)).toBeVisible({ timeout: TEST_TIMEOUT });
      await expect(selected(page, device.tenant)).toBeVisible();
      await expect(selected(page, device.name)).toBeVisible({ timeout: TEST_TIMEOUT });

      const post = nextPost(page, endpoint);
      await submit(page);
      expect((await post).postDataJSON()).toEqual(payload(device.id));
      await expectWorkflowDetails(page);
    });

    test("a prefilled device can be changed before submitting", async ({ page }) => {
      await page.goto(`${path}?site=${site}&device-id=${device.id}`);
      await expect(selected(page, device.name)).toBeVisible({ timeout: TEST_TIMEOUT });

      await selected(page, site).click();
      await page.getByRole("dialog").getByRole("option", { name: otherSite, exact: true }).click();
      await choose(page, SELECT_DEVICE, otherDevice.name);

      const post = nextPost(page, endpoint);
      await submit(page);
      expect((await post).postDataJSON()).toEqual(payload(otherDevice.id));
    });

    test("a device that is not at the linked site is dropped", async ({ page }) => {
      await page.goto(`${path}?site=${otherSite}&device-id=${device.id}`);
      await expect(selected(page, otherSite)).toBeVisible({ timeout: TEST_TIMEOUT });
      await expect(picker(page, SELECT_DEVICE)).toBeEnabled({ timeout: TEST_TIMEOUT });
      await expect(page.getByRole("button", { name: "Submit" })).toBeEnabled();
    });
  });

  test.describe(`${formTitle} - Error Scenarios`, () => {
    test.beforeEach(async ({ page }) => {
      await mockServerCatalogAndUser(page, ["reader", "executor"]);
      await page.goto(path);
    });

    test("shows a forbidden submission in the failure toast", async ({ page }) => {
      await choose(page, SELECT_SITE, FORBIDDEN_SITE_ID);
      await choose(page, SELECT_DEVICE, forbiddenFilter(DEVICES_LIST[FORBIDDEN_SITE_ID]).name);
      await submit(page);

      await expectFailureToast(page, "Forbidden: You do not have permission to run this workflow");
      await expect(page.getByRole("button", { name: "Submit" })).toBeEnabled();
    });

    test("shows FastAPI 422 errors inline: on the field, unknown locations form-level", async ({
      page,
    }) => {
      await reply422(page, endpoint, [
        { type: "value_error", loc: ["body", "device_id"], msg: "Value error, device is not managed" },
        { type: "missing", loc: ["body", "user"], msg: "Field required" },
      ]);
      await choose(page, SELECT_SITE, site);
      await choose(page, SELECT_DEVICE, device.name);
      await submit(page);

      await expect(page.getByText("Value error, device is not managed")).toBeVisible();
      await expect(formErrors(page)).toContainText("user: Field required");
      await noFailureToast(page);

      // Changing the device clears its server error.
      await selected(page, device.name).click();
      await page.getByRole("dialog").getByRole("option", { name: secondDevice.name, exact: true }).click();
      await expect(page.getByText("Value error, device is not managed")).toHaveCount(0);
      await expect(page.getByRole("button", { name: "Submit" })).toBeEnabled();
    });

    test("shows a string 422 detail as a form-level error", async ({ page }) => {
      await reply422(page, endpoint, "Device is not reachable from this site");
      await choose(page, SELECT_SITE, site);
      await choose(page, SELECT_DEVICE, device.name);
      await submit(page);

      await expect(formErrors(page)).toContainText("Device is not reachable from this site");
      await noFailureToast(page);
    });
  });
};
