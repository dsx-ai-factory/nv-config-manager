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

import fs from "node:fs/promises";
import path from "node:path";

import { expect, test } from "@playwright/test";

const SCREENSHOT_DIR = path.resolve(
  __dirname,
  "../../../docs/assets/images/dhcp"
);
const CONFIG_SYNC_TIMESTAMP_METRIC =
  "nv_config_manager_dhcp_cache_last_refresh_timestamp_seconds";

test.use({
  colorScheme: "light",
  locale: "en-US",
  timezoneId: "America/Los_Angeles",
  viewport: { width: 1440, height: 1000 },
});

test.beforeEach(async ({ page }) => {
  const configSyncTimestamp = Math.floor(Date.now() / 1000) - 240;
  await page.addInitScript(() => {
    window.BYPASS_MSW = true;
  });
  await page.route("**/api/config", async (route) => {
    await route.fulfill({
      status: 200,
      json: {
        configStoreApiUrl: "http://localhost:9001",
        dhcpUrl: "http://127.0.0.1:3000",
        dcimUrl: "https://nautobot.nvcm.air",
        dcimProvider: "nautobot",
        dcimDisplayName: "Nautobot",
        renderServiceUrl: "http://localhost:9002",
        workflowApiUrl: "http://localhost:9000",
        ztpUrl: "http://localhost:9003",
      },
    });
  });
  await page.route("**/healthcheck", async (route) => {
    await route.fulfill({ status: 200, json: { status: "ok" } });
  });
  await page.route("**/whoami", async (route) => {
    await route.fulfill({
      status: 200,
      json: { user: "demo", roles: ["all", "nvcm-network"] },
    });
  });
  await page.route("**/metrics", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/plain; version=0.0.4",
      body: `${CONFIG_SYNC_TIMESTAMP_METRIC}{ip_version="4"} ${configSyncTimestamp}\n`,
    });
  });
  await page.route("**/summary*", async (route) => {
    await route.fulfill({
      status: 200,
      json: {
        active_lease_count: 3,
        reservation_count: 18,
        pool_count: 1,
      },
    });
  });
  await page.route("**/lease?*", async (route) => {
    await route.fulfill({
      status: 200,
      json: {
        next_cursor: null,
        leases: [
          {
            ip_address: "10.217.162.42",
            hostname: "SPINE1-GP1-CIN3-PDX01",
            hw_address: "02:05:91:48:df:cf",
            subnet: "10.217.162.0/24",
            state: 0,
            cltt: 1783700000,
            valid_lft: 7200,
            expires_at: "2026-07-10T18:00:00Z",
          },
          {
            ip_address: "10.217.162.51",
            hostname: "LEAF1-GP1-CIN2-PDX01",
            client_id: "00:4d:54:32:34:31:35:58",
            subnet: "10.217.162.0/24",
            state: 0,
            cltt: 1783700300,
            valid_lft: 7200,
            expires_at: "2026-07-10T18:05:00Z",
          },
          {
            ip_address: "10.217.162.52",
            hostname: "LEAF2-GP1-CIN1-PDX01",
            hw_address: "02:05:91:48:df:d0",
            subnet: "10.217.162.0/24",
            state: 0,
            cltt: 1783700600,
            valid_lft: 7200,
            expires_at: "2026-07-10T18:10:00Z",
          },
        ],
      },
    });
  });
});

test("captures the DHCP lease dashboard", async ({ page }) => {
  await fs.mkdir(SCREENSHOT_DIR, { recursive: true });
  await page.goto("/dhcp");
  const dashboard = page.getByTestId("dhcp-dashboard");
  await expect(
    dashboard.getByRole("heading", { name: "DHCP lease activity" })
  ).toBeVisible();
  await expect(dashboard.getByText("SPINE1-GP1-CIN3-PDX01")).toBeVisible();
  await expect(dashboard.getByText("4m", { exact: true })).toBeVisible();
  await expect(
    dashboard.getByRole("searchbox", { name: "Filter displayed DHCP data" })
  ).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  await dashboard.screenshot({
    path: path.join(SCREENSHOT_DIR, "lease-dashboard.png"),
  });
});
