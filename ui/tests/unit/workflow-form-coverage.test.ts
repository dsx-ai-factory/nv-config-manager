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
 * Coverage policy (plan section 20): every workflow with a `/form` has captured
 * parity scenarios. `formParity.spec.ts` checks these fixtures against its live
 * scenario definitions and executes every definition on the form ID route.
 */
import { readFileSync, readdirSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { SERVER_FORM_EXCLUSIONS, SERVER_WORKFLOW_FORMS } from "./server-snapshot";

interface ParityFixture {
  workflow: string;
  scenarios: Array<{
    name: string;
    generic_payload: Record<string, unknown> | null;
  }>;
}

const fixtures = readdirSync(
  new URL("../e2e/fixtures/form-parity", import.meta.url)
)
  .filter((file) => file.endsWith(".json"))
  .map(
    (file) =>
      JSON.parse(
        readFileSync(
          new URL(`../e2e/fixtures/form-parity/${file}`, import.meta.url),
          "utf8"
        )
      ) as ParityFixture
  );

describe("workflow form coverage", () => {
  it("has captured parity scenarios for exactly the workflows with a /form", () => {
    expect(fixtures.map(({ workflow }) => workflow).sort()).toEqual(
      Object.keys(SERVER_WORKFLOW_FORMS).sort()
    );
    for (const excluded of Object.keys(SERVER_FORM_EXCLUSIONS)) {
      expect(SERVER_WORKFLOW_FORMS).not.toHaveProperty(excluded);
    }
  });

  it.each(fixtures)("$workflow has nonempty captured submissions", (fixture) => {
    expect(fixture.scenarios.length).toBeGreaterThan(0);
    for (const scenario of fixture.scenarios) {
      expect(scenario.name).not.toBe("");
      expect(scenario.generic_payload).not.toBeNull();
      expect(typeof scenario.generic_payload).toBe("object");
    }
  });
});
