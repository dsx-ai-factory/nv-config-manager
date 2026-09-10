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
export interface DeployWorkflowFormProps {
  devices: Device[];
}

export interface BackupWorkflowFormProps {
  devices: Device[];
}

export interface DeviceCableValidationWorkflowFormProps {
  devices: Device[];
}

export interface ConnectedHostMetadataWorkflowFormProps {
  devices: Device[];
}

export interface SiteCableValidationWorkflowFormProps {
  site: Site[];
  roles: string[];
  status: string[];
  tenant: Tenant;
  device_types: DeviceType[];
  raise_for_invalid: boolean;
}

export interface Tenant {
  name: string;
  slug: string;
}

export interface DeviceType {
  name: string;
  id: string;
}

export interface Site {
  name: string;
  slug: string;
}

export interface Device {
  name: string;
  id: string;
}

export interface Option {
  key: string;
  value: string;
}

export type DeviceResult = {
  name: string;
  id: string;
};

export type SiteResult = {
  name: string;
  id: string;
  model?: string | null;
};

export type LocationResult = SiteResult;

export type LocationOption = Option & {
  id: string;
  model?: string;
};

export type SiteOption = LocationOption;

export type DeviceOption = {
  key: string;
  value: string;
  platform?: string | null;
};
