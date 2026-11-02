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
 * The workflow-scoped option provider returns the selected platforms' command catalog.
 */
import { expect, type Page } from "@playwright/test";

import { DEVICES_LIST, SITES_LIST } from "@/mocks/data";

import {
  DIAGNOSTICS_COMMANDS,
  mockServerCatalogAndUser,
  mockTypedLocationsEndpoint,
} from "./shared/apiMocks";
import { test, TEST_TIMEOUT } from "./shared/utils";
import {
  formPath,
  nextPost,
  recordPosts,
  submit,
} from "./shared/workflowFormTests";

const PATH = formPath("DiagnosticsWorkflow");
const TITLE = "New Device Diagnostics Workflow";
const ENDPOINT = "/v1/workflow/ngc/diagnostics";
const [DEVICE, , SECOND_DEVICE] = DEVICES_LIST.PDX01;
const THIRD_DEVICE = DEVICES_LIST.PDX01.find(
  ({ platform }) => platform === "UFM"
)!;

const pickDevices = async (page: Page, ...names: string[]) => {
  await page
    .locator("form")
    .getByRole("combobox", { name: "Select Devices..." })
    .click();
  for (const name of names) {
    await page
      .getByRole("dialog")
      .getByRole("option", { name, exact: true })
      .click();
  }
  await page.keyboard.press("Escape");
};

test.beforeEach(async ({ page }) => {
  await mockServerCatalogAndUser(page, ["reader", "executor"]);
  await mockTypedLocationsEndpoint(page);
});

test("a legacy link opens the generic form on the form ID route", async ({
  page,
}) => {
  await page.goto("/workflows/diagnosticsworkflow/form");
  await expect(page).toHaveURL(PATH);
  await expect(page.getByRole("heading", { name: TITLE })).toBeVisible({
    timeout: TEST_TIMEOUT,
  });
  await expect(
    page.getByText("Select devices above to see available commands.")
  ).toBeVisible();
  await expect(
    page.getByText("Select the required fields to load Commands.")
  ).toHaveCount(0);
});

test("requires a device and a command", async ({ page }) => {
  await page.goto(PATH);
  const posts = recordPosts(page, ENDPOINT);
  await submit(page);
  await expect(page.getByText("Site is required", { exact: true })).toBeVisible({
    timeout: TEST_TIMEOUT,
  });
  await expect(page.getByText("Devices is required")).toBeVisible({
    timeout: TEST_TIMEOUT,
  });
  await expect(page.getByText("Commands is required")).toBeVisible({
    timeout: TEST_TIMEOUT,
  });
  expect(posts).toEqual([]);
});

test("submits the devices, commands, and ticket", async ({ page }) => {
  const deviceRequests: URL[] = [];
  const commandRequests: URL[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/v1/parameter/device") deviceRequests.push(url);
    if (
      url.pathname ===
      "/v1/workflow/diagnostics/form-options/diagnostic-commands"
    ) {
      commandRequests.push(url);
    }
  });

  await page.goto(PATH);
  await expect(page.getByRole("heading", { name: TITLE })).toBeVisible({
    timeout: TEST_TIMEOUT,
  });
  await expect(
    page.getByText("Select devices above to see available commands.")
  ).toBeVisible();
  await expect(
    page.getByText("Select the required fields to load Commands.")
  ).toHaveCount(0);
  await expect(
    page.locator("form").getByRole("combobox", {
      name: "Ticketing Platform: Select a Ticketing Platform...",
      exact: true,
    })
  ).toBeVisible();
  expect(deviceRequests).toEqual([]);
  expect(commandRequests).toEqual([]);

  await page
    .locator("form")
    .getByRole("combobox", { name: "Select a Site..." })
    .click();
  await page
    .getByRole("dialog")
    .getByRole("option", { name: SITES_LIST.pdx01, exact: true })
    .click();
  await expect.poll(() => deviceRequests.length).toBe(1);
  expect(deviceRequests[0].searchParams.getAll("site")).toEqual([
    SITES_LIST.pdx01,
  ]);
  expect(deviceRequests[0].searchParams.getAll("site_type")).toEqual(["Site"]);
  expect(commandRequests).toEqual([]);

  await pickDevices(page, DEVICE.name, SECOND_DEVICE.name);

  await expect.poll(() => commandRequests.length).toBeGreaterThan(0);
  await expect(
    page.getByText("Select devices above to see available commands.")
  ).toHaveCount(0);
  await expect(
    page.getByText("Select the required fields to load Commands.")
  ).toHaveCount(0);
  const commandRequest = commandRequests.at(-1)!;
  expect(commandRequest.searchParams.getAll("platform")).toEqual([
    "arista-eos",
    "cumulus-linux",
  ]);
  expect(commandRequest.searchParams.has("device_id")).toBe(false);
  expect(deviceRequests).toHaveLength(1);

  const [first, second] = DIAGNOSTICS_COMMANDS;
  await expect(page.getByText("Runs on all selected devices")).toBeVisible({
    timeout: TEST_TIMEOUT,
  });
  await page.getByLabel(first.name).click();
  await page.getByLabel(second.name).click();
  await page
    .locator("form")
    .getByRole("combobox", {
      name: "Ticketing Platform: Select a Ticketing Platform...",
      exact: true,
    })
    .click();
  await page.getByRole("dialog").getByRole("option", { name: "Jira" }).click();
  await page
    .getByLabel("Issue Key (optional — leave blank for ticketless mode)")
    .fill("NETSUPPORT-1234");
  await page
    .getByRole("checkbox", { name: "Include tech support bundle" })
    .click();

  const post = nextPost(page, ENDPOINT);
  await submit(page);
  expect((await post).postDataJSON()).toEqual({
    device_ids: [DEVICE.id, SECOND_DEVICE.id],
    commands: [first.name, second.name],
    ticketing_platform: "jira",
    issue_key: "NETSUPPORT-1234",
    include_tech_support: true,
  });
});

