// @vitest-environment jsdom
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
 * The controlled RJSF shell and its core fields in jsdom, against a fake workflow API
 * whose responses tests can hold back and release to force orderings. User edits are
 * driven through the form context, the way core fields write.
 */
import * as React from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type { FormProps } from "@rjsf/core";
import { SWRConfig } from "swr";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FormUnavailable } from "@/components/forms/workflow/form-load-error";
import type { ShellFormContext } from "@/components/forms/workflow-rjsf/context";
import { RJSF_DEFAULT_STATE_BEHAVIOR } from "@/components/forms/workflow-rjsf/state";
import { WorkflowRjsfForm } from "@/components/forms/workflow-rjsf/workflow-rjsf-form";
import { WORKFLOW_FORM_FIXTURES } from "@/mocks/data/workflowForms";
import type {
  WorkflowCatalogEntry,
  WorkflowFormResponse,
} from "@/types/workflow-catalog.types";

// Record every <Form> render's props: the form context drives user edits, and the
// props show what RJSF was given.
const rendered = vi.hoisted(() => ({ props: [] as FormProps[] }));
vi.mock("@rjsf/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@rjsf/core")>();
  const { createElement, forwardRef } = await import("react");
  const Form = forwardRef<unknown, FormProps>((props, ref) => {
    rendered.props.push(props);
    return createElement(actual.default, { ...props, ref } as never);
  });
  return { ...actual, default: Form };
});

const API = "http://api.test";

// Radix primitives measure themselves; jsdom has no ResizeObserver.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

/** Labels with exactly this text (a required marker allowed). */
const labels = (text: string) =>
  screen.queryAllByText(new RegExp(`^${text}( \\*)?$`), { selector: "label" });

const labelledControl = (text: string): HTMLElement => {
  const id = labels(text)[0]?.getAttribute("for");
  const control = id ? document.getElementById(id) : null;
  if (!control) throw new Error(`No control is associated with the ${text} label`);
  return control;
};

const lastProps = () => rendered.props[rendered.props.length - 1];
const context = () => lastProps().formContext as ShellFormContext;
const formData = () => lastProps().formData as Record<string, unknown>;
const pending = () => [...context().pending].sort();

// ---------------------------------------------------------------------------
// Fake workflow API
// ---------------------------------------------------------------------------

const DEVICES: Record<string, Array<{ id: string; name: string }>> = {
  "": [{ id: "d9", name: "unfiltered-9" }],
  PDX01: [
    { id: "d1", name: "pdx-1" },
    { id: "d2", name: "pdx-2" },
  ],
  RNO1: [{ id: "d3", name: "rno-3" }],
};

type Route = (
  url: URL,
  init?: RequestInit
) => { status: number; body: unknown } | undefined;

let requests: URL[];
let posts: Array<{ url: URL; body: unknown }>;
let held: Array<{ url: URL; release: () => void }>;
let holdIf: (url: URL) => boolean;
let override: Route;

const defaultRoute = (url: URL): { status: number; body: unknown } => {
  const path = url.pathname;
  const ok = (body: unknown) => ({ status: 200, body });
  if (path === "/api/config") return ok({ workflowApiUrl: API });
  if (path === "/v1/parameter/location") {
    return ok([
      { id: "PDX01", name: "PDX01", location_type: "Site" },
      { id: "RNO1", name: "RNO1", location_type: "Site" },
      { id: "42", name: "Pod 42", location_type: "Module" },
    ]);
  }
  if (path === "/v1/parameter/tenant")
    return ok([{ name: "TenantA" }, { name: "TenantB" }]);
  if (path === "/v1/parameter/status")
    return ok([{ name: "Active" }, { name: "Planned" }]);
  if (path === "/v1/parameter/device") {
    if (url.searchParams.get("role") === "UFM")
      return ok([{ id: "ufm1", name: "ufm-1" }]);
    return ok(DEVICES[url.searchParams.get("site") ?? ""] ?? []);
  }
  if (path === "/v1/parameter/overlay")
    return ok([{ name: `ov-${url.searchParams.get("location")}` }]);
  if (path === "/v1/parameter/namespace-tag")
    return ok([{ name: "spectrumx" }, { name: "ns1" }]);
  if (/^\/v1\/parameter\/device\/[^/]+\/interfaces$/.test(path))
    return ok([{ name: "eth0" }]);
  if (path.startsWith("/v1/workflow/")) return ok({ id: "run-1" });
  return { status: 404, body: { detail: "not found" } };
};

