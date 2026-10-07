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
import { http, HttpResponse } from "msw";

import { mockApiURL as apiURL } from "@/config/mockApiUrl";
import { sanitizeUrl } from "@/lib/utils";
import { DEVICES_LIST } from "@/mocks/data";

const diagnosticsCommands = [
  {
    label: "show interface",
    value: "show interface",
    description: "Collect interface state",
  },
  {
    label: "show lldp neighbor",
    value: "show lldp neighbor",
    description: "Collect LLDP neighbors",
  },
  {
    label: "show version",
    value: "show version",
    description: "Collect software versions",
  },
];

const passwordUsers = [
  { label: "admin", value: "admin", description: "admin (admin-password)" },
  { label: "cumulus", value: "cumulus", description: "cumulus (cumulus-password)" },
];

const matchingDeviceCount = (request: Request): number => {
  const search = new URL(request.url).searchParams;
  const location = search.get("location") ?? "";
  const devices = DEVICES_LIST[location as keyof typeof DEVICES_LIST] ?? [];
  const roles = search.getAll("role");
  const statuses = search.getAll("status");
  const tenant = search.get("tenant");

  return devices.filter((device) => {
    const row = device as { role?: string; status?: string; tenant?: string };
    return (
      (roles.length === 0 || roles.includes(row.role ?? "")) &&
      (statuses.length === 0 || statuses.includes(row.status ?? "")) &&
      (!tenant || row.tenant === tenant)
    );
  }).length;
};

/** Direct option sources used by the schema-driven workflow forms. */
export const workflowFormOptionHandlers = [
  http.get(
    sanitizeUrl(`${apiURL}/v1/parameter/diagnostics/command-options`),
    async ({ request }) => {
      const selectedDeviceIds = new URL(request.url).searchParams.getAll("device_id");
      const group = selectedDeviceIds.length > 1 ? "Runs on all selected devices" : undefined;
      return HttpResponse.json(
        {
          items:
            selectedDeviceIds.length > 0
              ? diagnosticsCommands.map((item) => ({ ...item, group }))
              : [],
          meta: { warnings: [] },
        },
        { status: 200 }
      );
    }
  ),
  http.get(
    sanitizeUrl(`${apiURL}/v1/parameter/password-users`),
    async ({ request }) => {
      const count = matchingDeviceCount(request);
      return HttpResponse.json(
        {
          items: count > 0 ? passwordUsers : [],
          meta: { matching_device_count: count, warnings: [] },
        },
        { status: 200 }
      );
    }
  ),
];
