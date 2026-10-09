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
  normalizeWorkflowCatalog,
  normalizeWorkflowCatalogEntry,
  sortWorkflowCatalog,
} from "@/lib/workflow-catalog";
import { workflowMetadata as mswWorkflowMetadata } from "@/mocks/handlers/workflowHandlers";
import type {
  WorkflowCatalogEntry,
  WorkflowCatalogEntryWire,
} from "@/types/workflow-catalog.types";

const wire = (overrides: Partial<WorkflowCatalogEntryWire> = {}): WorkflowCatalogEntryWire => ({
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

const entry = (
  name: string,
  display_name: string,
  order?: number
): WorkflowCatalogEntry =>
  normalizeWorkflowCatalogEntry(wire({ name, display_name, order }));

describe("normalizeWorkflowCatalogEntry", () => {
  it("fails closed when the server omits form availability", () => {
    const normalized = normalizeWorkflowCatalogEntry(wire());

    expect(normalized).toEqual({
      ...wire(),
      plugin: null,
      tags: [],
      has_form: null,
      enabled: true,
      order: undefined,
      group: DEFAULT_WORKFLOW_GROUP,
    });
  });

  it("treats null like absent", () => {
    const normalized = normalizeWorkflowCatalogEntry(
      wire({
        plugin: null,
        tags: null,
        has_form: null,
          enabled: null,
        order: null,
        group: null,
      })
    );

    expect(normalized).toMatchObject({
      plugin: null,
      tags: [],
      has_form: null,
      enabled: true,
      order: undefined,
      group: DEFAULT_WORKFLOW_GROUP,
    });
  });

  it("keeps explicit values, including enabled=false and order=0", () => {
    const normalized = normalizeWorkflowCatalogEntry(
      wire({
        plugin: "nv-config-manager-acme",
        tags: ["network", "backup"],
        has_form: false,
        enabled: false,
        order: 0,
        group: "Backups",
      })
    );

    expect(normalized).toMatchObject({
      plugin: "nv-config-manager-acme",
      tags: ["network", "backup"],
      has_form: false,
      enabled: false,
      order: 0,
      group: "Backups",
    });
  });

  it("falls back to defaults for wrongly typed optional fields", () => {
    const normalized = normalizeWorkflowCatalogEntry({
      ...wire(),
      tags: ["ok", 3, null],
      has_form: "yes",
      enabled: "no",
      order: Number.NaN,
      group: "",
    } as unknown as WorkflowCatalogEntryWire);

    expect(normalized).toMatchObject({
      tags: ["ok"],
      has_form: null,
      enabled: true,
      order: undefined,
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
    expect(normalizeWorkflowCatalogEntry(wire({ input_class: "BackupInput" })).has_form).toBe(
      null
    );
    expect(normalizeWorkflowCatalogEntry(wire({ input_class: "Unknown" })).has_form).toBe(null);
    expect(
      normalizeWorkflowCatalogEntry(wire({ input_class: "Unknown", has_form: true }))
        .has_form
    ).toBe(true);
    expect(
      normalizeWorkflowCatalogEntry(wire({ input_class: "BackupInput", has_form: false }))
        .has_form
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
          workflow.enabled &&
          workflow.has_form === !workflowsWithoutForms.has(workflow.name) &&
          workflow.group === DEFAULT_WORKFLOW_GROUP &&
          workflow.order === undefined
      )
    ).toBe(true);
  });
});

describe("sortWorkflowCatalog", () => {
  it("puts explicit order first, then display_name, then name", () => {
    const sorted = sortWorkflowCatalog([
      entry("Zeta", "Alpha"),
      entry("Second", "Zulu", 2),
      entry("Beta", "Alpha"),
      entry("First", "Yankee", 1),
      entry("Other", "Bravo"),
      entry("Negative", "Xray", -5),
    ]);

    expect(sorted.map((workflow) => workflow.name)).toEqual([
      "Negative",
      "First",
      "Second",
      "Beta",
      "Zeta",
      "Other",
    ]);
  });

  it("orders display names naturally and is independent of input order", () => {
    const items = [
      entry("A10", "Workflow 10"),
      entry("A2", "Workflow 2"),
      entry("A1", "Workflow 1"),
    ];
    const expected = ["A1", "A2", "A10"];

    expect(sortWorkflowCatalog(items).map((w) => w.name)).toEqual(expected);
    expect(sortWorkflowCatalog([...items].reverse()).map((w) => w.name)).toEqual(
      expected
    );
  });

  it("does not mutate its input", () => {
    const items = [entry("B", "B"), entry("A", "A")];
    sortWorkflowCatalog(items);

    expect(items.map((workflow) => workflow.name)).toEqual(["B", "A"]);
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
