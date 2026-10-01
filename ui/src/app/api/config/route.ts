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
import { NextResponse } from "next/server";

export async function GET() {
  const config = {
    // Application version (git tag from image, e.g. 1.2.1-rc37)
    version: process.env.APP_VERSION || "development",
    // Workflow API (Temporal)
    workflowApiUrl: process.env.WORKFLOW_API_URL || "http://localhost:9000",
    // Config Store API
    configStoreApiUrl:
      process.env.CONFIG_STORE_API_URL || "http://localhost:9000",
    // Selected DCIM provider
    dcimUrl: process.env.DCIM_URL || "https://nautobot.example.com",
    dcimProvider: process.env.DCIM_PROVIDER || "nautobot",
    dcimDisplayName: process.env.DCIM_DISPLAY_NAME || "Nautobot",
    // Render Service
    renderServiceUrl: process.env.RENDER_SERVICE_URL || "http://localhost:9000",
    // ZTP Service
    ztpUrl: process.env.ZTP_URL || "http://localhost:9000",
    // DHCP Service
    dhcpUrl: process.env.DHCP_URL || "http://localhost:9000",
    // Temporal UI (native Temporal web interface)
    temporalUiUrl: process.env.TEMPORAL_UI_URL || "",
    // Grafana (optional observability dashboard)
    grafanaUrl: process.env.GRAFANA_URL || "",
  };

  return NextResponse.json(config);
}

export const dynamic = "force-dynamic";
