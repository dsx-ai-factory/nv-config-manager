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
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { getWorkflowTypeFilterOptions } from "@/app/workflows/columns";
import { normalizeWorkflowCatalog } from "@/lib/workflow-catalog";
import type { WorkflowCatalogResponseWire } from "@/types/workflow-catalog.types";

const serverMetadata = JSON.parse(
  readFileSync(
    fileURLToPath(
      new URL(
        "../../../src/tests/temporal/api/fixtures/workflow_metadata_baseline.json",
        import.meta.url
      )
    ),
    "utf8"
  )
) as WorkflowCatalogResponseWire;

describe("getWorkflowTypeFilterOptions", () => {
  it("offers every catalog workflow in server order, as before", () => {
    expect(getWorkflowTypeFilterOptions(normalizeWorkflowCatalog(serverMetadata))).toEqual(
      serverMetadata.workflows.map((workflow) => ({
        label: workflow.display_name,
        value: workflow.name,
      }))
    );
  });
});
