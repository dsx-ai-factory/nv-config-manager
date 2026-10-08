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
export const FORBIDDEN_SITE_ID = "forbidden-site-123";
export const FORBIDDEN_VPC_ID = "forbidden-vpc-123";
export const FORBIDDEN_WORKFLOW_ID = "forbidden-workflow-123";

export const SITES_LIST = {
  pdx01: "PDX01",
  rno1: "RNO1",
  forbidden: FORBIDDEN_SITE_ID,
} as const;
export const SITES_LIST_API_RESPONSE = [
  {
    id: "PDX01",
    name: "PDX01",
  },
  {
    id: "RNO1",
    name: "RNO1",
  },
  {
    id: FORBIDDEN_SITE_ID,
    name: FORBIDDEN_SITE_ID,
  },
];
export const ROLES_LIST = {
  leaf: "cin-leaf",
  spine: "cin-spine",
  wan: "wan",
} as const;
export const ROLES_LIST_API_RESPONSE = [
  {
    id: "cin-leaf",
    name: "cin-leaf",
  },
  {
    id: "cin-spine",
    name: "cin-spine",
  },
  {
    id: "wan",
    name: "wan",
  },
];

export const STATUS_LIST = {
  active: "Active",
  provisioned: "Provisioned",
  provisioning: "Provisioning",
  planned: "Planned",
  staged: "Staged",
} as const;
export const STATUS_LIST_API_RESPONSE = [
  {
    id: "Active",
    name: "Active",
  },
  {
    id: "Provisioned",
    name: "Provisioned",
  },
  {
    id: "Provisioning",
    name: "Provisioning",
  },
  {
    id: "Planned",
    name: "Planned",
  },
  {
    id: "Staged",
    name: "Staged",
  },
];

export const TENANT_LIST = {
  tenant_a: "TenantA",
  ngc: "TenantB",
} as const;
export const TENANT_LIST_API_RESPONSE = [
  {
    id: "TenantA",
    name: "TenantA",
  },
  {
    id: "TenantB",
    name: "TenantB",
  },
];

export const SPX_OVERLAY_LIST = {
  primary: "test-overlay-1",
  secondary: "test-overlay-2",
  modified: "modified-vpc",
  submission: "test-overlay-submission",
  forbidden: "test-overlay",
} as const;
export const SPX_OVERLAY_LIST_API_RESPONSE = Object.values(SPX_OVERLAY_LIST).map(
  (name, index) => ({
    id: `spx-overlay-${index + 1}`,
    name,
  })
);

export const DEVICE_INTERFACES_LIST_API_RESPONSE = [
  { id: "interface-swp1", name: "swp1" },
  { id: "interface-swp2", name: "swp2" },
  { id: "interface-swp3", name: "swp3" },
  { id: "interface-swp4", name: "swp4" },
];

export const NAMESPACE_TAGS_LIST_API_RESPONSE = [
  {
    id: "spectrumx",
    name: "spectrumx",
  },
  {
    id: "tenant-a",
    name: "tenant-a",
  },
];

export const DEVICE_TYPES_LIST = [
  "a28b0c1f-2ca9-53cf-ab24-7acc0008e7e4",
  "ce01ade9-ed54-5ae2-8b9d-1d3859233cfe",
  "5a1701c9-ee93-5fa6-9d03-9e2530af911d",
  "57c88ccb-2bfd-416e-9cb2-f313a9cfaf34",
  "2b4910b6-380c-51dc-a6c4-f5eda292755f",
] as const;
export const DEVICE_TYPES_LIST_API_RESPONSE = [
  {
    id: "a28b0c1f-2ca9-53cf-ab24-7acc0008e7e4",
    name: "a28b0c1f-2ca9-53cf-ab24-7acc0008e7e4",
  },
  {
    id: "ce01ade9-ed54-5ae2-8b9d-1d3859233cfe",
    name: "ce01ade9-ed54-5ae2-8b9d-1d3859233cfe",
  },
  {
    id: "5a1701c9-ee93-5fa6-9d03-9e2530af911d",
    name: "5a1701c9-ee93-5fa6-9d03-9e2530af911d",
  },
  {
    id: "57c88ccb-2bfd-416e-9cb2-f313a9cfaf34",
    name: "57c88ccb-2bfd-416e-9cb2-f313a9cfaf34",
  },
  {
    id: "2b4910b6-380c-51dc-a6c4-f5eda292755f",
    name: "2b4910b6-380c-51dc-a6c4-f5eda292755f",
  },
];
