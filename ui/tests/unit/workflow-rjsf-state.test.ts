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
import type { RJSFSchema, RJSFValidationError } from "@rjsf/utils";
import { describe, expect, it } from "vitest";

import {
  buildLayout,
  checkFieldPatch,
} from "@/components/forms/workflow-rjsf/context";
import { buildPayload } from "@/components/forms/workflow-rjsf/payload";
import {
  initialPending,
  optionQueryValues,
  snapshotQuery,
  standardPrefill,
} from "@/components/forms/workflow-rjsf/prefill";
import { mapServerErrors } from "@/components/forms/workflow-rjsf/server-errors";
import {
  createInitialState,
  shellReducer,
  type ShellState,
} from "@/components/forms/workflow-rjsf/state";
import { effectiveOrder } from "@/components/forms/workflow-rjsf/ui-schema";
import {
  createCustomValidate,
  createTransformErrors,
  workflowValidator,
} from "@/components/forms/workflow-rjsf/validator";
import { WORKFLOW_FORM_FIXTURES } from "@/mocks/data/workflowForms";

const fixture = (name: string) => {
  const form = WORKFLOW_FORM_FIXTURES[name];
  return { schema: form.schema as RJSFSchema, uiSchema: form.ui_schema };
};
const query = (search: string) => snapshotQuery(new URLSearchParams(search));
const initial = (name: string, search = "") => {
  const { schema, uiSchema } = fixture(name);
  return createInitialState(
    schema,
    uiSchema,
    query(search),
    buildLayout(schema, uiSchema).exclusiveGroups
  );
};

const DEVICE_OPTIONS = {
  source: {
    endpoint: "/v1/parameter/device",
    label_key: "name",
    value_key: "id",
  },
  filters: ["site", "tenant", "status"],
  siteRequired: true,
};

/** A form with a shared scope ("fabric"), a private one, and standard fields. */
const scoped = {
  schema: {
    type: "object",
    properties: {
      note: { type: "string", title: "Note" },
      switch_device_ids: {
        type: "array",
        items: { type: "string" },
        title: "Switches",
      },
      ufm_device_id: { type: "string", title: "UFM" },
      backup_device_id: { type: "string", title: "Backup device" },
      count: { type: "integer", title: "Count" },
      dry_run: { type: "boolean", title: "Dry run" },
      tags: { type: "array", items: { type: "string" }, title: "Tags" },
      hidden_value: { type: "string", default: "x", title: "Hidden" },
    },
    required: ["switch_device_ids"],
  } as RJSFSchema,
  uiSchema: {
    "ui:order": ["note", "*", "ufm_device_id"],
    switch_device_ids: {
      "ui:field": "device",
      "ui:options": {
        ...DEVICE_OPTIONS,
        filterScope: "fabric",
        queryParam: "device-id",
      },
    },
    ufm_device_id: {
      "ui:field": "device",
      "ui:options": { ...DEVICE_OPTIONS, filterScope: "fabric" },
    },
    backup_device_id: {
      "ui:field": "device",
      "ui:options": {
        ...DEVICE_OPTIONS,
        filters: ["tenant"],
        filterScope: "implicit:backup_device_id",
        queryParam: "backup",
        queryAliases: ["backup-device"],
      },
    },
    hidden_value: { "ui:widget": "hidden" },
  },
};

