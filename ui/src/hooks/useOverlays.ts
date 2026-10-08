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
import useSWR from "swr";
import { fetcher } from "@/lib/fetcher";
import { useRuntimeConfig } from "@/config/runtime";
import { mapRoles, sanitizeUrl } from "@/lib/utils";
import { Option } from "@/types/workflow-form.types";
import { parseLocationValue } from "@/lib/location-options";

interface UseOverlaysOptions {
  enabled?: boolean;
  isolationType?: string;
  location?: string;
}

interface UseOverlaysReturn {
  overlays: Option[];
  error: Error | null;
  hasLoaded: boolean;
  isLoading: boolean;
}

export const SPX_OVERLAY_ISOLATION_TYPE = "spectrum_x_vrf";

const useOverlays = ({
  enabled = true,
  isolationType,
  location,
}: UseOverlaysOptions = {}): UseOverlaysReturn => {
  const { config } = useRuntimeConfig();
  const apiURL = config?.workflowApiUrl;
  const params = new URLSearchParams();

  if (location) {
    const reference = parseLocationValue(location);
    if (reference) {
      params.set("location", reference.id);
      if (reference.locationType) params.set("location_type", reference.locationType);
    }
  }
  if (isolationType) {
    params.set("isolation_type", isolationType);
  }

  const queryString = params.toString();
  let url: string | null = null;
  if (apiURL && enabled) {
    const querySuffix = queryString ? `?${queryString}` : "";
    url = sanitizeUrl(`${apiURL}/v1/parameter/overlay${querySuffix}`);
  }
  const { data, error, isLoading } = useSWR(url, fetcher);
  const overlays = useMemo(
    () => (data && !error ? mapRoles(data, "name", "name") : []),
    [data, error]
  );

  return {
    overlays,
    error,
    hasLoaded: data !== undefined || Boolean(error),
    isLoading,
  };
};

export default useOverlays;
