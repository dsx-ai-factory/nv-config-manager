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
import { setupServer } from "msw/node";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import {
  SITE_FILTER_SOURCE,
  STATUS_FILTER_SOURCE,
  TENANT_FILTER_SOURCE,
} from "@/components/forms/workflow-rjsf/fields/device-field";
import { mockApiURL } from "@/config/mockApiUrl";
import { fetcher } from "@/lib/fetcher";
import {
  buildOptionSourceRequest,
  mapOptionEnvelope,
  mapOptionRows,
} from "@/lib/option-source";
import { fetchWorkflowForm } from "@/lib/workflow-form";
import { WORKFLOW_FORM_FIXTURES } from "@/mocks/data/workflowForms";
import { handlers } from "@/mocks/handlers";
import type { OptionSource } from "@/types/workflow-catalog.types";

import { SERVER_WORKFLOW_FORMS } from "./server-snapshot";

describe("MSW /form fixtures", () => {
  it("are a verbatim copy of the server snapshot", () => {
    expect(WORKFLOW_FORM_FIXTURES).toEqual(SERVER_WORKFLOW_FORMS);
  });
});

// Drives the dev-server MSW handlers through the real loader, in Node.
const server = setupServer(...handlers);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());

describe("MSW GET /v1/workflow/:name/form", () => {
  it.each(Object.keys(WORKFLOW_FORM_FIXTURES))("serves the %s fixture", async (name) => {
    expect(await fetchWorkflowForm(mockApiURL, name)).toEqual({
      kind: "ok",
      form: WORKFLOW_FORM_FIXTURES[name],
    });
  });

  it("answers 404 for an unknown workflow, not the /v1/workflow/:id handler", async () => {
    expect((await fetchWorkflowForm(mockApiURL, "NoSuchWorkflow")).kind).toBe("not_found");
  });
});

/** Values that fill every dependency of every sample, known to the mocks. */
const FILLED = {
  site: "PDX01",
  site_type: "Site",
  device_id: "1",
  device_ids: ["1"],
  location: "PDX01",
  status: ["Active"],
};

const sources: Array<[string, OptionSource]> = [
  ["device Site filter", SITE_FILTER_SOURCE],
  ["device Tenant filter", TENANT_FILTER_SOURCE],
  ["device Status filter", STATUS_FILTER_SOURCE],
  ...Object.entries(WORKFLOW_FORM_FIXTURES).flatMap(([name, form]) =>
    Object.entries(form.ui_schema).flatMap(([property, ui]) => {
      const source = (ui as { "ui:options"?: { source?: OptionSource } })["ui:options"]?.source;
      return source ? [[`${name}.${property}`, source] as [string, OptionSource]] : [];
    })
  ),
];

describe("MSW parameter endpoints referenced by the /form samples", () => {
  it.each(sources)("serves options for %s", async (_label, source) => {
    const request = buildOptionSourceRequest(mockApiURL, source, FILLED);
    if (request.kind !== "ready") throw new Error(JSON.stringify(request));

    const response = await fetcher(request.url);
    const mapping =
      source.response === "options-v1"
        ? mapOptionEnvelope(response)
        : mapOptionRows(response, source.label_key, source.value_key, source.type_key);

    expect(mapping?.options.length).toBeGreaterThan(0);
  });
});