describe("initial state", () => {
  it("accepts repeated and comma-separated values for multi-select option prefills", () => {
    const values = optionQueryValues(
      query("port_names=swp1%2C+swp2&port_names=swp3"),
      ["port_names"],
      true,
      ","
    );
    expect(values).toEqual(["swp1", "swp2", "swp3"]);
    expect(
      optionQueryValues(query("port_names=swp1%2Cswp2"), ["port_names"], true)
    ).toEqual(["swp1,swp2"]);
  });

  it("contains schema defaults before any field renders", () => {
    expect(initial("DeployWorkflow").formData).toEqual({
      commit_confirm: true,
    });
    expect(initial("BackupWorkflow").formData).toEqual({ trigger: "API" });
  });

  it("starts a required minItems list as [] and leaves optional lists absent", () => {
    const { formData } = initial("SpXOverlayTenantChangeWorkflow");
    expect(formData.port_names).toEqual([]);
    expect(formData).not.toHaveProperty("overlay_id");
    expect(
      createInitialState(scoped.schema, scoped.uiSchema, {}).formData
    ).not.toHaveProperty("tags");
  });

  it("keeps an optional nested object empty, so the untouched form validates", () => {
    const schema = {
      type: "object",
      $defs: {
        Ticket: {
          type: "object",
          title: "Ticket",
          properties: {
            platform: { type: "string", default: "jira", title: "Platform" },
            key: { type: "string", title: "Key" },
          },
          required: ["key"],
        },
      },
      properties: {
        name: { type: "string", default: "n", title: "Name" },
        ticket: { $ref: "#/$defs/Ticket", title: "Ticket" },
      },
    } as RJSFSchema;
    const { formData } = createInitialState(schema, {}, {});

    expect(formData).toEqual({ name: "n" });
    expect(workflowValidator.validateFormData(formData, schema).errors).toEqual(
      []
    );
  });

  it("lets explicit standard prefills win over defaults, coerced by schema type", () => {
    expect(
      initial("DeployWorkflow", "commit_confirm=false").formData.commit_confirm
    ).toBe(false);
    expect(
      standardPrefill(
        scoped.schema,
        scoped.uiSchema,
        query("note=hi&count=3&dry_run=true&tags=a&tags=b")
      )
    ).toEqual({ note: "hi", count: 3, dry_run: true, tags: ["a", "b"] });
    expect(
      standardPrefill(
        scoped.schema,
        scoped.uiSchema,
        query("count=1.5&dry_run=yes")
      )
    ).toEqual({});
  });

  it("never prefills hidden, core, or non-projected properties", () => {
    expect(
      standardPrefill(
        scoped.schema,
        scoped.uiSchema,
        query(
          "hidden_value=y&ufm_device_id=u&user=mallory&user_domain=evil&trigger=SCHEDULED"
        )
      )
    ).toEqual({});
    expect(
      initial("BackupWorkflow", "trigger=SCHEDULED&user=mallory").formData
    ).toEqual({
      trigger: "API",
    });
  });

  it("starts exactly the owners whose URL parameters are present", () => {
    const pending = (search: string) =>
      [...initialPending(scoped.schema, scoped.uiSchema, query(search))].sort();

    expect(pending("")).toEqual([]);
    expect(pending("device-id=a&device-id=b")).toEqual([
      "field:switch_device_ids",
    ]);
    // `ufm_device_id` has no queryParam: no prefill.
    expect(pending("ufm_device_id=u")).toEqual([]);
    expect(pending("backup-device=b")).toEqual(["field:backup_device_id"]);
    expect(pending("site=PDX01")).toEqual(["scope:fabric"]);
    expect(pending("tenant=T")).toEqual([
      "scope:fabric",
      "scope:implicit:backup_device_id",
    ]);
    expect(pending("site=")).toEqual([]);
  });

  it("gives the later exclusive mode precedence for conflicting URL prefills", () => {
    expect(
      initial(
        "PortLLDPInfoWorkflow",
        "interface=swp1&remote_mac_address=00%3A11%3A22%3A33%3A44%3A55"
      ).formData
    ).toEqual({ remote_mac_address: "00:11:22:33:44:55" });
  });

  it("leaves Site to the location field when a device uses siteField", () => {
    const { schema, uiSchema } = fixture("SpXOverlayTenantChangeWorkflow");
    expect(
      [
        ...initialPending(schema, uiSchema, query("site=PDX01&device-id=d1")),
      ].sort()
    ).toEqual(["field:device_id", "field:site"]);
  });
});

describe("layout", () => {
  it("expands ui:order wildcards in schema order", () => {
    expect(effectiveOrder(scoped.schema, scoped.uiSchema)).toEqual([
      "note",
      "switch_device_ids",
      "backup_device_id",
      "count",
      "dry_run",
      "tags",
      "hidden_value",
      "ufm_device_id",
    ]);
  });

  it("hosts each scope's filters at its first device in effective order", () => {
    expect(buildLayout(scoped.schema, scoped.uiSchema).scopeHosts).toEqual({
      fabric: "switch_device_ids",
      "implicit:backup_device_id": "backup_device_id",
    });
  });

  it("assigns a location's typeField sibling to the location's owner", () => {
    const { schema, uiSchema } = fixture("SpXOverlayTenantChangeWorkflow");
    expect(buildLayout(schema, uiSchema)).toMatchObject({
      owners: {
        site: "field:site",
        site_type: "field:site",
        device_id: "field:device_id",
        overlay_id: "field:overlay_id",
        port_names: "field:port_names",
      },
      typeFields: { site: "site_type" },
    });
  });
});

