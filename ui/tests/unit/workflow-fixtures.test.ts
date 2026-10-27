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
import { readFileSync, readdirSync } from "node:fs";

import { describe, expect, it } from "vitest";

import workflowFormIds from "@/config/workflow-form-ids.json";

describe("workflow summary fixtures", () => {
  it("keeps form-enabled workflow summaries complete and synchronized", () => {
    const canonicalDirectory = new URL(
      "../../../packages/workflows/tests/fixtures/workflow_summaries/",
      import.meta.url
    );
    const browserDirectory = new URL(
      "../../src/mocks/data/workflows/fixtures/",
      import.meta.url
    );
    const canonicalFiles = readdirSync(canonicalDirectory)
      .filter((file) => file.endsWith(".json"))
      .sort();
    const fixtureWorkflowTypes: string[] = [];

    expect(
      readdirSync(browserDirectory)
        .filter((file) => file.endsWith(".json"))
        .sort()
    ).toEqual(canonicalFiles);
    for (const file of canonicalFiles) {
      const canonicalFixture = readFileSync(new URL(file, canonicalDirectory));
      expect(readFileSync(new URL(file, browserDirectory))).toEqual(
        canonicalFixture
      );
      const collection = JSON.parse(canonicalFixture.toString()) as {
        workflows: Array<{ workflow_type: string }>;
      };
      const collectionWorkflowTypes = new Set(
        collection.workflows.map(({ workflow_type }) => workflow_type)
      );

      expect(collectionWorkflowTypes.size).toBe(1);
      fixtureWorkflowTypes.push(...collectionWorkflowTypes);
    }
    expect(fixtureWorkflowTypes.sort()).toEqual(
      Object.keys(workflowFormIds).sort()
    );
  });
});
