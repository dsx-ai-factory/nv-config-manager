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
import useSWR from "swr";

export interface RuntimeConfig {
  version?: string;
  workflowApiUrl: string;
  configStoreApiUrl: string;
  dcimUrl: string;
  dcimProvider: string;
  dcimDisplayName: string;
  renderServiceUrl: string;
  ztpUrl: string;
  dhcpUrl: string;
  temporalUiUrl?: string;
  grafanaUrl?: string;
}

const fetcher = (url: string) =>
  fetch(url, {
    credentials: "include",
  }).then((res) => res.json());

export function useRuntimeConfig() {
  const { data, error, isLoading } = useSWR<RuntimeConfig>(
    "/api/config",
    fetcher,
    {
      revalidateOnFocus: false,
      revalidateOnReconnect: false,
      // Cache for the session
      dedupingInterval: 60000,
    }
  );

  return {
    config: data,
    isLoading,
    isError: error,
  };
}