const fakeFetch = (input: RequestInfo | URL, init?: RequestInit) => {
  const url = new URL(String(input), "http://localhost");
  requests.push(url);
  if (init?.method === "post")
    posts.push({ url, body: JSON.parse(String(init.body)) });
  const respond = () => {
    const { status, body } = override(url, init) ?? defaultRoute(url);
    return { ok: status < 400, status, json: async () => body };
  };
  if (holdIf(url)) {
    return new Promise((resolve) =>
      held.push({ url, release: () => resolve(respond()) })
    );
  }
  return Promise.resolve(respond());
};

/** Release held responses matching `match`, in arrival order, and let effects run. */
const release = async (match: (url: URL) => boolean = () => true) => {
  const due = held.filter(({ url }) => match(url));
  held = held.filter((entry) => !due.includes(entry));
  holdIf = () => false;
  for (const { release: send } of due) await act(async () => send());
};

const deviceRequests = () =>
  requests.filter((url) => url.pathname === "/v1/parameter/device");
const param = (url: URL, name: string) => url.searchParams.getAll(name);

beforeEach(() => {
  rendered.props = [];
  requests = [];
  posts = [];
  held = [];
  holdIf = () => false;
  override = () => undefined;
  vi.stubGlobal("fetch", vi.fn(fakeFetch));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const entry = (name: string): WorkflowCatalogEntry =>
  ({
    name,
    display_name: name,
    endpoint: `/${name.toLowerCase()}`,
  } as WorkflowCatalogEntry);

const renderForm = async (
  form: WorkflowFormResponse,
  search = "",
  name = "TestWorkflow"
) => {
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <WorkflowRjsfForm
        entry={entry(name)}
        form={structuredClone(form)}
        searchParams={new URLSearchParams(search)}
      />
    </SWRConfig>
  );
  await act(async () => {});
};

const submitButton = () =>
  screen.getByRole("button", { name: /submit|create pkey/i });

const device = (filters: string[], extra: Record<string, unknown> = {}) => ({
  "ui:field": "device",
  "ui:options": {
    source: {
      endpoint: "/v1/parameter/device",
      label_key: "name",
      value_key: "id",
      params: { managed_only: true },
    },
    filters,
    siteRequired: true,
    ...extra,
  },
});

// ---------------------------------------------------------------------------

describe("initial state and RJSF agreement", () => {
  it("renders with schema defaults and passes the shared default-state behavior", async () => {
    await renderForm(WORKFLOW_FORM_FIXTURES.DeployWorkflow);

    expect(rendered.props[0].formData).toEqual({ commit_confirm: true });
    // RJSF's mount-time defaults add nothing the shell did not already have.
    expect(formData()).toEqual({ commit_confirm: true });
    for (const props of rendered.props) {
      expect(props.experimental_defaultFormStateBehavior).toBe(
        RJSF_DEFAULT_STATE_BEHAVIOR
      );
    }
  });

  it("hides schema descriptions only when ui:globalOptions asks", async () => {
    await renderForm(WORKFLOW_FORM_FIXTURES.DeployWorkflow);
    expect(
      screen.queryByText(/Whether to use commit-confirmed mode/)
    ).toBeNull();
    expect(
      screen.getByText(/Rollback if device becomes unreachable/)
    ).toBeTruthy();
    cleanup();

    await renderForm(WORKFLOW_FORM_FIXTURES.BackupWorkflow);
    expect(
      screen.getByText("Identifier of the network device to back up.")
    ).toBeTruthy();
  });

  it("starts a required minItems list as an empty list", async () => {
    await renderForm(WORKFLOW_FORM_FIXTURES.SpXOverlayTenantChangeWorkflow);
    expect(formData().port_names).toEqual([]);
  });

  it("uses ui:submitButtonOptions for the submit label", async () => {
    await renderForm(WORKFLOW_FORM_FIXTURES.IBPKeyCreationWorkflow);
    expect(screen.getByRole("button", { name: "Create PKey" })).toBeTruthy();
  });

  it("keeps the required marker out of accessible names and marks the control required", async () => {
    await renderForm(WORKFLOW_FORM_FIXTURES.IBPKeyCreationWorkflow);
    // Visible "UFM Host *", but announced as "UFM Host" plus required.
    expect(labels("UFM Host")[0].textContent).toBe("UFM Host *");
    const host = screen.getByRole("textbox", { name: "UFM Host" });
    expect(host.getAttribute("aria-required")).toBe("true");
    const pkey = screen.getByRole("textbox", { name: "PKey (optional)" });
    expect(pkey.hasAttribute("aria-required")).toBe(false);
  });

  it("associates picker labels, descriptions, and required state with their buttons", async () => {
    await renderForm(WORKFLOW_FORM_FIXTURES.BackupWorkflow);

    const devicePicker = labelledControl("Device");
    expect(devicePicker.tagName).toBe("BUTTON");
    expect(labels("Device")[0].textContent).toBe("Device *");
    expect(devicePicker.getAttribute("aria-describedby")).toContain(
      `${devicePicker.id}__description`
    );
    expect(document.getElementById(`${devicePicker.id}__description`)?.textContent).toBe(
      "Identifier of the network device to back up."
    );
  });
});

describe("exclusive input modes", () => {
  const mac = "00:11:22:33:44:55";

  it("disables the inactive Port LLDP mode and re-enables it after clearing", async () => {
    await renderForm(WORKFLOW_FORM_FIXTURES.PortLLDPInfoWorkflow);
    const interfaceInput = screen.getByRole("textbox", { name: "Interface" });
    const macInput = screen.getByRole("textbox", { name: "MAC Address" });

    expect((interfaceInput as HTMLInputElement).disabled).toBe(false);
    expect((macInput as HTMLInputElement).disabled).toBe(false);

    await act(async () => {
      context().setFilters(
        "implicit:device_id",
        { site: { id: "PDX01", type: "Site" } },
        "user"
      );
    });
    await waitFor(() =>
      expect((macInput as HTMLInputElement).disabled).toBe(true)
    );

    await act(async () => {
      context().setFilters("implicit:device_id", { site: undefined }, "user");
    });
    await waitFor(() =>
      expect((macInput as HTMLInputElement).disabled).toBe(false)
    );

    fireEvent.change(macInput, { target: { value: mac } });
    await waitFor(() =>
      expect((interfaceInput as HTMLInputElement).disabled).toBe(true)
    );
    expect(
      (lastProps().uiSchema?.device_id as { "ui:disabled"?: boolean })[
        "ui:disabled"
      ]
    ).toBe(true);

    fireEvent.change(macInput, { target: { value: "" } });
    await waitFor(() =>
      expect((interfaceInput as HTMLInputElement).disabled).toBe(false)
    );

    fireEvent.change(interfaceInput, { target: { value: "swp1" } });
    await waitFor(() =>
      expect((macInput as HTMLInputElement).disabled).toBe(true)
    );
  });
});

describe("device filter scopes", () => {
  it("keeps a scope pending while one requested filter is still loading, then loads devices", async () => {
    holdIf = (url) => url.pathname === "/v1/parameter/tenant";
    await renderForm(
      WORKFLOW_FORM_FIXTURES.BackupWorkflow,
      "site=PDX01&tenant=TenantA&device-id=d2"
    );

    await waitFor(() =>
      expect(
        requests.some((url) => url.pathname === "/v1/parameter/location")
      ).toBe(true)
    );
    expect(pending()).toEqual(["field:device_id", "scope:implicit:device_id"]);
    expect(deviceRequests()).toEqual([]);
    expect((submitButton() as HTMLButtonElement).disabled).toBe(true);

    await release();

    await waitFor(() => expect(formData().device_id).toBe("d2"));
    expect(pending()).toEqual([]);
    expect(context().filters["implicit:device_id"]).toEqual({
      site: { id: "PDX01", type: "Site" },
      tenant: ["TenantA"],
      status: [],
    });
    const [request] = deviceRequests();
    expect(param(request, "site")).toEqual(["PDX01"]);
    expect(param(request, "site_type")).toEqual(["Site"]);
    expect(param(request, "tenant")).toEqual(["TenantA"]);
    expect((submitButton() as HTMLButtonElement).disabled).toBe(false);
  });

  it("renders a shared scope once and settles its filters before its devices", async () => {
    const form: WorkflowFormResponse = {
      schema: {
        type: "object",
        properties: {
          ufm_device_id: { type: "string", title: "UFM" },
          switch_device_ids: {
            type: "array",
            items: { type: "string" },
            minItems: 1,
            title: "Switches",
          },
        },
        required: ["ufm_device_id", "switch_device_ids"],
      },
      ui_schema: {
        "ui:order": ["ufm_device_id", "switch_device_ids"],
        ufm_device_id: {
          "ui:field": "device",
          "ui:options": {
            source: {
              endpoint: "/v1/parameter/device",
              label_key: "name",
              value_key: "id",
              params: { role: "UFM" },
            },
            filters: ["site", "tenant"],
            siteRequired: true,
            filterScope: "fabric",
          },
        },
        switch_device_ids: device(["site", "tenant"], {
          filterScope: "fabric",
          queryParam: "device-id",
        }),
      },
      ui_schema_version: 1,
      requires: ["core-field.device.v1"],
    };
    holdIf = (url) => url.pathname === "/v1/parameter/tenant";
    await renderForm(
      form,
      "site=PDX01&tenant=TenantA&device-id=d1&device-id=d2&device-id=zzz"
    );

    expect(labels("Site")).toHaveLength(1);
    expect(labels("Tenant \\(optional\\)")).toHaveLength(1);
    expect(deviceRequests()).toEqual([]);

    await release();

    await waitFor(() =>
      expect(formData().switch_device_ids).toEqual(["d1", "d2"])
    );
    expect(pending()).toEqual([]);
    const ufm = deviceRequests().find((url) => param(url, "role")[0] === "UFM");
    const switches = deviceRequests().find(
      (url) => param(url, "managed_only")[0] === "true"
    );
    for (const url of [ufm, switches]) {
      expect(url && param(url, "site")).toEqual(["PDX01"]);
      expect(url && param(url, "tenant")).toEqual(["TenantA"]);
    }
    expect(formData()).not.toHaveProperty("site");
    expect(formData()).not.toHaveProperty("tenant");
  });

  it("does not settle a device against an optional Site that is still pending", async () => {
    const form: WorkflowFormResponse = {
      schema: {
        type: "object",
        properties: {
          device_ids: {
            type: "array",
            items: { type: "string" },
            title: "Devices",
          },
        },
        required: ["device_ids"],
      },
      ui_schema: {
        device_ids: device(["site", "tenant"], {
          siteRequired: false,
          filterScope: "implicit:device_ids",
          queryParam: "device-id",
        }),
      },
      ui_schema_version: 1,
      requires: ["core-field.device.v1"],
    };
    holdIf = (url) => url.pathname === "/v1/parameter/location";
    await renderForm(form, "site=PDX01&device-id=d1");

    // The unfiltered list (without d1) loads while the Site prefill is still pending.
    await waitFor(() => expect(deviceRequests()).toHaveLength(1));
    await act(async () => {});
    expect(param(deviceRequests()[0], "site")).toEqual([]);
    expect(pending()).toEqual([
      "field:device_ids",
      "scope:implicit:device_ids",
    ]);

    await release();

    await waitFor(() => expect(formData().device_ids).toEqual(["d1"]));
    expect(pending()).toEqual([]);
  });

  it("clears the device when a filter changes, and keeps filters out of the payload", async () => {
    await renderForm(
      WORKFLOW_FORM_FIXTURES.BackupWorkflow,
      "site=PDX01&device-id=d1"
    );
    await waitFor(() => expect(formData().device_id).toBe("d1"));

    await act(async () =>
      context().setFilters("implicit:device_id", { status: ["Active"] }, "user")
    );
    await waitFor(() => expect(formData()).not.toHaveProperty("device_id"));

    await act(async () =>
      context().setFields("field:device_id", { device_id: "d2" }, "user")
    );
    fireEvent.click(submitButton());
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].url.pathname).toBe("/v1/workflow/testworkflow");
    expect(posts[0].body).toEqual({ device_id: "d2", trigger: "API" });
  });
});

