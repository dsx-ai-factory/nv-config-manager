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

import { use } from "react";
import { WorkflowPageProps } from "@/types/workflow-page.types";
import { WorkflowClientComponent } from "@/components/stage-details";
import { fetcher } from "@/lib/fetcher";
import useSWR from "swr";
import WorkflowErrorPage from "@/components/loading/error";
import { useRuntimeConfig } from "@/config/runtime";
import { sanitizeUrl } from "@/lib/utils";
import WorkflowLoadingPage from "./loading";
import { getErrorConfig } from "@/lib/errors";
import { Workflow } from "@/types/data-table.types";

// Force dynamic rendering since we need runtime config
export const dynamic = 'force-dynamic';

export default function WorkflowPage({ params }: WorkflowPageProps) {
  const { id } = use(params);
  const { config } = useRuntimeConfig();
  const apiURL = config?.workflowApiUrl;
  
  const { data, error, isLoading, mutate } = useSWR(
    apiURL ? sanitizeUrl(`${apiURL}/v1/workflow/${id}`) : null,
    fetcher,
    // Refresh every 5 seconds until the workflow is complete
    {
      refreshInterval: (latestData: Workflow | undefined): number => {
        return latestData?.status == "COMPLETED" ? 0 : 5000;
      },
    },
  );
  if (error) {
    return (
      <WorkflowErrorPage
        error={error}
        errorConfig={getErrorConfig(error)}
        reset={function (): void {
          globalThis.location.reload();
        }}
      />
    );
  }
  
  // Show loading while config is loading or data is being fetched
  if (!apiURL || isLoading || !data) {
    return <WorkflowLoadingPage />;
  }
  
  return <WorkflowClientComponent workflow={data} mutate={mutate} />;
}
