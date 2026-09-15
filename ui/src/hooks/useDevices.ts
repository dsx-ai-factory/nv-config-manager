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
import { sanitizeUrl } from "@/lib/utils";
import { DeviceOption } from "@/types/workflow-form.types";

interface UseDevicesProps {
  site: string;
  filterParams?: string[][];
  path?: string;
}

interface UseDevicesReturn {
  devices: DeviceOption[];
  error: Error | null;
  isLoading: boolean;
}

const useDevices = ({
  site,
  filterParams = [],
  path = "/v1/parameter/device",
}: UseDevicesProps): UseDevicesReturn => {
  const { config } = useRuntimeConfig();
  const apiURL = config?.workflowApiUrl;
  const params = new URLSearchParams([...filterParams]).toString();

  const { data, error, isLoading } = useSWR(
    site && apiURL ? sanitizeUrl(`${apiURL}${path}?${params}`) : null,
    fetcher
  );

  return {
    devices: Array.isArray(data)
      ? data.map((d: { name: string; id: string; platform?: string | null }) => ({ key: d.name, value: d.id, platform: d.platform }))
      : [],
    error,
    isLoading,
  };
};
export default useDevices;
