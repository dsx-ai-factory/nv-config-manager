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
import { APIError, TokenError } from "@/lib/errors";
import { fetcher } from "@/lib/fetcher";
import { sanitizeUrl } from "@/lib/utils";

export interface WhoamiResponse {
  user: string;
  roles: string[];
}

export type WhoamiStatus =
  | "loading"
  | "loaded"
  | "unauthorized"
  | "unavailable";

interface UseWhoamiReturn {
  userInfo: WhoamiResponse | undefined;
  status: WhoamiStatus;
  isUnauthorized: boolean;
  userRoles: ReadonlySet<string>;
  isLoaded: boolean;
  isRetrying: boolean;
  reload: () => Promise<WhoamiResponse | undefined>;
}

const isAuthorizationError = (error: Error | undefined): boolean =>
  error instanceof TokenError ||
  (error instanceof APIError && (error.status === 401 || error.status === 403));

const useWhoami = (): UseWhoamiReturn => {
  const { config } = useRuntimeConfig();
  const apiURL = config?.workflowApiUrl;
  const { data, error, isValidating, mutate } = useSWRImmutable<
    WhoamiResponse,
    Error
  >(apiURL ? sanitizeUrl(`${apiURL}/whoami`) : null, fetcher);

  const status: WhoamiStatus =
    data !== undefined
      ? "loaded"
      : isAuthorizationError(error)
        ? "unauthorized"
        : error
          ? "unavailable"
          : "loading";
  const isUnauthorized = status === "unauthorized";
  const roles = data?.roles;
  const userRoles = useMemo(() => new Set(roles ?? []), [roles]);

  return {
    userInfo: data,
    status,
    isUnauthorized,
    userRoles,
    isLoaded: status !== "loading",
    isRetrying: status === "unavailable" && isValidating,
    reload: () => mutate(),
  };
};

export default useWhoami;
