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

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  buildWorkflowFormUrl,
  fetchWorkflowForm,
  parseWorkflowFormResponse,
  SUPPORTED_CAPABILITIES,
} from "@/lib/workflow-form";
import { WORKFLOW_FORM_FIXTURES } from "@/mocks/data/workflowForms";

const API_URL = "http://localhost:9000";

const validForm = () => ({
  schema: {
    type: "object",
    properties: { site: { type: "string", title: "Site" }, site_type: { type: "string", title: "Site Type" } },
    required: ["site"],
  },
  ui_schema: {
    "ui:order": ["site", "*"],
    "ui:globalOptions": { hideSchemaDescriptions: true },
    site: {
      "ui:field": "location",
      "ui:options": {
        source: {
          endpoint: "/v1/parameter/location",
          label_key: "name",
          value_key: "id",
          type_key: "location_type",
        },
        typeField: "site_type",
      },
    },
    site_type: { "ui:widget": "hidden" },
  },
  ui_schema_version: 1,
  requires: ["core-field.location.v1", "theme.hide-schema-descriptions.v1"],
  ui_component: null,
});

const CANONICAL_UI_DIR = new URL(
  "../../../packages/workflows/src/nv_config_manager_workflows/ui/",
  import.meta.url
);
const read = (url: URL) => readFileSync(fileURLToPath(url), "utf8");

describe("v1 contract artifacts", () => {
  it.each(["workflow-form-v1.schema.json", "workflow-form-capabilities-v1.json"])(
    "keeps the UI copy of %s byte-identical to the canonical file",
    (file) => {
      expect(read(new URL(`../../src/lib/${file}`, import.meta.url))).toBe(
        read(new URL(file, CANONICAL_UI_DIR))
      );
    }
  );

  it("supports every capability in the manifest", () => {
    const manifest = JSON.parse(read(new URL("workflow-form-capabilities-v1.json", CANONICAL_UI_DIR)));
    expect([...SUPPORTED_CAPABILITIES].sort()).toEqual([...manifest.capabilities].sort());
  });
});

describe("parseWorkflowFormResponse", () => {
  it("accepts a valid v1 envelope unchanged", () => {
    expect(parseWorkflowFormResponse(validForm())).toEqual({ ok: true, form: validForm() });
  });

  it.each(Object.entries(WORKFLOW_FORM_FIXTURES))("accepts the %s mock", (_name, form) => {
    expect(parseWorkflowFormResponse(structuredClone(form)).ok).toBe(true);
  });

  it("reports an unsupported version before validating the envelope", () => {
    expect(parseWorkflowFormResponse({ ui_schema_version: 2, ui_schema: "nonsense" })).toEqual({
      ok: false,
      kind: "unsupported",
      version: 2,
      capabilities: [],
    });
  });

  it("reports unknown capabilities as needing a newer UI, not as malformed", () => {
    const form = { ...validForm(), requires: ["core-field.location.v1", "core-field.row-editor.v1"] };
    expect(parseWorkflowFormResponse(form)).toEqual({
      ok: false,
      kind: "unsupported",
      capabilities: ["core-field.row-editor.v1"],
    });
  });

  it("checks capabilities against the given supported set", () => {
    expect(parseWorkflowFormResponse(validForm(), new Set(["core-field.location.v1"]))).toMatchObject({
      kind: "unsupported",
      capabilities: ["theme.hide-schema-descriptions.v1"],
    });
  });

  it.each([
    ["a missing envelope key", (form: ReturnType<typeof validForm>) => ({ ...form, requires: undefined })],
    ["an unknown ui_schema key", (form: ReturnType<typeof validForm>) => ({ ...form, ui_schema: { ...form.ui_schema, "ui:groups": [] } })],
    ["an unknown widget", (form: ReturnType<typeof validForm>) => ({ ...form, ui_schema: { ...form.ui_schema, site_type: { "ui:widget": "slider" } } })],
    ["a location without a source", (form: ReturnType<typeof validForm>) => ({ ...form, ui_schema: { ...form.ui_schema, site: { "ui:field": "location", "ui:options": {} } } })],
    ["an empty ui_component", (form: ReturnType<typeof validForm>) => ({ ...form, ui_component: "" })],
  ])("rejects %s as malformed with issues", (_label, mutate) => {
    const result = parseWorkflowFormResponse(mutate(validForm()));
    expect(result).toMatchObject({ ok: false, kind: "malformed" });
    expect(!result.ok && result.kind === "malformed" && result.issues.length).toBeGreaterThan(0);
  });
});

