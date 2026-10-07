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
import type { WorkflowFormResponse } from "@/types/workflow-catalog.types";

import serverForms from "./workflowForms.json";

/**
 * `GET /v1/workflow/{name}/form` responses for the dev-server MSW mocks: a verbatim
 * copy of the server snapshot (`src/tests/temporal/api/fixtures/workflow_forms.json`,
 * which the browser bundle cannot import because it lives outside `ui/`).
 * `tests/unit/workflow-form-mocks.test.ts` fails when the copy drifts; re-copy the file
 * then. Playwright serves the snapshot itself (`SERVER_WORKFLOW_FORMS` in
 * `tests/e2e/shared/apiMocks.ts`).
 */
export const WORKFLOW_FORM_FIXTURES: Readonly<Record<string, WorkflowFormResponse>> =
  serverForms as unknown as Record<string, WorkflowFormResponse>;

/** Fixture for a workflow class name, or `undefined` (the mocks answer 404). */
export const getWorkflowFormFixture = (name: string): WorkflowFormResponse | undefined =>
  Object.prototype.hasOwnProperty.call(WORKFLOW_FORM_FIXTURES, name)
    ? WORKFLOW_FORM_FIXTURES[name]
    : undefined;
