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
import { DEVICES_LIST } from "@/mocks/data";
import { expect } from "@playwright/test";

import { runWorkflowFormTests } from "./shared/workflowFormTests";
import { test } from "./shared/utils";

runWorkflowFormTests({
  formPath: "/workflows/certificaterotationworkflow/form",
  formTitle: "New Certificate Rotation Workflow",
  deviceFilter: (devices) =>
    devices.find((device) => device.platform === "Cumulus Linux") || devices[0],
  defaultPlatform: "Cumulus Linux",
});

test("certificate rotation submits only the selected device ID", async ({
  page,
}) => {
  const device = DEVICES_LIST.PDX01.find(
    (candidate) => candidate.platform === "Cumulus Linux"
  );
  expect(device).toBeDefined();

  const requestPromise = page.waitForRequest((request) =>
    request.url().includes("/v1/workflow/ngc/certificate_rotation")
  );
  await page.goto(
    `/workflows/certificaterotationworkflow/form?site=PDX01&device-id=${device!.id}`
  );
  await page.getByRole("button", { name: "Submit" }).click();

  const request = await requestPromise;
  expect(JSON.parse((await request.postData()) || "{}")).toEqual({
    device_id: device!.id,
  });
});