describe("setFields ownership", () => {
  const { schema, uiSchema } = fixture("SpXOverlayTenantChangeWorkflow");
  const layout = buildLayout(schema, uiSchema);

  it("allows the owner's property and its declared sibling", () => {
    expect(() =>
      checkFieldPatch(layout, schema, "field:site", {
        site: "PDX01",
        site_type: "Site",
      })
    ).not.toThrow();
  });

  it.each([
    ["another field's property", "field:site", { device_id: "d1" }],
    ["a sibling it does not declare", "field:device_id", { site_type: "Site" }],
    ["a property that is not projected", "field:site", { user: "mallory" }],
    ["anything, as a scope", "scope:implicit:device_id", { device_id: "d1" }],
  ] as const)("rejects %s", (_label, owner, patch) => {
    expect(() => checkFieldPatch(layout, schema, owner, patch)).toThrow(
      /may not write/
    );
  });
});

describe("shellReducer", () => {
  const state = (
    pending: string[] = [],
    formData = {},
    filters: ShellState["filters"] = {}
  ): ShellState => ({
    formData,
    pending: new Set(pending) as ShellState["pending"],
    filters,
    serverErrors: undefined,
  });

  it("applies a field patch atomically and ends its owner's pending state", () => {
    const next = shellReducer(state(["field:site"], { a: 1 }), {
      type: "field-patch",
      owner: "field:site",
      patch: { site: "42", site_type: "Module" },
      source: "prefill",
    });
    expect(next.formData).toEqual({ a: 1, site: "42", site_type: "Module" });
    expect(next.pending.size).toBe(0);
  });

  it("keeps an atomic patch when an RJSF edit from the same render arrives after it", () => {
    const rendered = { site: "PDX01", site_type: "Site", note: "" };
    let current = shellReducer(state([], rendered), {
      type: "field-patch",
      owner: "field:site",
      patch: { site: "42", site_type: "Module" },
      source: "user",
    });
    // RJSF computed this event from `rendered`, before the patch above re-rendered it.
    current = shellReducer(current, {
      type: "rjsf-change",
      base: rendered,
      next: { ...rendered, note: "typed" },
    });
    expect(current.formData).toEqual({
      site: "42",
      site_type: "Module",
      note: "typed",
    });
  });

  it("applies RJSF deletions and ignores events that change nothing", () => {
    const base = { a: 1, b: 2 };
    const start = state([], base);
    expect(
      shellReducer(start, { type: "rjsf-change", base, next: { a: 1 } })
        .formData
    ).toEqual({ a: 1 });
    expect(
      shellReducer(start, { type: "rjsf-change", base, next: { ...base } })
    ).toBe(start);
  });

  it("ignores a late prefill after a user edit or a settlement", () => {
    const edited = shellReducer(state(["field:device_id"]), {
      type: "field-patch",
      owner: "field:device_id",
      patch: { device_id: "mine" },
      source: "user",
    });
    const late = shellReducer(edited, {
      type: "field-patch",
      owner: "field:device_id",
      patch: { device_id: "from-url" },
      source: "prefill",
    });
    expect(late).toBe(edited);
    expect(late.formData.device_id).toBe("mine");

    const settled = shellReducer(state(["scope:s"]), {
      type: "settle",
      owner: "scope:s",
    });
    expect(
      shellReducer(settled, {
        type: "filter-patch",
        scope: "s",
        patch: { tenant: ["T"] },
        source: "prefill",
      })
    ).toBe(settled);
  });

  it("makes repeated settle calls harmless", () => {
    const once = shellReducer(state(["field:a", "field:b"]), {
      type: "settle",
      owner: "field:a",
    });
    const twice = shellReducer(once, { type: "settle", owner: "field:a" });
    expect(twice).toBe(once);
    expect([...twice.pending]).toEqual(["field:b"]);
  });

  it("keeps scope filters out of form data", () => {
    const next = shellReducer(state(["scope:s"]), {
      type: "filter-patch",
      scope: "s",
      patch: { site: { id: "PDX01", type: "Site" }, tenant: ["T"] },
      source: "prefill",
    });
    expect(next.filters.s).toEqual({
      site: { id: "PDX01", type: "Site" },
      tenant: ["T"],
      status: [],
    });
    expect(next.formData).toEqual({});
    expect(next.pending.size).toBe(0);
  });

  const exclusiveGroups = [
    {
      fields: ["device_id", "interface"],
      filterScopes: ["implicit:device_id"],
    },
    { fields: ["remote_mac_address"], filterScopes: [] },
  ];

  it("clears the inactive fields and filters when a mode is activated", () => {
    const withMac = state([], { remote_mac_address: "00:11:22:33:44:55" }, {});
    const deviceMode = shellReducer(withMac, {
      type: "filter-patch",
      scope: "implicit:device_id",
      patch: { site: { id: "PDX01", type: "Site" } },
      source: "user",
      exclusiveGroups,
    });
    expect(deviceMode.formData).toEqual({});
    expect(deviceMode.filters["implicit:device_id"]?.site?.id).toBe("PDX01");

    const macMode = shellReducer(deviceMode, {
      type: "rjsf-change",
      base: deviceMode.formData,
      next: { remote_mac_address: "00:11:22:33:44:55" },
      exclusiveGroups,
    });
    expect(macMode.formData).toEqual({
      remote_mac_address: "00:11:22:33:44:55",
    });
    expect(macMode.filters).toEqual({});
  });

  it("clears errors for fields removed by a filter-mode transition", () => {
    const withMac: ShellState = {
      ...state([], { remote_mac_address: "00:11:22:33:44:55" }),
      serverErrors: {
        __errors: ["choose one lookup mode"],
        remote_mac_address: { __errors: ["bad MAC"] },
        device_id: { __errors: ["bad device"] },
      } as unknown as ShellState["serverErrors"],
    };

    const next = shellReducer(withMac, {
      type: "filter-patch",
      scope: "implicit:device_id",
      patch: { site: { id: "PDX01", type: "Site" } },
      source: "user",
      exclusiveGroups,
    });

    expect(next.formData).toEqual({});
    expect(next.serverErrors).toEqual({
      device_id: { __errors: ["bad device"] },
    });
  });

  it("clears errors for every field changed by an RJSF mode transition", () => {
    const withDevice: ShellState = {
      ...state([], { device_id: "d1", interface: "swp1" }),
      serverErrors: {
        __errors: ["choose one lookup mode"],
        device_id: { __errors: ["bad device"] },
        interface: { __errors: ["bad interface"] },
        remote_mac_address: { __errors: ["bad MAC"] },
      } as unknown as ShellState["serverErrors"],
    };

    const next = shellReducer(withDevice, {
      type: "rjsf-change",
      base: withDevice.formData,
      next: {
        ...withDevice.formData,
        remote_mac_address: "00:11:22:33:44:55",
      },
      exclusiveGroups,
    });

    expect(next.formData).toEqual({
      remote_mac_address: "00:11:22:33:44:55",
    });
    expect(next.serverErrors).toBeUndefined();
  });

  it("ignores a late prefill from an inactive exclusive mode and settles it", () => {
    const withMac = state(["field:device_id"], {
      remote_mac_address: "00:11:22:33:44:55",
    });
    const late = shellReducer(withMac, {
      type: "field-patch",
      owner: "field:device_id",
      patch: { device_id: "from-url" },
      source: "prefill",
      exclusiveGroups,
    });

    expect(late.formData).toEqual(withMac.formData);
    expect(late.pending.size).toBe(0);
  });

  it("clears mapped server errors for changed keys and form-level errors on any change", () => {
    const withErrors: ShellState = {
      ...state([], { a: "1", b: "2" }),
      serverErrors: {
        __errors: ["root"],
        a: { __errors: ["bad a"] },
        b: { __errors: ["bad b"] },
      } as unknown as ShellState["serverErrors"],
    };
    const next = shellReducer(withErrors, {
      type: "rjsf-change",
      base: { a: "1", b: "2" },
      next: { a: "3", b: "2" },
    });
    expect(next.serverErrors).toEqual({ b: { __errors: ["bad b"] } });
  });
});

