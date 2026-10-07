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
 * Workflow form URLs and the legacy-page redirects derived from the migration map
 * (`workflow-routes.json`). Plain ESM so `next.config.mjs` can import it at build time;
 * the app uses it through `workflow-routes.ts`.
 */

/**
 * Class-name form route that serves migrated and plugin workflows.
 * @param {string} name Workflow class name.
 * @returns {string}
 */
export const workflowFormPath = (name) => `/workflows/new/${encodeURIComponent(name)}`;

/**
 * Legacy per-workflow form page.
 * @param {string} slug Directory of the page under `src/app/workflows/`.
 * @returns {string}
 */
export const legacyWorkflowFormPath = (slug) => `/workflows/${slug}/form`;

/**
 * Next.js `redirects()` entries: each migrated workflow's legacy form page goes to its
 * class-name route. Temporary (307) for the migration window. Next.js passes the
 * request's query string through, repeated parameters such as `device-id` included.
 *
 * @param {Readonly<Record<string, {legacySlug: string, migrated: boolean}>>} routes
 * @returns {{source: string, destination: string, permanent: false}[]}
 */
export const buildWorkflowRedirects = (routes) =>
  Object.entries(routes)
    .filter(([, route]) => route.migrated)
    .map(([name, route]) => ({
      source: legacyWorkflowFormPath(route.legacySlug),
      destination: workflowFormPath(name),
      permanent: false,
    }));
