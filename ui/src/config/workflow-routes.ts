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
 * Per-workflow form migration map: workflow class name → its legacy form page slug and
 * whether the form has moved to the class-name route. The data lives in
 * `workflow-routes.json` so `next.config.mjs` can read it to generate redirects (see
 * `workflow-redirects.mjs`, which also builds the URLs). Flipping `migrated` moves a
 * form after the backend schema supports it.
 */
import { legacyWorkflowFormPath, workflowFormPath } from "./workflow-redirects.mjs";
import workflowRoutesJson from "./workflow-routes.json";

export { legacyWorkflowFormPath, workflowFormPath };

export interface WorkflowRoute {
  /** Directory of the legacy page under `/workflows/<legacySlug>/form`. */
  legacySlug: string;
  /** `true` once the workflow is served from `/workflows/new/<ClassName>`. */
  migrated: boolean;
}

export type WorkflowRoutes = Readonly<Record<string, WorkflowRoute>>;

export const WORKFLOW_ROUTES: WorkflowRoutes = workflowRoutesJson;

/** Migration entry for a workflow class name; `undefined` for unmapped (plugin) workflows. */
export const getWorkflowRoute = (
  name: string,
  routes: WorkflowRoutes = WORKFLOW_ROUTES
): WorkflowRoute | undefined =>
  Object.prototype.hasOwnProperty.call(routes, name) ? routes[name] : undefined;

/**
 * Where to start a workflow: its legacy page until it is migrated, otherwise (and for
 * any workflow missing from the map, such as plugin workflows) the class-name route.
 */
export const workflowHref = (
  name: string,
  routes: WorkflowRoutes = WORKFLOW_ROUTES
): string => {
  const route = getWorkflowRoute(name, routes);
  return route && !route.migrated
    ? legacyWorkflowFormPath(route.legacySlug)
    : workflowFormPath(name);
};
