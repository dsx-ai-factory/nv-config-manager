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
 * The legacy page's "API validation details" toast is now an inline form-level error
 * (structured 422 detail at the request body), covered by the shared suite.
 */
import { runWorkflowFormTests } from "./shared/workflowFormTests";

const isUfm = (device: { platform: string }) => device.platform === "UFM";

runWorkflowFormTests({
  workflow: "InfinibandGetUnhealthyPortsWorkflow",
  legacySlug: "infinibandgetunhealthyportsworkflow",
  formTitle: "New InfiniBand Get Unhealthy Ports Workflow",
  endpoint: "/v1/workflow/ngc/infiniband_get_unhealthy_ports",
  deviceFilter: (devices) => devices.find(isUfm)!,
  forbiddenFilter: (devices) => devices.find(isUfm)!,
  listedDevices: (devices) => devices.filter(isUfm),
});
