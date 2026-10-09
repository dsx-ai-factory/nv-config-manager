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

import {
  DEFAULT_WORKFLOW_GROUP,
  buildWorkflowCatalogUrl,
  isLegacyWorkflowCatalog,
  normalizeWorkflowCatalog,
  normalizeWorkflowCatalogEntry,
} from "@/lib/workflow-catalog";
import { workflowMetadata as mswWorkflowMetadata } from "@/mocks/handlers/workflowHandlers";
import type { WorkflowCatalogEntryWire } from "@/types/workflow-catalog.types";

const wire = (
  overrides: Partial<WorkflowCatalogEntryWire> = {}
): WorkflowCatalogEntryWire => ({
  name: "BackupWorkflow",
  display_name: "Configuration Backup",
  description: "Back up a device.",
  endpoint: "/ngc/backup",
  namespace: "ngc",
  cli_name: "backup",
  input_class: "BackupInput",
  read_roles: ["all"],
  execute_roles: ["nvcm-network"],
  ...overrides,
});

describe("normalizeWorkflowCatalogEntry", () => {
  it("fails closed when the server omits form availability", () => {
    const normalized = normalizeWorkflowCatalogEntry(wire());

    expect(normalized).toEqual({
      ...wire(),
      form_id: null,
      has_form: null,
      group: DEFAULT_WORKFLOW_GROUP,
    });
  });

  it("treats null like absent", () => {
    const normalized = normalizeWorkflowCatalogEntry(
      wire({
        form_id: null,
        has_form: null,
        group: null,
      })
    );

    expect(normalized).toMatchObject({
      form_id: null,
      has_form: null,
      group: DEFAULT_WORKFLOW_GROUP,
    });
  });

  it("keeps explicit values emitted by the backend", () => {
    const normalized = normalizeWorkflowCatalogEntry(
      wire({
        form_id: "config-backup",
        has_form: false,
        group: "Backups",
      })
    );

    expect(normalized).toMatchObject({
      form_id: "config-backup",
      has_form: false,
      group: "Backups",
    });
  });

  it("falls back to defaults for wrongly typed optional fields", () => {
    const normalized = normalizeWorkflowCatalogEntry({
      ...wire(),
      form_id: "Not/A/Stable/ID",
      has_form: "yes",
      group: "",
    } as unknown as WorkflowCatalogEntryWire);

    expect(normalized).toMatchObject({
      form_id: null,
      has_form: null,
      group: DEFAULT_WORKFLOW_GROUP,
    });
  });

  it("does not alter the nine existing metadata fields", () => {
    const original = wire();
    const normalized = normalizeWorkflowCatalogEntry(original);

    for (const key of Object.keys(original) as Array<keyof typeof original>) {
      expect(normalized[key]).toEqual(original[key]);
    }
  });
});

describe("has_form availability", () => {
  it("does not infer a generic form from input_class", () => {
    expect(
      normalizeWorkflowCatalogEntry(wire({ input_class: "BackupInput" }))
        .has_form
    ).toBe(null);
    expect(
      normalizeWorkflowCatalogEntry(wire({ input_class: "Unknown" })).has_form
    ).toBe(null);
    expect(
      normalizeWorkflowCatalogEntry(
        wire({ input_class: "Unknown", has_form: true })
      ).has_form
    ).toBe(true);
    expect(
      normalizeWorkflowCatalogEntry(
        wire({ input_class: "BackupInput", has_form: false })
      ).has_form
    ).toBe(false);
  });
});

describe("normalizeWorkflowCatalog", () => {
  it("returns an empty catalog for a missing response", () => {
    expect(normalizeWorkflowCatalog(undefined)).toEqual([]);
    expect(normalizeWorkflowCatalog(null)).toEqual([]);
  });

  it("normalises the MSW /metadata mock and preserves declared form availability", () => {
    const catalog = normalizeWorkflowCatalog(mswWorkflowMetadata);
    const workflowsWithoutForms = new Set([
      "HelloWorld",
      "HelloWorldApproval",
      "NVLinkSwitchFirmwareUpgradeWorkflow",
      "RedfishProvisioningWorkflow",
      "SpXOverlayAssignmentWorkflow",
    ]);

    expect(catalog).toHaveLength(mswWorkflowMetadata.workflows.length);
    expect(catalog.map((workflow) => workflow.name)).toEqual(
      mswWorkflowMetadata.workflows.map((workflow) => workflow.name)
    );
    expect(
      catalog.every(
        (workflow) =>
          workflow.has_form === !workflowsWithoutForms.has(workflow.name) &&
          workflow.group === DEFAULT_WORKFLOW_GROUP
      )
    ).toBe(true);
  });
});

describe("isLegacyWorkflowCatalog", () => {
  it("recognizes a real pre-form API response", () => {
    const catalog = normalizeWorkflowCatalog({
      workflows: [wire(), wire({ name: "DeployWorkflow" })],
    });

    expect(isLegacyWorkflowCatalog(catalog)).toBe(true);
  });

  it("does not treat an empty, current, or mixed catalog as a legacy API", () => {
    const current = normalizeWorkflowCatalog({
      workflows: [wire({ form_id: "backup", has_form: true })],
    });
    const mixed = normalizeWorkflowCatalog({
      workflows: [
        wire(),
        wire({
          name: "DeployWorkflow",
          form_id: "deploy",
          has_form: true,
        }),
      ],
    });

    expect(isLegacyWorkflowCatalog([])).toBe(false);
    expect(isLegacyWorkflowCatalog(current)).toBe(false);
    expect(isLegacyWorkflowCatalog(mixed)).toBe(false);
  });
});

describe("buildWorkflowCatalogUrl", () => {
  it("joins the API URL with the metadata path like the existing callers", () => {
    expect(buildWorkflowCatalogUrl("http://localhost:9000")).toBe(
      "http://localhost:9000/v1/workflow/metadata"
    );
    expect(buildWorkflowCatalogUrl("http://localhost:9000/")).toBe(
      "http://localhost:9000/v1/workflow/metadata"
    );
  });
});
