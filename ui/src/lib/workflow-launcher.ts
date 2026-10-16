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

/** Builds the "New workflow" launcher from the workflow catalog and site overrides. */
import type { WorkflowLauncherOverride } from "@/config/site";
import { workflowFormPath } from "@/config/workflow-redirects.mjs";
import { DEFAULT_WORKFLOW_GROUP } from "@/lib/workflow-catalog";
import type { WorkflowMetadata } from "@/types/data-table.types";
import type { WorkflowCatalogEntry } from "@/types/workflow-catalog.types";

export type WorkflowLauncherOverrides = Readonly<
  Record<string, WorkflowLauncherOverride>
>;

export const WORKFLOW_FORM_API_UPGRADE_REQUIRED =
  "Upgrade the Config Manager workflow API before deploying this UI version. Browser workflow forms require the backend form metadata and endpoints.";

const canExecuteWorkflow = (
  metadata: WorkflowMetadata | undefined,
  userRoles: ReadonlySet<string>
): boolean => {
  if (!metadata) {
    return false;
  }

  if (metadata.execute_roles.includes("all")) {
    return true;
  }

  return metadata.execute_roles.some((role) => userRoles.has(role));
};

const getDisabledWorkflowReason = (
  metadata: WorkflowCatalogEntry | undefined,
  isUnauthorized: boolean
): string => {
  if (isUnauthorized) {
    return "Unauthorized";
  }

  if (!metadata) {
    return "Workflow metadata is unavailable.";
  }

  if (
    metadata.has_form === null ||
    (metadata.has_form && metadata.form_id === null)
  ) {
    return WORKFLOW_FORM_API_UPGRADE_REQUIRED;
  }

  const executeRoles = metadata.execute_roles;
  if (executeRoles.length === 0) {
    return "Required execute roles are not configured.";
  }

  return `Required execute roles: ${executeRoles.join(", ")}`;
};

export type WorkflowExecutePermission =
  | { allowed: true }
  | {
      allowed: false;
      /** Why not; the launcher's tooltip and the form route show this text. */
      reason: string;
    };

/**
 * Whether the current user may start a workflow, as the launcher and the form ID
 * form route decide it. The server still enforces execute roles on submit.
 *
 * @param metadata Catalog entry; `undefined` when the catalog has none.
 * @param userRoles Roles from `/whoami`.
 * @param isUnauthorized `/whoami` reported an authentication or authorization failure.
 */
export const getWorkflowExecutePermission = (
  metadata: WorkflowCatalogEntry | undefined,
  userRoles: ReadonlySet<string>,
  isUnauthorized: boolean
): WorkflowExecutePermission =>
  !isUnauthorized &&
  metadata?.has_form !== null &&
  metadata?.form_id !== null &&
  canExecuteWorkflow(metadata, userRoles)
    ? { allowed: true }
    : {
        allowed: false,
        reason: getDisabledWorkflowReason(metadata, isUnauthorized),
      };

export interface WorkflowLauncherItem {
  /** Workflow class name. */
  name: string;
  /** Shown title. */
  display_name: string;
  group: string;
  href: string;
  /**
   * The catalog entry, or `undefined` when the catalog has no entry for an overridden
   * built-in (not loaded yet, failed to load, or not registered). The launcher then
   * lists the workflow disabled, as it always has.
   */
  metadata: WorkflowCatalogEntry | undefined;
}

export interface WorkflowLauncherSection {
  group: string;
  items: WorkflowLauncherItem[];
}

const getOverride = (
  overrides: WorkflowLauncherOverrides,
  name: string
): WorkflowLauncherOverride | undefined =>
  Object.prototype.hasOwnProperty.call(overrides, name)
    ? overrides[name]
    : undefined;

/**
 * Launcher entries in display order: catalog workflows unless they explicitly disable
 * forms, plus overridden built-ins, minus the ones an override hides. Legacy or malformed
 * catalog entries remain visible but disabled so their compatibility error is actionable.
 */
export const buildWorkflowLauncherItems = (
  catalog: readonly WorkflowCatalogEntry[],
  overrides: WorkflowLauncherOverrides
): WorkflowLauncherItem[] => {
  const catalogByName = new Map(catalog.map((entry) => [entry.name, entry]));
  const names = new Set([...catalogByName.keys(), ...Object.keys(overrides)]);
  const items: WorkflowLauncherItem[] = [];

  for (const name of names) {
    const entry = catalogByName.get(name);
    const override = getOverride(overrides, name);
    if (
      override?.hidden ||
      entry?.has_form === false
    ) {
      continue;
    }

    items.push({
      name,
      display_name: entry?.display_name || override?.title || name,
      group: entry?.group ?? DEFAULT_WORKFLOW_GROUP,
      href: entry?.form_id ? workflowFormPath(entry.form_id) : "",
      metadata: entry,
    });
  }

  return sortLauncherItems(items);
};

const launcherCollator = new Intl.Collator("en", { numeric: true });

const sortLauncherItems = (
  items: readonly WorkflowLauncherItem[]
): WorkflowLauncherItem[] =>
  [...items].sort((a, b) => {
    const byDisplayName = launcherCollator.compare(
      a.display_name,
      b.display_name
    );
    return byDisplayName || launcherCollator.compare(a.name, b.name);
  });

/**
 * Split launcher items into alphabetically ordered sections, sort each section by
 * display name, and put the default section last.
 */
export const groupWorkflowLauncherItems = (
  items: readonly WorkflowLauncherItem[]
): WorkflowLauncherSection[] => {
  const sections = new Map<string, WorkflowLauncherItem[]>();
  for (const item of items) {
    const section = sections.get(item.group);
    if (section) {
      section.push(item);
    } else {
      sections.set(item.group, [item]);
    }
  }

  const ordered = [...sections]
    .map(([group, sectionItems]) => ({
      group,
      items: sortLauncherItems(sectionItems),
    }))
    .sort((a, b) => launcherCollator.compare(a.group, b.group));
  return [
    ...ordered.filter((section) => section.group !== DEFAULT_WORKFLOW_GROUP),
    ...ordered.filter((section) => section.group === DEFAULT_WORKFLOW_GROUP),
  ];
};
