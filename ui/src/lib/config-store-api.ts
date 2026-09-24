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
import useSWR from 'swr';
import { useRuntimeConfig } from '@/config/runtime';
import type {
  ConfigFile,
  ConfigVersionsResponse,
  DiffResponse,
} from '@/types/config-store.types';

const fetcher = (url: string) => fetch(url, {
  credentials: 'include',
}).then((res) => {
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
});

export function useDeviceConfigs(deviceUuid: string | null, fileType: 'intended' | 'backup' = 'intended') {
  const { config } = useRuntimeConfig();
  const url = config && deviceUuid 
    ? `${config.configStoreApiUrl}/v1/config/device/${deviceUuid}?file_type=${fileType}` 
    : null;

  return useSWR<ConfigFile[]>(url, fetcher);
}

export function useConfigFile(deviceUuid: string | null, filename: string | null, fileType: 'intended' | 'backup' = 'intended', version?: number) {
  const { config } = useRuntimeConfig();
  const queryParams = new URLSearchParams({ file_type: fileType });
  if (version) queryParams.set('version', version.toString());
  
  const url = config && deviceUuid && filename
    ? `${config.configStoreApiUrl}/v1/config/${deviceUuid}/${filename}?${queryParams}`
    : null;

  return useSWR<ConfigFile>(url, fetcher);
}

export function useConfigVersions(deviceUuid: string | null, filename: string | null, fileType: 'intended' | 'backup' = 'intended') {
  const { config } = useRuntimeConfig();
  const url = config && deviceUuid && filename
    ? `${config.configStoreApiUrl}/v1/config/${deviceUuid}/${filename}/versions?file_type=${fileType}`
    : null;

  return useSWR<ConfigVersionsResponse>(url, fetcher);
}

export function useConfigDiff(
  deviceUuid: string | null,
  filename: string | null,
  fromVersion: number | null,
  toVersion: number | null,
  fileType: 'intended' | 'backup' = 'intended'
) {
  const { config } = useRuntimeConfig();
  const url = config && deviceUuid && filename && fromVersion !== null && toVersion !== null
    ? `${config.configStoreApiUrl}/v1/config/${deviceUuid}/${filename}/diff?from_version=${fromVersion}&to_version=${toVersion}&file_type=${fileType}`
    : null;

  return useSWR<DiffResponse>(url, fetcher);
}

export async function fetchDeviceConfigs(
  apiUrl: string,
  deviceUuid: string,
  fileType: 'intended' | 'backup' = 'intended'
) {
  const response = await fetch(
    `${apiUrl}/v1/config/device/${deviceUuid}?file_type=${fileType}`,
    { credentials: 'include' }
  );
  if (!response.ok) throw new Error(`Failed to fetch configs: ${response.status}`);
  return response.json() as Promise<ConfigFile[]>;
}

export async function searchDevices(
  apiUrl: string,
  query: string = '',
  limit: number = 100,
  fileType: 'intended' | 'backup' = 'intended',
  includeInactive: boolean = false,
) {
  const params = new URLSearchParams({ limit: limit.toString(), file_type: fileType });
  const trimmedQuery = query.trim();
  if (trimmedQuery) {
    params.set('q', trimmedQuery);
  }
  if (includeInactive) {
    params.set('include_inactive', 'true');
  }
  const response = await fetch(`${apiUrl}/v1/admin/devices/search?${params}`, {
    credentials: 'include',
  });
  if (!response.ok) throw new Error(`Device search error: ${response.status}`);
  return response.json();
}

export async function deleteDevice(apiUrl: string, deviceUuid: string) {
  const response = await fetch(`${apiUrl}/v1/admin/devices/${deviceUuid}`, {
    method: 'DELETE',
    credentials: 'include',
  });
  if (!response.ok) throw new Error(`Delete device error: ${response.status}`);
  return response.json();
}

