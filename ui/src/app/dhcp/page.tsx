"use client";
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

import { Loader2 } from "lucide-react";

import { LeaseDashboard } from "@/components/dhcp";
import { useRuntimeConfig } from "@/config/runtime";

export const dynamic = "force-dynamic";

/** Render the dedicated DHCP activity dashboard. */
export default function DhcpPage() {
  const { config, isLoading } = useRuntimeConfig();

  if (isLoading || !config) {
    return (
      <div className="container flex min-h-[60vh] items-center justify-center py-8">
        <Loader2 className="h-8 w-8 animate-spin" />
        <p className="ml-2">Loading DHCP dashboard...</p>
      </div>
    );
  }

  return (
    <main className="container py-8">
      <LeaseDashboard
        dhcpUrl={config.dhcpUrl}
        grafanaUrl={config.grafanaUrl}
      />
    </main>
  );
}