describe("location and siteField", () => {
  it("prefills SpX tenant change from ?site=&device-id= through the location field", async () => {
    await renderForm(
      WORKFLOW_FORM_FIXTURES.SpXOverlayTenantChangeWorkflow,
      "site=PDX01&device-id=d1"
    );

    await waitFor(() => expect(formData().device_id).toBe("d1"));
    expect(formData()).toMatchObject({ site: "PDX01", site_type: "Site" });
    expect(pending()).toEqual([]);
    // Site comes from the location field: the device renders no Site control of its own.
    expect(labels("Site")).toHaveLength(1);
    const [request] = deviceRequests();
    expect(param(request, "site")).toEqual(["PDX01"]);
    expect(param(request, "site_type")).toEqual(["Site"]);
  });

  it("drops a pending device prefill absent from the options of a newly chosen Site", async () => {
    holdIf = (url) => url.pathname === "/v1/parameter/device";
    await renderForm(
      WORKFLOW_FORM_FIXTURES.SpXOverlayTenantChangeWorkflow,
      "site=PDX01&device-id=d1"
    );
    await waitFor(() => expect(deviceRequests()).toHaveLength(1));
    expect(formData().site).toBe("PDX01");

    await act(async () =>
      context().setFields(
        "field:site",
        { site: "RNO1", site_type: "Site" },
        "user"
      )
    );
    await waitFor(() => expect(deviceRequests()).toHaveLength(2));
    // The stale PDX01 response (which has d1) arrives first, then RNO1's.
    await release();

    await waitFor(() => expect(pending()).toEqual([]));
    expect(formData()).not.toHaveProperty("device_id");
    expect(
      rendered.props.some((props) => props.formData?.device_id === "d1")
    ).toBe(false);
  });

  it("never requests dependent options with a mismatched location id and type", async () => {
    await renderForm(WORKFLOW_FORM_FIXTURES.SpXOverlayDeletionWorkflow);
    await act(async () =>
      context().setFields(
        "field:site",
        { site: "PDX01", site_type: "Site" },
        "user"
      )
    );
    await act(async () =>
      context().setFields(
        "field:site",
        { site: "42", site_type: "Module" },
        "user"
      )
    );
    await act(async () =>
      context().setFields(
        "field:site",
        { site: undefined, site_type: undefined },
        "user"
      )
    );
    await act(async () => {});

    const pairs = requests
      .filter(
        (url) =>
          url.pathname === "/v1/parameter/overlay" ||
          url.pathname === "/v1/parameter/namespace-tag"
      )
      .map(
        (url) =>
          `${param(url, "location")[0] ?? ""}/${
            param(url, "location_type")[0] ?? ""
          }`
      );
    expect(new Set(pairs)).toEqual(new Set(["PDX01/Site", "42/Module", "/"]));
    expect(formData()).not.toHaveProperty("site_type");
  });

  it("settles a required dependency that settles empty, and a missed location", async () => {
    await renderForm(
      WORKFLOW_FORM_FIXTURES.SpXOverlayTenantChangeWorkflow,
      "site=NOPE&device-id=d1"
    );
    await waitFor(() => expect(pending()).toEqual([]));
    expect(formData()).not.toHaveProperty("site");
    expect(formData()).not.toHaveProperty("device_id");
    expect(deviceRequests()).toEqual([]);
  });
});

