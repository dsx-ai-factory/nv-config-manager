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

import { DEVICES_LIST, SITES_LIST } from "@/mocks/data";

import { mockServerCatalogAndUser } from "./shared/apiMocks";
import { test, TEST_TIMEOUT } from "./shared/utils";
import { formPath, nextPost, runWorkflowFormTests, selected, submit } from "./shared/workflowFormTests";

const isMlnx = (device: { platform: string }) => device.platform === "MLNX-OS";

runWorkflowFormTests({
  workflow: "InfinibandMlnxOSUpgradeWorkflow",
  legacySlug: "infinibandmlnxosupgradeworkflow",
  formTitle: "New InfiniBand MLNX-OS Upgrade Workflow",
  endpoint: "/v1/workflow/ngc/infiniband_mlnx_os_upgrade",
  deviceFilter: (devices) => devices.find(isMlnx)!,
  forbiddenFilter: (devices) => devices.find(isMlnx)!,
  listedDevices: (devices) => devices.filter(isMlnx),
});

test("URL parameters cannot set server-owned workflow inputs", async ({ page }) => {
  await mockServerCatalogAndUser(page, ["reader", "executor"]);
  const device = DEVICES_LIST[SITES_LIST.pdx01].find(isMlnx)!;
  await page.goto(
    `${formPath("InfinibandMlnxOSUpgradeWorkflow")}?site=${SITES_LIST.pdx01}&device-id=${device.id}` +
      "&user=mallory&user_domain=evil.example&workflow_id=forged"
  );
  await expect(selected(page, device.name)).toBeVisible({ timeout: TEST_TIMEOUT });

  const post = nextPost(page, "/v1/workflow/ngc/infiniband_mlnx_os_upgrade");
  await submit(page);
  expect((await post).postDataJSON()).toEqual({ device_id: device.id });
});
