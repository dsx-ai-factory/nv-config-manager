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

import {
  WORKFLOW_ROUTES,
  getWorkflowRoute,
  workflowHref,
  type WorkflowRoutes,
} from "@/config/workflow-routes";

const UI_ROOT = fileURLToPath(new URL("../../", import.meta.url));
const INVENTORY_DIR = join(UI_ROOT, "tests/e2e/fixtures/workflow-form-inventory");

const readInventory = (): Array<{ slug: string; workflow: string }> =>
  readdirSync(INVENTORY_DIR)
    .filter((file) => file.endsWith(".json"))
    .map((file) => JSON.parse(readFileSync(join(INVENTORY_DIR, file), "utf8")));

describe("WORKFLOW_ROUTES", () => {
  it("maps every inventoried legacy form to its slug; every form is migrated", () => {
    const inventory = readInventory();
    expect(inventory).toHaveLength(26);

    expect(WORKFLOW_ROUTES).toEqual(
      Object.fromEntries(
        inventory.map(({ slug, workflow }) => [
          workflow,
          {
            legacySlug: slug,
            migrated: true,
          },
        ])
      )
    );
  });

  it("does not retain implementations behind migrated legacy routes", () => {
    for (const { legacySlug } of Object.values(WORKFLOW_ROUTES)) {
      expect(
        existsSync(join(UI_ROOT, "src/app/workflows", legacySlug, "form", "page.tsx")),
        legacySlug
      ).toBe(false);
    }
  });
});

describe("workflowHref", () => {
  const routes: WorkflowRoutes = {
    DeployWorkflow: { legacySlug: "deployworkflow", migrated: false },
    IBPKeyCreationWorkflow: { legacySlug: "ibpkeycreationworkflow", migrated: true },
  };

  it("links an unmigrated workflow to its legacy page", () => {
    expect(workflowHref("DeployWorkflow", routes)).toBe("/workflows/deployworkflow/form");
  });

  it("links a migrated workflow to the class-name route", () => {
    expect(workflowHref("IBPKeyCreationWorkflow", routes)).toBe(
      "/workflows/new/IBPKeyCreationWorkflow"
    );
  });

  it("links an unmapped (plugin) workflow to the encoded class-name route", () => {
    expect(workflowHref("AcmeFabricAuditWorkflow", routes)).toBe(
      "/workflows/new/AcmeFabricAuditWorkflow"
    );
    expect(workflowHref("Acme/Audit Workflow?x", routes)).toBe(
      "/workflows/new/Acme%2FAudit%20Workflow%3Fx"
    );
  });

  it("does not treat inherited object keys as mapped workflows", () => {
    for (const name of ["constructor", "toString", "__proto__"]) {
      expect(getWorkflowRoute(name, routes)).toBeUndefined();
      expect(workflowHref(name, routes)).toBe(`/workflows/new/${name}`);
    }
  });

  it("uses the shipped map by default", () => {
    expect(workflowHref("ValidateHardwareWorkflow")).toBe(
      "/workflows/new/ValidateHardwareWorkflow"
    );
    expect(workflowHref("BackupWorkflow")).toBe("/workflows/new/BackupWorkflow");
    expect(workflowHref("AcmeFabricAuditWorkflow")).toBe(
      "/workflows/new/AcmeFabricAuditWorkflow"
    );
  });
});
