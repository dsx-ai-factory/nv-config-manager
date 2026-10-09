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

/**
 * Apply documented defaults to one catalog entry. Absent and `null` optional fields are
 * treated alike; values of the wrong type fall back to the default rather than leaking
 * into the UI.
 */
export const normalizeWorkflowCatalogEntry = (
  entry: WorkflowCatalogEntryWire
): WorkflowCatalogEntry => {
  const { plugin, tags, has_form, enabled, order, group, ...metadata } = entry;

  return {
    ...metadata,
    plugin: isNonEmptyString(plugin) ? plugin : null,
    tags: Array.isArray(tags)
      ? tags.filter((tag): tag is string => typeof tag === "string")
      : [],
    // Missing means an older API, not that the new `/form` endpoint exists. Keep that
    // state so the launcher can fail closed with an upgrade message.
    has_form: typeof has_form === "boolean" ? has_form : null,
    enabled: typeof enabled === "boolean" ? enabled : true,
    order: typeof order === "number" && Number.isFinite(order) ? order : undefined,
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

const displayNameCollator = new Intl.Collator("en", { numeric: true });

/** The fields that decide display order; launcher items carry them too. */
export type WorkflowSortKey = Pick<WorkflowCatalogEntry, "name" | "display_name" | "order">;

/**
 * Total order for catalog entries: explicit `order` first (ascending), then
 * `display_name`, then `name` as a final tie-break so the result never depends on
 * server ordering.
 */
export const compareWorkflowCatalogEntries = (
  a: WorkflowSortKey,
  b: WorkflowSortKey
): number => {
  if (a.order !== b.order) {
    if (a.order === undefined) return 1;
    if (b.order === undefined) return -1;
    return a.order - b.order;
  }

  const byDisplayName = displayNameCollator.compare(a.display_name, b.display_name);
  if (byDisplayName !== 0) return byDisplayName;

  if (a.name < b.name) return -1;
  if (a.name > b.name) return 1;
  return 0;
};

/** Sorted copy of the catalog; the input is not mutated. */
export const sortWorkflowCatalog = <T extends WorkflowSortKey>(entries: readonly T[]): T[] =>
  [...entries].sort(compareWorkflowCatalogEntries);
