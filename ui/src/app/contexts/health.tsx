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

import React, { createContext, useContext, useMemo } from "react";
import useSWR from "swr";
import { fetcher } from "@/lib/fetcher";
import { useRuntimeConfig } from "@/config/runtime";
import { TokenError } from "@/lib/errors";
import { useHeaderContext } from "./header";
import { sanitizeUrl } from "@/lib/utils";
import { TokenExpiryDialog } from "@/components/loading/error";

interface HealthContextType {
  isHealthy: boolean;
}

const HealthContext = createContext<HealthContextType>({ isHealthy: true });

export function HealthProvider({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const { setRefreshPaused, refreshPaused } = useHeaderContext();
  const { config } = useRuntimeConfig();
  const apiURL = config?.workflowApiUrl;

  const { error } = useSWR(apiURL ? sanitizeUrl(`${apiURL}/healthcheck`) : null, fetcher, {
    refreshInterval: 30000,
    onError: (error) => {
      if (error instanceof TokenError) {
        setRefreshPaused(true);
      }
    },
    onSuccess: () => {
      setRefreshPaused(false);
    },
  });

  const isHealthy = !error;
  const healthContextValue = useMemo(() => ({ isHealthy }), [isHealthy]);

  return (
    <HealthContext.Provider value={healthContextValue}>
      <div className="fixed top-4 right-4 z-50">
        <TokenExpiryDialog open={refreshPaused} setOpen={setRefreshPaused} />
      </div>
      {children}
    </HealthContext.Provider>
  );
}

export const useHealth = () => useContext(HealthContext);
