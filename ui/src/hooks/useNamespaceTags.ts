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
import { fetcher } from "@/lib/fetcher";
import { useRuntimeConfig } from "@/config/runtime";
import { mapRoles, sanitizeUrl } from "@/lib/utils";
import { Option } from "@/types/workflow-form.types";

interface UseNamespaceTagsReturn {
  namespaceTags: Option[];
  error: Error | null;
  hasLoaded: boolean;
  isLoading: boolean;
}

const useNamespaceTags = (location?: string): UseNamespaceTagsReturn => {
  const { config } = useRuntimeConfig();
  const apiURL = config?.workflowApiUrl;
  const params = new URLSearchParams();

  if (location) {
    params.set("location", location);
  }

  const queryString = params.toString();
  const query = queryString ? `?${queryString}` : "";
  const url = apiURL
    ? sanitizeUrl(`${apiURL}/v1/parameter/namespace-tag${query}`)
    : null;

  const { data, error, isLoading } = useSWR(url, fetcher);

  return {
    namespaceTags: data && !error ? mapRoles(data, "name", "name") : [],
    error,
    hasLoaded: data !== undefined || Boolean(error),
    isLoading,
  };
};

export default useNamespaceTags;
