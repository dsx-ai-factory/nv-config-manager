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
import { useMemo } from "react";
import useSWRImmutable from "swr/immutable";

import { useRuntimeConfig } from "@/config/runtime";
import { fetcher } from "@/lib/fetcher";
import { sanitizeUrl } from "@/lib/utils";

export interface WhoamiResponse {
  user: string;
  roles: string[];
}

interface UseWhoamiReturn {
  userInfo: WhoamiResponse | undefined;
  isUnauthorized: boolean;
  userRoles: ReadonlySet<string>;
  isLoaded: boolean;
}

const useWhoami = (): UseWhoamiReturn => {
  const { config } = useRuntimeConfig();
  const apiURL = config?.workflowApiUrl;
  const { data, error } = useSWRImmutable<WhoamiResponse>(
    apiURL ? sanitizeUrl(`${apiURL}/whoami`) : null,
    fetcher
  );

  const isUnauthorized = Boolean(error);
  const roles = isUnauthorized ? undefined : data?.roles;
  const userRoles = useMemo(() => new Set(roles ?? []), [roles]);

  return {
    userInfo: data,
    isUnauthorized,
    userRoles,
    isLoaded: data !== undefined || isUnauthorized,
  };
};

export default useWhoami;
