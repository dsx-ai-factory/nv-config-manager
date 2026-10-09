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
 * Workflow form URLs and redirects from previously shipped per-workflow form URLs.
 * Plain ESM so `next.config.mjs` can import it at build time.
 */

/**
 * Class-name form route that serves every browser-launchable workflow.
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
 * Next.js `redirects()` entries: each previously shipped form URL goes to its
 * class-name route. Temporary (307) for the compatibility window. Next.js passes the
 * request's query string through, including repeated parameters such as `device-id`.
 *
 * @param {Readonly<Record<string, string>>} redirects Workflow class name to legacy slug.
 * @returns {{source: string, destination: string, permanent: false}[]}
 */
export const buildWorkflowRedirects = (redirects) =>
  Object.entries(redirects).map(([name, legacySlug]) => ({
    source: legacyWorkflowFormPath(legacySlug),
    destination: workflowFormPath(name),
    permanent: false,
  }));