describe("buildWorkflowFormUrl", () => {
  it("encodes the workflow name as one path segment", () => {
    expect(buildWorkflowFormUrl(`${API_URL}/`, "a/b c?d")).toBe(
      "http://localhost:9000/v1/workflow/a%2Fb%20c%3Fd/form"
    );
  });
});

describe("fetchWorkflowForm", () => {
  const jsonResponse = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

  const stubFetch = (impl: () => Promise<unknown>) => {
    const fetchMock = vi.fn(impl);
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  };

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns the validated form and requests the encoded URL with credentials", async () => {
    const fetchMock = stubFetch(async () => jsonResponse(validForm()));

    expect(await fetchWorkflowForm(API_URL, "Hello World")).toEqual({ kind: "ok", form: validForm() });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:9000/v1/workflow/Hello%20World/form",
      expect.objectContaining({ credentials: "include" })
    );
  });

  it.each([
    [404, "not_found"],
    [401, "unauthorized"],
    [403, "unauthorized"],
    [500, "http_error"],
    [503, "http_error"],
  ])("maps HTTP %i to %s", async (status, kind) => {
    stubFetch(async () => jsonResponse({ detail: "nope" }, status));

    expect((await fetchWorkflowForm(API_URL, "BackupWorkflow")).kind).toBe(kind);
  });

  it("maps a plugin form diagnostic (503 workflow_form_unavailable) to unavailable", async () => {
    stubFetch(async () =>
      jsonResponse(
        {
          detail: {
            code: "workflow_form_unavailable",
            plugin: "acme",
            workflow: "AcmeWorkflow",
            message: "ui_schema.device: unknown ui:field 'gizmo'",
          },
        },
        503
      )
    );

    const result = await fetchWorkflowForm(API_URL, "AcmeWorkflow");

    expect(result).toMatchObject({
      kind: "unavailable",
      diagnostic: "ui_schema.device: unknown ui:field 'gizmo'",
    });
    expect(result.kind === "unavailable" && result.message).toMatch(/plugin "acme"/);
  });

  it("maps an unknown version or capability to unsupported with an actionable message", async () => {
    stubFetch(async () => jsonResponse({ ...validForm(), ui_schema_version: 2 }));
    const version = await fetchWorkflowForm(API_URL, "BackupWorkflow");
    expect(version.kind === "unsupported" && version.message).toMatch(/version 2.*supports version 1.*Upgrade/);

    stubFetch(async () => jsonResponse({ ...validForm(), requires: ["core-field.row-editor.v1"] }));
    const capability = await fetchWorkflowForm(API_URL, "BackupWorkflow");
    expect(capability.kind === "unsupported" && capability.message).toMatch(
      /a capability this UI does not support \(core-field\.row-editor\.v1\)\. Upgrade/
    );
  });

  it("maps an SSO redirect (opaque status 0) to unauthorized", async () => {
    stubFetch(async () => ({ status: 0, ok: false, json: async () => ({}) }));
    expect(await fetchWorkflowForm(API_URL, "BackupWorkflow")).toMatchObject({ kind: "unauthorized", status: 401 });
  });

  it("maps a rejected fetch to network_error and a non-JSON body to malformed", async () => {
    stubFetch(async () => {
      throw new TypeError("Failed to fetch");
    });
    expect((await fetchWorkflowForm(API_URL, "BackupWorkflow")).kind).toBe("network_error");

    stubFetch(async () => new Response("<html>proxy</html>", { status: 200 }));
    expect((await fetchWorkflowForm(API_URL, "BackupWorkflow")).kind).toBe("malformed");
  });

  it.each(["", ".", ".."])("does not request a path for the name %j", async (name) => {
    const fetchMock = stubFetch(async () => jsonResponse(validForm()));

    expect((await fetchWorkflowForm(API_URL, name)).kind).toBe("not_found");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