describe("apiOptions prefill", () => {
  it("matches a value (or an already-shipped alias) once its dependencies settle", async () => {
    await renderForm(
      WORKFLOW_FORM_FIXTURES.SpXOverlayDeletionWorkflow,
      "site=PDX01&overlay_id=ov-PDX01&namespace=ns1"
    );
    await waitFor(() => expect(pending()).toEqual([]));
    expect(formData()).toMatchObject({
      site: "PDX01",
      site_type: "Site",
      overlay_id: "ov-PDX01",
      namespace_tag: "ns1",
    });
  });

  it("settles a value absent from the options without writing it", async () => {
    await renderForm(
      WORKFLOW_FORM_FIXTURES.SpXOverlayDeletionWorkflow,
      "site=PDX01&overlay_id=nope"
    );
    await waitFor(() => expect(pending()).toEqual([]));
    expect(formData()).not.toHaveProperty("overlay_id");
  });

  it("settles when its options fail to load", async () => {
    override = (url) =>
      url.pathname === "/v1/parameter/overlay"
        ? { status: 500, body: {} }
        : undefined;
    await renderForm(
      WORKFLOW_FORM_FIXTURES.SpXOverlayDeletionWorkflow,
      "site=PDX01&overlay_id=ov-PDX01"
    );
    await waitFor(() => expect(pending()).toEqual([]));
    expect(formData()).not.toHaveProperty("overlay_id");
    expect(screen.getByText(/Could not load Overlay ID options/)).toBeTruthy();
  });

  it("ignores a prefill that arrives after the user picked a value", async () => {
    override = (url) =>
      url.pathname === "/v1/parameter/overlay"
        ? { status: 200, body: [{ name: "ov-PDX01" }, { name: "mine" }] }
        : undefined;
    holdIf = (url) => url.pathname === "/v1/parameter/overlay";
    await renderForm(
      WORKFLOW_FORM_FIXTURES.SpXOverlayDeletionWorkflow,
      "site=PDX01&overlay_id=ov-PDX01"
    );
    await waitFor(() => expect(held).toHaveLength(1));

    await act(async () =>
      context().setFields("field:overlay_id", { overlay_id: "mine" }, "user")
    );
    await release();

    expect(formData().overlay_id).toBe("mine");
    expect(pending()).toEqual([]);
  });
});

