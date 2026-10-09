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
 * Form-ID route that serves every browser-launchable workflow.
 * @param {string} formId Stable workflow form ID.
 * @returns {string}
 */
export const workflowFormPath = (formId) =>
  `/workflows/new/${encodeURIComponent(formId)}`;

/**
 * Legacy per-workflow form page.
 * @param {string} slug Directory of the page under `src/app/workflows/`.
 * @returns {string}
 */
export const legacyWorkflowFormPath = (slug) => `/workflows/${slug}/form`;

/**
 * Next.js `redirects()` entries: each previously shipped form URL goes to its
 * form ID route. Temporary (307) for the compatibility window. Next.js passes the
 * request's query string through, including repeated parameters such as `device-id`.
 *
 * @param {Readonly<Record<string, string>>} redirects Workflow class name to legacy slug.
 * @param {Readonly<Record<string, string>>} formIds Workflow class name to stable form ID.
 * @returns {{source: string, destination: string, permanent: false}[]}
 */
export const buildWorkflowRedirects = (redirects, formIds) =>
  Object.entries(redirects).map(([name, legacySlug]) => {
    const formId = formIds[name];
    if (!formId) throw new Error(`Missing workflow form ID for ${name}`);
    return {
      source: legacyWorkflowFormPath(legacySlug),
      destination: workflowFormPath(formId),
      permanent: false,
    };
  });
