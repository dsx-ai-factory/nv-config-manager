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
import { useMemo } from "react";
import { useRuntimeConfig } from "@/config/runtime";
import { fetcher } from "@/lib/fetcher";
import { sanitizeUrl } from "@/lib/utils";
import { Option } from "@/types/workflow-form.types";

interface DeviceInterfaceResult {
  id: string;
  name: string;
}

interface UseDeviceInterfacesReturn {
  interfaces: Option[];
  error: Error | null;
  hasLoaded: boolean;
  isLoading: boolean;
}

const useDeviceInterfaces = (
  deviceId: string
): UseDeviceInterfacesReturn => {
  const { config } = useRuntimeConfig();
  const apiURL = config?.workflowApiUrl;
  const { data, error, isLoading } = useSWR<DeviceInterfaceResult[]>(
    deviceId && apiURL
      ? sanitizeUrl(
          `${apiURL}/v1/parameter/device/${encodeURIComponent(deviceId)}/interfaces`
        )
      : null,
    fetcher
  );
  const interfaces = useMemo(
    () =>
      Array.isArray(data)
        ? data.map((deviceInterface) => ({
            key: deviceInterface.name,
            value: deviceInterface.name,
          }))
        : [],
    [data]
  );

  return {
    interfaces,
    error,
    hasLoaded: data !== undefined,
    isLoading,
  };
};

export default useDeviceInterfaces;