describe("buildPayload", () => {
  const schema = {
    type: "object",
    properties: {
      host: { type: "string" },
      pkey: { type: "string" },
      script: { type: "string" },
      blank_script: { type: "string" },
      site_type: { type: "string" },
      trigger: { type: "string", default: "API" },
      roles: { type: "array", items: { type: "string" } },
      names: { type: "array", items: { type: "string" } },
      defaulted: { type: "array", items: { type: "string" }, default: ["a"] },
      nested: { type: "object", properties: { x: { type: "string" } } },
    },
    required: ["names"],
  };
  const uiSchema = {
    script: { "ui:widget": "textarea" },
    blank_script: { "ui:widget": "textarea" },
    site_type: { "ui:widget": "hidden" },
    trigger: { "ui:widget": "hidden" },
  };

  it("submits projected properties only, shallowly cleaned", () => {
    expect(
      buildPayload(schema, uiSchema, {
        host: "  ufm1  ",
        pkey: "   ",
        script: "  line 1\n  line 2\n",
        blank_script: "  \n ",
        site_type: "Site",
        trigger: "API",
        roles: [],
        names: [],
        defaulted: [],
        nested: { x: "  keep  " },
        tenant: ["filters never reach the payload"],
        undefined_value: undefined,
      })
    ).toEqual({
      host: "ufm1",
      script: "  line 1\n  line 2\n",
      site_type: "Site",
      trigger: "API",
      names: [],
      defaulted: [],
      nested: { x: "  keep  " },
    });
  });
});

