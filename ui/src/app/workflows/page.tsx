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

import WorkflowTable from "./workflow-table";

import { fetcher } from "@/lib/fetcher";
import useSWR from "swr";
import useSWRImmutable from "swr/immutable";
import { useRuntimeConfig } from "@/config/runtime";
import { sanitizeUrl } from "@/lib/utils";
import WorkflowErrorPage from "@/components/loading/error";
import { getErrorConfig, TokenError } from "@/lib/errors";
import { useState, useEffect } from "react";
import WorkflowsListSkeleton from "./loading";
import { WorkflowMetadataResponse } from "@/types/data-table.types";

// Force dynamic rendering since we need runtime config
export const dynamic = 'force-dynamic';

export default function WorkflowsPage() {
  const [shouldFetch, setShouldFetch] = useState(true);
  const { config } = useRuntimeConfig();
  const apiURL = config?.workflowApiUrl;

  const {
    data: workflowMetadata,
    error: workflowMetadataError,
    isLoading: workflowMetadataIsLoading,
  } = useSWRImmutable<WorkflowMetadataResponse>(
    apiURL ? sanitizeUrl(`${apiURL}/v1/workflow/metadata`) : null,
    fetcher
  );

  const { error } = useSWR(
    shouldFetch && apiURL ? sanitizeUrl(`${apiURL}/healthcheck`) : null,
    fetcher,
    {
      refreshInterval: 60000,
    }
  );

  useEffect(() => {
    if (error instanceof TokenError) {
      setShouldFetch(false);
    }
  }, [error]);

  useEffect(() => {
    const hash = globalThis.location.hash;

    if (hash) {
      const element = document.querySelector(hash);
      if (element) {
        element.scrollIntoView({ behavior: "smooth" });
      }
    }
  }, []);

  if (workflowMetadataError) {
    return (
      <WorkflowErrorPage
        error={workflowMetadataError}
        errorConfig={getErrorConfig(workflowMetadataError)}
        reset={function (): void {
          globalThis.location.reload();
        }}
      />
    );
  }
  
  // Show loading while config is loading or data is being fetched
  if (!apiURL || workflowMetadataIsLoading || !workflowMetadata) {
    return <WorkflowsListSkeleton />;
  }

  return (
    <WorkflowTable
      workflowMetadata={workflowMetadata.workflows}
    />
  );
}
