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
import { delay, http, HttpResponse } from "msw";
import { sanitizeUrl } from "@/lib/utils";
import { mockApiURL as apiURL } from "@/config/mockApiUrl";
import {
  DEVICE_INTERFACES_LIST_API_RESPONSE,
  DEVICES_LIST,
} from "@/mocks/data";

export const useDevicesHandlers = [
  http.get(
    sanitizeUrl(`${apiURL}/v1/parameter/device`),
    async ({ request }) => {
      const url = new URL(request.url);
      const site = url.searchParams.get("site") || "PDX01";
      let devices = DEVICES_LIST[site as keyof typeof DEVICES_LIST] || [];

      // Process all filter parameters
      url.searchParams.forEach((value, key) => {
        if (key === "site") return; // Already handled above
        if (key === "managed_only") return; // Membership flag, not a device field

        // Filter devices based on the parameter
        devices = devices.filter((device) => {
          // Handle multi-value parameters (arrays)
          if (url.searchParams.getAll(key).length > 1) {
            const allowedValues = url.searchParams.getAll(key);
            return allowedValues.includes(
              String(device[key as keyof typeof device])
            );
          }

          // Handle single value parameters
          // Case insensitive matching and partial matching for string values
          const deviceValue = device[key as keyof typeof device];
          if (typeof deviceValue === "string" && typeof value === "string") {
            return deviceValue.toLowerCase().includes(value.toLowerCase());
          }

          return device[key as keyof typeof device] === value;
        });
      });

      await delay(500);

      return HttpResponse.json(devices, { status: 200 });
    }
  ),
  http.get(
    sanitizeUrl(`${apiURL}/v1/parameter/device/:deviceId/interfaces`),
    async () => {
      await delay(300);

      return HttpResponse.json(DEVICE_INTERFACES_LIST_API_RESPONSE, {
        status: 200,
      });
    }
  ),
];
