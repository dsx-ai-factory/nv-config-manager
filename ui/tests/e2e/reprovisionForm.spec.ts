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
import { test, TEST_TIMEOUT } from "./shared/utils";
import { formPath, runWorkflowFormTests } from "./shared/workflowFormTests";

const isReprovisionable = (device: { platform: string }) =>
  device.platform === "Cumulus Linux" || device.platform === "NV-OS";

runWorkflowFormTests({
  workflow: "ReprovisionWorkflow",
  legacySlug: "reprovisionworkflow",
  formTitle: "New Reprovision Workflow",
  endpoint: "/v1/workflow/ngc/reprovision",
  deviceFilter: (devices) => devices.find(isReprovisionable)!,
  forbiddenFilter: (devices) => devices.find(isReprovisionable)!,
  listedDevices: (devices) => devices.filter(isReprovisionable),
});

test("Reprovision warns that the workflow is destructive", async ({ page }) => {
  await mockServerCatalogAndUser(page, ["reader", "executor"]);
  await page.goto(formPath("ReprovisionWorkflow"));
  await expect(
    page.getByText(
      "This workflow is destructive. It will replace all existing configuration on the " +
        "device with the intended configuration."
    )
  ).toBeVisible({ timeout: TEST_TIMEOUT });
});
