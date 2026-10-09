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
 * The Switch OS Upgrade form on its form ID route (the legacy
 * `/workflows/switchosupgradeworkflow/form` redirects there). The shared suite replaces
 * the legacy page's tests; the payload is unchanged (`{device_id}`).
 */
import { FORBIDDEN_DEVICE_IDS } from "@/mocks/data";

import { runWorkflowFormTests } from "./shared/workflowFormTests";

runWorkflowFormTests({
  workflow: "SwitchOSUpgradeWorkflow",
  legacySlug: "switchosupgradeworkflow",
  formTitle: "New Switch OS Upgrade Workflow",
  endpoint: "/v1/workflow/ngc/switch_os_upgrade",
  forbiddenFilter: (devices) => devices.find((device) => device.id === FORBIDDEN_DEVICE_IDS.ARISTA)!,
});
