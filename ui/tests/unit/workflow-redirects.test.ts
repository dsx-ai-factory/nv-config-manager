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
import { describe, expect, it } from "vitest";

import { buildWorkflowRedirects } from "@/config/workflow-redirects.mjs";
import { WORKFLOW_ROUTES, workflowHref, type WorkflowRoutes } from "@/config/workflow-routes";

import nextConfig from "../../next.config.mjs";

describe("buildWorkflowRedirects", () => {
  it("redirects nothing while no workflow is migrated", () => {
    expect(
      buildWorkflowRedirects({
        DeployWorkflow: { legacySlug: "deployworkflow", migrated: false },
        BackupWorkflow: { legacySlug: "backupworkflow", migrated: false },
      })
    ).toEqual([]);
    expect(buildWorkflowRedirects({})).toEqual([]);
  });

  it("redirects a migrated workflow's legacy page to its encoded class-name route", () => {
    const routes: WorkflowRoutes = {
      DeployWorkflow: { legacySlug: "deployworkflow", migrated: true },
      BackupWorkflow: { legacySlug: "backupworkflow", migrated: false },
      "Acme Audit/Workflow": { legacySlug: "acmeauditworkflow", migrated: true },
    };

    expect(buildWorkflowRedirects(routes)).toEqual([
      {
        source: "/workflows/deployworkflow/form",
        destination: "/workflows/new/DeployWorkflow",
        permanent: false,
      },
      {
        source: "/workflows/acmeauditworkflow/form",
        destination: "/workflows/new/Acme%20Audit%2FWorkflow",
        permanent: false,
      },
    ]);
    // The redirect lands where the launcher links.
    for (const name of ["DeployWorkflow", "Acme Audit/Workflow"]) {
      expect(buildWorkflowRedirects({ [name]: routes[name] })[0].destination).toBe(
        workflowHref(name, routes)
      );
    }
  });
});

describe("next.config.mjs redirects()", () => {
  it("serves the redirects generated from the shipped migration map", async () => {
    const redirects = await nextConfig.redirects?.();

    expect(redirects).toEqual(buildWorkflowRedirects(WORKFLOW_ROUTES));
    expect(redirects).toEqual(
      Object.entries(WORKFLOW_ROUTES)
        .filter(([, route]) => route.migrated)
        .map(([name, route]) => ({
          source: `/workflows/${route.legacySlug}/form`,
          destination: `/workflows/new/${name}`,
          permanent: false,
        }))
    );
    expect(redirects).toHaveLength(
      Object.values(WORKFLOW_ROUTES).filter((route) => route.migrated).length
    );
  });
});
