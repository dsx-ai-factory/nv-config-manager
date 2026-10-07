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
 * The Configuration Backup form on its class-name route (the legacy
 * `/workflows/backupworkflow/form` redirects there).
 *
 * Replaces the legacy page's "Additional Tests" (plan section 2): the legacy page posted
 * `intended_config_commit_id: ""`, `user: ""`, `user_domain`, and `workflow_id: ""`; the
 * RJSF form posts only projected inputs, the device plus the hidden `trigger` whose
 * form-only default is "API" (the model requires it), and the server fills the rest.
 */
import { expect } from "@playwright/test";

import { DEVICES_LIST, FORBIDDEN_DEVICE_IDS, SITES_LIST } from "@/mocks/data";

import { mockServerCatalogAndUser } from "./shared/apiMocks";
import { test, TEST_TIMEOUT } from "./shared/utils";
import { formPath, nextPost, runWorkflowFormTests, selected, submit } from "./shared/workflowFormTests";

const ENDPOINT = "/v1/workflow/ngc/backup";

runWorkflowFormTests({
  workflow: "BackupWorkflow",
  legacySlug: "backupworkflow",
  formTitle: "New Configuration Backup Workflow",
  endpoint: ENDPOINT,
  extraPayload: { trigger: "API" },
  forbiddenFilter: (devices) => devices.find((device) => device.id === FORBIDDEN_DEVICE_IDS.ARISTA)!,
});

test.describe("Backup Config Form - server-owned and hidden inputs", () => {
  test("URL parameters cannot set the trigger, user, or other server-owned inputs", async ({
    page,
  }) => {
    await mockServerCatalogAndUser(page, ["reader", "executor"]);
    const device = DEVICES_LIST[SITES_LIST.pdx01][0];
    await page.goto(
      `${formPath("BackupWorkflow")}?site=${SITES_LIST.pdx01}&device-id=${device.id}` +
        "&trigger=SCHEDULED&user=mallory&user_domain=evil.example&workflow_id=x" +
        "&intended_config_commit_id=abc"
    );
    await expect(selected(page, device.name)).toBeVisible({ timeout: TEST_TIMEOUT });

    const post = nextPost(page, ENDPOINT);
    await submit(page);
    expect((await post).postDataJSON()).toEqual({ device_id: device.id, trigger: "API" });
  });
});