test("prefills a device and command after selected-device metadata settles", async ({
  page,
}) => {
  const commandRequests: URL[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (
      url.pathname ===
      "/v1/workflow/diagnostics/form-options/diagnostic-commands"
    ) {
      commandRequests.push(url);
    }
  });
  const command = DIAGNOSTICS_COMMANDS[2].name;
  const query = new URLSearchParams({
    site: SITES_LIST.pdx01,
    "device-id": DEVICE.id,
    commands: command,
  });

  await page.goto(`${PATH}?${query}`);

  await expect(page.getByLabel(command)).toBeChecked({
    timeout: TEST_TIMEOUT,
  });
  await expect.poll(() => commandRequests.length).toBeGreaterThan(0);
  expect(commandRequests.at(-1)!.searchParams.getAll("platform")).toEqual([
    "arista-eos",
  ]);

  const post = nextPost(page, ENDPOINT);
  await submit(page);
  expect((await post).postDataJSON()).toEqual({
    device_ids: [DEVICE.id],
    commands: [command],
    include_tech_support: false,
  });
});

test("submits a command shared by some selected platforms only once", async ({
  page,
}) => {
  const partialCommand = "show partial support";
  await page.route(
    /\/v1\/workflow\/diagnostics\/form-options\/diagnostic-commands(\?.*)?$/,
    (route) =>
      route.fulfill({
        status: 200,
        json: {
          items: [
            {
              label: "show version",
              value: "show version",
              description: "Supported everywhere",
              group: "Runs on all selected devices",
            },
            {
              label: partialCommand,
              value: partialCommand,
              description: "Supported by Arista and Cumulus",
              group: "Arista EOS only",
            },
            {
              label: partialCommand,
              value: partialCommand,
              description: "Supported by Arista and Cumulus",
              group: "Cumulus Linux only",
            },
          ],
          meta: { warnings: [] },
        },
      })
  );
  await page.goto(PATH);
  await page
    .locator("form")
    .getByRole("combobox", { name: "Select a Site..." })
    .click();
  await page
    .getByRole("dialog")
    .getByRole("option", { name: SITES_LIST.pdx01, exact: true })
    .click();
  await pickDevices(page, DEVICE.name, SECOND_DEVICE.name, THIRD_DEVICE.name);

  await expect(page.getByText("Arista EOS only")).toBeVisible({
    timeout: TEST_TIMEOUT,
  });
  await expect(page.getByText("Cumulus Linux only")).toBeVisible();
  const appearances = page.getByLabel(partialCommand);
  await expect(appearances).toHaveCount(2);
  await appearances.first().click();
  await expect(appearances.nth(0)).toBeChecked();
  await expect(appearances.nth(1)).toBeChecked();
  const post = nextPost(page, ENDPOINT);
  await submit(page);
  expect((await post).postDataJSON()).toEqual({
    device_ids: [DEVICE.id, SECOND_DEVICE.id, THIRD_DEVICE.id],
    commands: [partialCommand],
    include_tech_support: false,
  });
});