describe("setFields ownership through the context", () => {
  it("throws on a key outside the owner's property and declared sibling", async () => {
    await renderForm(WORKFLOW_FORM_FIXTURES.SpXOverlayTenantChangeWorkflow);
    expect(() =>
      context().setFields("field:device_id", { site: "PDX01" }, "user")
    ).toThrow(/may not write/);
    expect(() =>
      context().setFields("field:site", { overlay_id: "x" }, "user")
    ).toThrow(/may not write/);
  });
});

describe("submission errors", () => {
  const ready = async () => {
    await renderForm(
      WORKFLOW_FORM_FIXTURES.BackupWorkflow,
      "site=PDX01&device-id=d1"
    );
    await waitFor(() => expect(formData().device_id).toBe("d1"));
  };

  it("shows a 422 field error inline and clears it when the field changes", async () => {
    override = (url, init) =>
      init?.method === "post"
        ? {
            status: 422,
            body: {
              detail: [
                {
                  loc: ["body", "device_id"],
                  msg: "Device is offline",
                  type: "x",
                },
              ],
            },
          }
        : undefined;
    await ready();

    fireEvent.click(submitButton());
    expect(await screen.findByText("Device is offline")).toBeTruthy();
    const devicePicker = labelledControl("Device");
    expect(devicePicker.getAttribute("aria-describedby")).toContain(
      `${devicePicker.id}__error`
    );
    expect(document.getElementById(`${devicePicker.id}__error`)?.textContent).toContain(
      "Device is offline"
    );

    await act(async () =>
      context().setFields("field:device_id", { device_id: "d2" }, "user")
    );
    await waitFor(() =>
      expect(screen.queryByText("Device is offline")).toBeNull()
    );
    expect(document.getElementById(`${devicePicker.id}__error`)).toBeNull();
  });

  it("shows a string 422 detail as a form-level error", async () => {
    override = (url, init) =>
      init?.method === "post"
        ? { status: 422, body: { detail: "Cannot canonicalize input" } }
        : undefined;
    await ready();

    fireEvent.click(submitButton());
    expect(await screen.findByText("Cannot canonicalize input")).toBeTruthy();
    expect(screen.getByText("The workflow input is invalid")).toBeTruthy();
  });

  it("validates on submit and live afterwards with '<label> is required'", async () => {
    await renderForm(WORKFLOW_FORM_FIXTURES.SpXOverlayTenantChangeWorkflow);
    expect(screen.queryByText("Site is required")).toBeNull();

    fireEvent.click(submitButton());
    expect(await screen.findByText("Site is required")).toBeTruthy();
    expect(screen.getByText("Ports is required")).toBeTruthy();
    expect(posts).toEqual([]);

    await act(async () =>
      context().setFields(
        "field:site",
        { site: "PDX01", site_type: "Site" },
        "user"
      )
    );
    await waitFor(() =>
      expect(screen.queryByText("Site is required")).toBeNull()
    );
  });
});

