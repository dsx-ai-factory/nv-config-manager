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
 * The server's `/form` snapshot and form exclusions, kept by the API tests outside
 * `ui/` (`src/tests/temporal/api/fixtures/`).
 */
import { readFileSync } from "node:fs";

import type { WorkflowFormResponse } from "@/types/workflow-catalog.types";

const readApiFixture = <T>(file: string): T =>
  JSON.parse(
    readFileSync(
      new URL(`../../../src/tests/temporal/api/fixtures/${file}`, import.meta.url),
      "utf8"
    )
  ) as T;

/** `GET /v1/workflow/{form_id}/form` of every built-in API workflow with a form. */
export const SERVER_WORKFLOW_FORMS: Readonly<Record<string, WorkflowFormResponse>> =
  readApiFixture("workflow_forms.json");

/** Built-in API workflows deliberately without a form, with the reason. */
export const SERVER_FORM_EXCLUSIONS: Readonly<Record<string, string>> = readApiFixture(
  "workflow_form_exclusions.json"
);

/** Frozen stable form IDs asserted by the backend's built-in form tests. */
export const SERVER_WORKFLOW_FORM_IDS: Readonly<Record<string, string>> = JSON.parse(
  readFileSync(
    new URL(
      "../../../packages/workflows/tests/fixtures/builtin_form_ids.json",
      import.meta.url
    ),
    "utf8"
  )
) as Record<string, string>;