describe("mapServerErrors", () => {
  const { schema, uiSchema } = fixture("SpXOverlayTenantChangeWorkflow");
  const layout = buildLayout(schema, uiSchema);
  const map = (detail: unknown) =>
    mapServerErrors(schema, uiSchema, layout, detail);

  it("maps a string detail to a form-level error", () => {
    expect(map("Invalid PKey")).toEqual({ __errors: ["Invalid PKey"] });
  });

  it("maps list details by location, stripping body and ignoring extra keys", () => {
    expect(
      map([
        {
          loc: ["body", "device_id"],
          msg: "Field required",
          type: "missing",
          input: {},
        },
        { loc: ["body", "site_type"], msg: "Bad type" },
        { loc: ["body", "port_names", 1], msg: "Unknown port" },
        { loc: ["body", "user"], msg: "Not yours" },
        { loc: ["body"], msg: "Model invalid" },
      ])
    ).toEqual({
      __errors: ["user: Not yours", "Model invalid"],
      device_id: { __errors: ["Field required"] },
      site: { __errors: ["Bad type"] },
      port_names: { __errors: ["Item 2: Unknown port"] },
    });
  });

  it("keeps nested locations of standard fields", () => {
    const nested = {
      type: "object",
      properties: {
        rows: {
          type: "array",
          items: { type: "object", properties: { a: { type: "string" } } },
        },
      },
    };
    expect(
      mapServerErrors(nested, {}, buildLayout(nested, {}), [
        { loc: ["body", "rows", 0, "a"], msg: "Bad" },
      ])
    ).toEqual({ rows: { 0: { a: { __errors: ["Bad"] } } } });
  });

  it.each([
    [undefined],
    [null],
    [[]],
    [[{ msg: "no loc" }]],
    [{ detail: "object" }],
  ])("returns null for %j so the caller shows a toast", (detail) => {
    expect(map(detail)).toBeNull();
  });
});

describe("validation messages", () => {
  const { schema, uiSchema } = fixture("SpXOverlayTenantChangeWorkflow");
  const validate = (formData: Record<string, unknown>) =>
    workflowValidator.validateFormData(
      formData,
      schema,
      createCustomValidate(schema, uiSchema),
      createTransformErrors(schema, uiSchema),
      uiSchema
    ).errors;
  const messages = (errors: RJSFValidationError[]) =>
    errors.map((error) => error.message).sort();

  it("says '<label> is required' for missing values and empty required lists", () => {
    expect(messages(validate({ port_names: [] }))).toEqual([
      "Device is required",
      "Ports is required",
      "Site is required",
    ]);
  });

  it("treats a whitespace-only required string as missing", () => {
    expect(
      messages(validate({ site: "  ", device_id: "d1", port_names: ["p"] }))
    ).toEqual(["Site is required"]);
  });

  it("moves item errors of a whole-list control onto the list", () => {
    const errors = validate({ site: "s", device_id: "d", port_names: [1] });
    expect(errors).toEqual([
      expect.objectContaining({
        property: ".port_names",
        message: "Item 1: must be string",
      }),
    ]);
  });

  it("applies declared numeric field comparisons", () => {
    const spx = fixture("SpXOverlayCreationWorkflow");
    const errors = workflowValidator.validateFormData(
      { site: "s", overlay_id: "o", tenant: "t", rd_min: 65000, rd_max: 60000 },
      spx.schema,
      createCustomValidate(spx.schema, spx.uiSchema),
      createTransformErrors(spx.schema, spx.uiSchema),
      spx.uiSchema
    ).errors;

    expect(errors).toEqual([
      expect.objectContaining({
        property: ".rd_min",
        message: "RD Min must be less than RD Max",
      }),
    ]);
  });
});
