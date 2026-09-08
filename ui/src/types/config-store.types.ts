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
export interface DeviceMetadata {
  name: string;
  site: string;
  platform: string | null;
  role: string | null;
  rack: string | null;
  primary_ip4: string | null;
  device_url: string | null;
  nautobot_url: string | null;
  last_updated: string | null;
}

export interface ConfigFile {
  id: string;
  device_uuid: string;
  filename: string;
  file_type: 'intended' | 'backup';
  version: number;
  content: string;
  content_hash: string;
  author: string;
  commit_message: string;
  created_at: string;
  device?: DeviceMetadata | null;
}

export interface ConfigVersion {
  version: number;
  file_type: 'intended' | 'backup';
  author: string;
  commit_message: string;
  created_at: string;
  content_hash: string;
}

export interface ConfigVersionsResponse {
  device_uuid: string;
  filename: string;
  versions: ConfigVersion[];
  device?: DeviceMetadata | null;
}

export interface DiffResponse {
  device_uuid: string;
  filename: string;
  from_version: number;
  to_version: number;
  diff: string;
  old_content: string;
  new_content: string;
  diff_stats: {
    from_lines: number;
    to_lines: number;
    additions: number;
    deletions: number;
  };
  device?: DeviceMetadata | null;
}

export interface BatchConfigItem {
  filename: string;
  content: string;
  author: string;
  commit_message: string;
}

export interface DeviceSearchResult {
  name: string;
  uuid: string;
}

export interface DeviceWithLatestConfig {
  uuid: string;
  name: string;
  site: string;
  latest_update: string;
  latest_author: string;
  latest_message: string;
  active: boolean;
}

export interface DeleteDeviceResponse {
  device_uuid: string;
  deleted_versions: number;
  message: string;
}
