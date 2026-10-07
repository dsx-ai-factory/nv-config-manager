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
import { SiteBackupWorkflowInput } from "@/types/data-table.types";
import { FORBIDDEN_SITE_ID } from "@/mocks/data/formData";

export function validateSiteBackupPayload(
  body: unknown
): { error: string } | null {
  if (!body || typeof body !== "object") {
    return { error: "Invalid request body" };
  }

  const payload = body as Partial<SiteBackupWorkflowInput>;

  if (typeof payload.site !== "string" || !payload.site.trim()) {
    return { error: "Missing required field: site" };
  }
  // Like `SiteBackupInput`: roles, status, and backup_enabled_only have defaults.
  if (payload.roles !== undefined && !Array.isArray(payload.roles)) {
    return { error: "Invalid field: roles" };
  }
  if (
    payload.status !== undefined &&
    (!Array.isArray(payload.status) || payload.status.length === 0)
  ) {
    return { error: "Invalid field: status" };
  }
  if (
    payload.backup_enabled_only !== undefined &&
    typeof payload.backup_enabled_only !== "boolean"
  ) {
    return { error: "Invalid field: backup_enabled_only" };
  }

  return null;
}

export const siteBackupHandlers = [
  http.post(
    sanitizeUrl(`${apiURL}/v1/workflow/ngc/site_backup`),
    async ({ request }) => {
      const body = (await request.json()) as SiteBackupWorkflowInput;

      const validationError = validateSiteBackupPayload(body);
      if (validationError) {
        return HttpResponse.json(validationError, { status: 400 });
      }

      if (body.site === FORBIDDEN_SITE_ID) {
        return HttpResponse.json(
          {
            error: "Forbidden: You do not have permission to run this workflow",
          },
          { status: 403 }
        );
      }

      await delay(2500);

      return HttpResponse.json(
        {
          id: body.site,
          href: `https://url-to-temporal.com/namespaces/default/workflows/${body.site}`,
        },
        { status: 201 }
      );
    }
  ),
];
