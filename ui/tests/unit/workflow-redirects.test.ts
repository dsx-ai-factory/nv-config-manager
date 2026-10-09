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
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import legacyWorkflowRedirects from "@/config/legacy-workflow-redirects.json";
import {
  buildWorkflowRedirects,
  workflowFormPath,
} from "@/config/workflow-redirects.mjs";

import nextConfig from "../../next.config.mjs";

const UI_ROOT = fileURLToPath(new URL("../../", import.meta.url));
const INVENTORY_DIR = join(UI_ROOT, "tests/e2e/fixtures/workflow-form-inventory");

const readInventory = (): Array<{ slug: string; workflow: string }> =>
  readdirSync(INVENTORY_DIR)
    .filter((file) => file.endsWith(".json"))
    .map((file) => JSON.parse(readFileSync(join(INVENTORY_DIR, file), "utf8")));

describe("legacy workflow redirects", () => {
  it("maps every inventoried legacy form to its previously shipped slug", () => {
    expect(legacyWorkflowRedirects).toEqual(
      Object.fromEntries(readInventory().map(({ slug, workflow }) => [workflow, slug]))
    );
  });

  it("does not retain implementations behind the redirected URLs", () => {
    for (const legacySlug of Object.values(legacyWorkflowRedirects)) {
      expect(
        existsSync(join(UI_ROOT, "src/app/workflows", legacySlug, "form", "page.tsx")),
        legacySlug
      ).toBe(false);
    }
  });
});

describe("buildWorkflowRedirects", () => {
  it("redirects nothing when there are no legacy URLs", () => {
    expect(buildWorkflowRedirects({})).toEqual([]);
  });

  it("redirects legacy URLs to encoded class-name routes", () => {
    const redirects = {
      DeployWorkflow: "deployworkflow",
      "Acme Audit/Workflow": "acmeauditworkflow",
    };

    expect(buildWorkflowRedirects(redirects)).toEqual([
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
    for (const [name, legacySlug] of Object.entries(redirects)) {
      expect(buildWorkflowRedirects({ [name]: legacySlug })[0].destination).toBe(
        workflowFormPath(name)
      );
    }
  });
});

describe("next.config.mjs redirects()", () => {
  it("serves redirects for every previously shipped form URL", async () => {
    const redirects = await nextConfig.redirects?.();

    expect(redirects).toEqual(buildWorkflowRedirects(legacyWorkflowRedirects));
    expect(redirects).toEqual(
      Object.entries(legacyWorkflowRedirects).map(([name, legacySlug]) => ({
        source: `/workflows/${legacySlug}/form`,
        destination: `/workflows/new/${name}`,
        permanent: false,
      }))
    );
    expect(redirects).toHaveLength(Object.keys(legacyWorkflowRedirects).length);
  });
});
