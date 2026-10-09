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

/**
 * Types for the registry-backed workflow catalog
 * (`GET /v1/workflow/metadata?include=form`) and the per-workflow form contract
 * (`GET /v1/workflow/{form_id}/form`, wire contract v1).
 *
 * Kept apart from `data-table.types.ts` so catalog and form concerns do not keep
 * expanding the execution-table model.
 */
import type { WorkflowMetadata } from "@/types/data-table.types";

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue =
  | JsonPrimitive
  | JsonValue[]
  | { [key: string]: JsonValue };
export type JsonObject = { [key: string]: JsonValue };

/**
 * Optional catalog fields. Every one may be absent on older servers, or `null` when
 * a server serialises an unset optional.
 */
export interface WorkflowCatalogOptionalFields {
  /** Stable URL identity for browser form endpoints; absent on older servers. */
  form_id?: string | null;
  has_form?: boolean | null;
  group?: string | null;
}

/** A catalog entry as received from the server. */
export type WorkflowCatalogEntryWire = WorkflowMetadata &
  WorkflowCatalogOptionalFields;

/** The `/v1/workflow/metadata?include=form` response as received from the server. */
export interface WorkflowCatalogResponseWire {
  workflows: WorkflowCatalogEntryWire[];
}

/** A catalog entry after boundary normalisation; every optional field has its default. */
export type WorkflowCatalogEntry = WorkflowMetadata & {
  /** Stable URL identity; `null` means the server cannot support browser form links. */
  form_id: string | null;
  /**
   * Browser-form availability advertised by the workflow API. `null` means the field
   * was absent or invalid, as on an older API; callers must not infer `/form` support.
   */
  has_form: boolean | null;
  group: string;
};

export type JsonSchemaNode = { [keyword: string]: unknown };

export type ScalarValue = string | number | boolean;

export type OptionSourceParamValue = ScalarValue | ScalarValue[];

export interface Dependency {
  field: string;
  required?: false;
}

export interface OptionSource {
  endpoint: string;
  label_key: string;
  value_key: string;
  response?: "options-v1";
  type_key?: string;
  params?: Record<string, OptionSourceParamValue>;
  depends_on?: Record<string, Dependency>;
  clear_on_change?: true;
}

export type WorkflowUiSchema = { [key: string]: unknown };

export interface WorkflowFormResponse {
  schema: JsonSchemaNode;
  ui_schema: WorkflowUiSchema;
  ui_schema_version: 1;
  requires: string[];
}
