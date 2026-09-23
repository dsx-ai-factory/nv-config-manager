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

import { useRuntimeConfig } from "@/config/runtime";
import { fetcher } from "@/lib/fetcher";
import { sanitizeUrl } from "@/lib/utils";
import { Option } from "@/types/workflow-form.types";

type NamedRecord = { id: string; name: string };
type CircuitRecord = { id: string; cid: string; status?: string | null };
type LagSuggestion = { lag_name: string };
type PrefixSuggestion = { prefix: string; parent_prefix: string };

const useParameter = <T>(path: string | null) => {
  const { config } = useRuntimeConfig();
  const apiURL = config?.workflowApiUrl;
  return useSWR<T>(path && apiURL ? sanitizeUrl(`${apiURL}${path}`) : null, fetcher);
};

export const useBBDevices = () => {
  const result = useParameter<NamedRecord[]>("/v1/parameter/bb-sandbox/devices");
  return {
    ...result,
    options: Array.isArray(result.data)
      ? result.data.map((device) => ({ key: device.name, value: device.id }))
      : [],
  };
};

export const useBBCircuits = (status?: string) => {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  const result = useParameter<CircuitRecord[]>(
    `/v1/parameter/bb-sandbox/circuits${query}`
  );
  return {
    ...result,
    options: Array.isArray(result.data)
      ? result.data.map((circuit) => ({
          key: circuit.status ? `${circuit.cid} (${circuit.status})` : circuit.cid,
          value: circuit.cid,
        }))
      : [],
  };
};

export const useBBInterfaces = (
  deviceId: string,
  purpose: "drain" | "lag-member"
) => {
  const path = deviceId
    ? `/v1/parameter/bb-sandbox/devices/${encodeURIComponent(deviceId)}/interfaces?purpose=${purpose}`
    : null;
  const result = useParameter<NamedRecord[]>(path);
  return {
    ...result,
    options: Array.isArray(result.data)
      ? result.data.map((deviceInterface) => ({
          key: deviceInterface.name,
          value: deviceInterface.name,
        }))
      : ([] as Option[]),
  };
};

export const useBBNextLag = (localDeviceId: string, remoteDeviceId: string) => {
  const params = new URLSearchParams({
    local_device_id: localDeviceId,
    remote_device_id: remoteDeviceId,
  });
  return useParameter<LagSuggestion>(
    localDeviceId && remoteDeviceId && localDeviceId !== remoteDeviceId
      ? `/v1/parameter/bb-sandbox/next-lag?${params}`
      : null
  );
};

export const useBBNextPrefix = (prefixLength: 31 | 127) =>
  useParameter<PrefixSuggestion>(
    `/v1/parameter/bb-sandbox/next-prefix?role=BB-P2P&prefix_length=${prefixLength}`
  );
