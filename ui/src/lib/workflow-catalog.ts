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
import { sanitizeUrl } from "@/lib/utils";
import type {
  WorkflowCatalogEntry,
  WorkflowCatalogEntryWire,
  WorkflowCatalogResponseWire,
} from "@/types/workflow-catalog.types";

/** Section for catalog entries that do not declare a `group`. */
export const DEFAULT_WORKFLOW_GROUP = "Other";

/** Build the catalog URL from the runtime workflow API URL. */
export const buildWorkflowCatalogUrl = (apiURL: string): string =>
  sanitizeUrl(`${apiURL}/v1/workflow/metadata`);

const isNonEmptyString = (value: unknown): value is string =>
  typeof value === "string" && value.length > 0;

const isWorkflowFormId = (value: unknown): value is string =>
  typeof value === "string" && /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/.test(value);

/**
 * Apply documented defaults to one catalog entry. Absent and `null` optional fields are
 * treated alike; values of the wrong type fall back to the default rather than leaking
 * into the UI.
 */
export const normalizeWorkflowCatalogEntry = (
  entry: WorkflowCatalogEntryWire
): WorkflowCatalogEntry => {
  const {
    name,
    display_name,
    description,
    endpoint,
    namespace,
    cli_name,
    input_class,
    read_roles,
    execute_roles,
    form_id,
    has_form,
    group,
  } = entry;

  return {
    name,
    display_name,
    description,
    endpoint,
    namespace,
    cli_name,
    input_class,
    read_roles,
    execute_roles,
    form_id: isWorkflowFormId(form_id) ? form_id : null,
    // Missing means an older API, not that the new `/form` endpoint exists. Keep that
    // state so the launcher can fail closed with an upgrade message.
    has_form: typeof has_form === "boolean" ? has_form : null,
    group: isNonEmptyString(group) ? group : DEFAULT_WORKFLOW_GROUP,
  };
};

/** Normalise a whole `/metadata` response; a missing response yields an empty catalog. */
export const normalizeWorkflowCatalog = (
  response: WorkflowCatalogResponseWire | null | undefined
): WorkflowCatalogEntry[] =>
  Array.isArray(response?.workflows)
    ? response.workflows.map(normalizeWorkflowCatalogEntry)
    : [];

/**
 * Whether a non-empty catalog came from an API that predates workflow forms.
 *
 * Old APIs omit both fields from every entry. Requiring both normalized fields to be
 * absent avoids treating one malformed or third-party entry as a system-wide version
 * mismatch.
 */
export const isLegacyWorkflowCatalog = (
  catalog: readonly WorkflowCatalogEntry[]
): boolean =>
  catalog.length > 0 &&
  catalog.every((entry) => entry.has_form === null && entry.form_id === null);
