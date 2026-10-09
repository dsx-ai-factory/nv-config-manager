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
import { existsSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import legacyWorkflowRedirects from "@/config/legacy-workflow-redirects.json";
import workflowFormIds from "@/config/workflow-form-ids.json";
import {
  buildWorkflowRedirects,
  workflowFormPath,
} from "@/config/workflow-redirects.mjs";

import nextConfig from "../../next.config.mjs";

const UI_ROOT = fileURLToPath(new URL("../../", import.meta.url));
describe("legacy workflow redirects", () => {
  it("declares a unique, URL-safe slug for every legacy route", () => {
    const slugs = Object.values(legacyWorkflowRedirects);
    expect(new Set(slugs).size).toBe(slugs.length);
    for (const slug of slugs) expect(slug).toMatch(/^[a-z0-9]+$/);
  });

  it("declares one unique lowercase kebab-case form ID per legacy workflow", () => {
    expect(Object.keys(workflowFormIds).sort()).toEqual(
      Object.keys(legacyWorkflowRedirects).sort()
    );
    const formIds = Object.values(workflowFormIds);
    expect(new Set(formIds).size).toBe(formIds.length);
    for (const formId of formIds) {
      expect(formId).toMatch(/^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/);
    }
  });

  it("does not retain implementations behind the redirected URLs", () => {
    for (const legacySlug of Object.values(legacyWorkflowRedirects)) {
      expect(
        existsSync(
          join(UI_ROOT, "src/app/workflows", legacySlug, "form", "page.tsx")
        ),
        legacySlug
      ).toBe(false);
    }
  });
});

describe("buildWorkflowRedirects", () => {
  it("redirects nothing when there are no legacy URLs", () => {
    expect(buildWorkflowRedirects({}, {})).toEqual([]);
  });

  it("fails explicitly when a legacy workflow has no form ID", () => {
    expect(() =>
      buildWorkflowRedirects({ DeployWorkflow: "deployworkflow" }, {})
    ).toThrow("Missing workflow form ID for DeployWorkflow");
  });

  it("redirects legacy URLs to encoded form ID routes", () => {
    const redirects = {
      DeployWorkflow: "deployworkflow",
      "Acme Audit/Workflow": "acmeauditworkflow",
    };

    const formIds: Record<string, string> = {
      DeployWorkflow: "deploy",
      "Acme Audit/Workflow": "acme-audit",
    };

    expect(buildWorkflowRedirects(redirects, formIds)).toEqual([
      {
        source: "/workflows/deployworkflow/form",
        destination: "/workflows/new/deploy",
        permanent: false,
      },
      {
        source: "/workflows/acmeauditworkflow/form",
        destination: "/workflows/new/acme-audit",
        permanent: false,
      },
    ]);
    for (const [name, legacySlug] of Object.entries(redirects)) {
      expect(
        buildWorkflowRedirects(
          { [name]: legacySlug },
          { [name]: formIds[name] }
        )[0].destination
      ).toBe(workflowFormPath(formIds[name]));
    }
  });
});

describe("next.config.mjs redirects()", () => {
  it("serves redirects for every previously shipped form URL", async () => {
    const redirects = await nextConfig.redirects?.();

    expect(redirects).toEqual(
      buildWorkflowRedirects(legacyWorkflowRedirects, workflowFormIds)
    );
    expect(redirects).toEqual(
      Object.entries(legacyWorkflowRedirects).map(([name, legacySlug]) => ({
        source: `/workflows/${legacySlug}/form`,
        destination: `/workflows/new/${
          workflowFormIds[name as keyof typeof workflowFormIds]
        }`,
        permanent: false,
      }))
    );
    expect(redirects).toHaveLength(Object.keys(legacyWorkflowRedirects).length);
  });
});
