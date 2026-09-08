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
import { sanitizeUrl } from "@/lib/utils";
import { mockApiURL as apiURL } from "@/config/mockApiUrl";

export const devicePasswordRotationHandlers = [
  http.post(
    sanitizeUrl(`${apiURL}/v1/workflow/ngc/device_password_rotation`),
    async ({ request }) => {
      const body = await request.json() as { device_id?: string; selected_secret?: string } | null;

      // Validate body exists and has required fields
      if (!body?.device_id || !body.selected_secret) {
        return HttpResponse.json(
          {
            error: "Missing required fields: device_id and selected_secret",
          },
          { status: 400 }
        );
      }

      // Simulate forbidden device scenario
      if (body.device_id === "forbidden-device-id") {
        return HttpResponse.json(
          {
            error: "Forbidden: You do not have permission to rotate passwords on this device",
          },
          { status: 403 }
        );
      }

      // Return success response
      return HttpResponse.json(
        {
          id: `device-password-rotation-${Date.now()}`,
          href: `https://temporal.example.com/workflows/device-password-rotation-${Date.now()}`,
        },
        { status: 201 }
      );
    }
  ),

  // Mock password users endpoint
  http.get(
    sanitizeUrl(`${apiURL}/v1/parameter/device/:deviceId/password_users`),
    async ({ params }) => {
      const { deviceId } = params;

      // Simulate forbidden device
      if (deviceId === "forbidden-device-id") {
        return HttpResponse.json(
          {
            error: "Forbidden: You do not have permission to access this device",
          },
          { status: 403 }
        );
      }

      // Return mock password users
      const passwordUsers = [
        { name: "admin", description: "Administrator account" },
        { name: "cumulus", description: "Cumulus user account" },
      ];

      return HttpResponse.json(passwordUsers, { status: 200 });
    }
  ),
];