describe("form-only minItems on an optional list", () => {
  // Like Device Status on the site workflows: a model default, and a form-only
  // `minItems: 1` that asks for at least one status.
  const statusForm: WorkflowFormResponse = {
    schema: {
      type: "object",
      properties: {
        status: {
          type: "array",
          items: { type: "string" },
          default: ["Active"],
          minItems: 1,
          title: "Status",
        },
      },
    },
    ui_schema: {
      status: {
        "ui:field": "apiOptions",
        "ui:title": "Device Status",
        "ui:options": {
          source: {
            endpoint: "/v1/parameter/status",
            label_key: "name",
            value_key: "name",
          },
        },
      },
    },
    ui_schema_version: 1,
    requires: ["core-field.api-options.v1"],
  };

  it("keeps a cleared list as [] so 'at least one' blocks the submission", async () => {
    await renderForm(statusForm);
    fireEvent.click(
      await screen.findByRole("button", { name: "Remove Active" })
    );
    await waitFor(() => expect(formData().status).toEqual([]));

    fireEvent.click(submitButton());
    expect(
      await screen.findByText("At least 1 Device Status is required")
    ).toBeTruthy();
    expect(posts).toEqual([]);
  });
});

describe("launcher load errors", () => {
  it("shows an unavailable plugin form's diagnostic with Return to Workflows and no Try again", () => {
    render(
      <FormUnavailable
        title="New Acme Workflow"
        error={{
          kind: "unavailable",
          message: "The form failed validation.",
          diagnostic: "ui_schema.x: bad",
        }}
        onRetry={() => {}}
      />
    );
    expect(screen.getByText("ui_schema.x: bad")).toBeTruthy();
    expect(screen.getByText("Return to Workflows")).toBeTruthy();
    expect(screen.queryByText("Try again")).toBeNull();
  });

  it("offers Try again only for transient failures", () => {
    render(
      <FormUnavailable
        title="New Acme Workflow"
        error={{ kind: "http_error", status: 500, message: "HTTP 500" }}
        onRetry={() => {}}
      />
    );
    expect(screen.getByText("Try again")).toBeTruthy();
    cleanup();

    render(
      <FormUnavailable
        title="New Acme Workflow"
        error={{ kind: "unsupported", message: "Upgrade the UI." }}
        onRetry={() => {}}
      />
    );
    expect(screen.getByText("This form needs a newer UI")).toBeTruthy();
    expect(screen.queryByText("Try again")).toBeNull();
  });
});
