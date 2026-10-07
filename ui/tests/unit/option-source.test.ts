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
  buildOptionSourceRequest,
  dependencyFields,
  dependencySignature,
  deriveOptionSourceState,
  mapOptionRows,
  normalizeDependencyValue,
  resolveOptionSourceEndpoint,
  type OptionSourceRequest,
} from "@/lib/option-source";
import type { OptionSource } from "@/types/workflow-catalog.types";

const API_URL = "http://localhost:9000";

/** SpX-style cascade on a location field and its `site_type` sibling. */
const overlaySource: OptionSource = {
  endpoint: "/v1/parameter/overlay",
  label_key: "name",
  value_key: "name",
  params: { isolation_type: "spectrum_x_vrf" },
  depends_on: {
    location: { field: "site" },
    location_type: { field: "site_type", required: false },
  },
};

const deviceSource: OptionSource = {
  endpoint: "/v1/parameter/device",
  label_key: "name",
  value_key: "id",
  params: { managed_only: true },
};

const interfacesSource: OptionSource = {
  endpoint: "/v1/parameter/device/{device_id}/interfaces",
  label_key: "name",
  value_key: "name",
};

const readyUrl = (request: OptionSourceRequest): string => {
  if (request.kind !== "ready") throw new Error(`expected ready, got ${JSON.stringify(request)}`);
  return request.url;
};

describe("normalizeDependencyValue", () => {
  it.each([
    ["undefined", undefined],
    ["null", null],
    ["an empty string", ""],
    ["an empty array", []],
    ["an array of empties", ["", null]],
    ["NaN", Number.NaN],
    ["Infinity", Number.POSITIVE_INFINITY],
    ["an object", { id: "PDX01" }],
  ])("treats %s as empty", (_label, value) => {
    expect(normalizeDependencyValue(value)).toEqual([]);
  });

  it("sends strings as-is, including blank ones, and keeps false and 0", () => {
    // The contract's empty values are null, "" and []; whitespace is a value.
    expect(normalizeDependencyValue(" PDX01 ")).toEqual([" PDX01 "]);
    expect(normalizeDependencyValue("  ")).toEqual(["  "]);
    expect(normalizeDependencyValue(0)).toEqual(["0"]);
    expect(normalizeDependencyValue(false)).toEqual(["false"]);
  });

  it("sorts and de-duplicates array values, dropping empty items", () => {
    expect(normalizeDependencyValue(["b", "a", "b", 3, "", null])).toEqual(["3", "a", "b"]);
  });
});

describe("resolveOptionSourceEndpoint", () => {
  it("resolves a relative path under the API URL", () => {
    expect(resolveOptionSourceEndpoint(API_URL, "/v1/parameter/site")?.toString()).toBe(
      "http://localhost:9000/v1/parameter/site"
    );
    expect(
      resolveOptionSourceEndpoint(`${API_URL}/`, "/v1/parameter/site")?.toString()
    ).toBe("http://localhost:9000/v1/parameter/site");
  });

  it("keeps an API path prefix, like the existing apiURL + path convention", () => {
    expect(
      resolveOptionSourceEndpoint(
        "https://gateway.example.com/workflow-api",
        "/v1/parameter/site"
      )?.toString()
    ).toBe("https://gateway.example.com/workflow-api/v1/parameter/site");
  });

  it.each([
    ["an absolute URL", "https://evil.example.com/v1/parameter/site"],
    ["a protocol-relative URL", "//evil.example.com/v1/parameter/site"],
    ["a backslash authority", "/\\evil.example.com/x"],
    ["userinfo", "@evil.example.com/v1/parameter/site"],
    ["a path without a leading slash", "v1/parameter/site"],
    ["an empty endpoint", ""],
    ["an embedded query", "/v1/parameter/site?limit=1"],
    ["a fragment", "/v1/parameter/site#x"],
    ["whitespace", "/v1/parameter/ site"],
    ["a control character", "/v1/parameter/\nsite"],
  ])("rejects %s", (_label, endpoint) => {
    expect(resolveOptionSourceEndpoint(API_URL, endpoint)).toBeNull();
  });

  it("rejects dot segments that escape the API path prefix", () => {
    const apiURL = "https://gateway.example.com/workflow-api";

    expect(resolveOptionSourceEndpoint(apiURL, "/../admin")).toBeNull();
    expect(resolveOptionSourceEndpoint(apiURL, "/%2e%2e/admin")).toBeNull();
  });

  it("rejects everything when the API URL itself is unusable", () => {
    expect(resolveOptionSourceEndpoint("", "/v1/parameter/site")).toBeNull();
    expect(resolveOptionSourceEndpoint("not a url", "/v1/parameter/site")).toBeNull();
  });
});


describe("buildOptionSourceRequest", () => {
  it("sends static params when there are no dependencies", () => {
    expect(
      buildOptionSourceRequest(
        API_URL,
        { endpoint: "/v1/parameter/tenant", label_key: "name", value_key: "name" },
        {}
      )
    ).toEqual({ kind: "ready", url: "http://localhost:9000/v1/parameter/tenant" });
  });

  it("repeats static array params in declared order", () => {
    const request = buildOptionSourceRequest(
      API_URL,
      {
        endpoint: "/v1/parameter/location",
        label_key: "name",
        value_key: "id",
        type_key: "location_type",
        params: { location_type: ["Site", "Module"] },
      },
      {}
    );

    expect(readyUrl(request)).toBe(
      "http://localhost:9000/v1/parameter/location?location_type=Site&location_type=Module"
    );
  });

  it("merges static and dependency params in sorted order, repeating arrays", () => {
    const request = buildOptionSourceRequest(
      API_URL,
      {
        endpoint: "/v1/parameter/device",
        label_key: "name",
        value_key: "id",
        params: { status: "Active", managed_only: true, limit: 50 },
        depends_on: { site: { field: "site" }, role: { field: "roles" } },
      },
      { site: "PDX01", roles: ["spine", "leaf"] }
    );

    expect(readyUrl(request)).toBe(
      "http://localhost:9000/v1/parameter/device" +
        "?limit=50&managed_only=true&role=leaf&role=spine&site=PDX01&status=Active"
    );
  });

  it("reads a location's type from its typeField sibling and omits it when empty", () => {
    expect(readyUrl(buildOptionSourceRequest(API_URL, overlaySource, { site: "42", site_type: "Module" }))).toBe(
      "http://localhost:9000/v1/parameter/overlay" +
        "?isolation_type=spectrum_x_vrf&location=42&location_type=Module"
    );
    expect(readyUrl(buildOptionSourceRequest(API_URL, overlaySource, { site: "42" }))).toBe(
      "http://localhost:9000/v1/parameter/overlay?isolation_type=spectrum_x_vrf&location=42"
    );
  });

  it.each([
    ["absent", {}],
    ["null", { site: null }],
    ["an empty string", { site: "" }],
    ["an empty list", { site: [] }],
  ])("waits while a required dependency is %s", (_label, values) => {
    expect(buildOptionSourceRequest(API_URL, overlaySource, values)).toEqual({
      kind: "waiting",
      missingDependencies: ["site"],
    });
  });

  it("adds extra params (device scope filters) over static ones, repeating lists", () => {
    const request = buildOptionSourceRequest(API_URL, deviceSource, {}, {
      site: ["PDX01"],
      site_type: ["Site"],
      tenant: ["TenantB", "TenantA"],
      status: [],
    });

    expect(readyUrl(request)).toBe(
      "http://localhost:9000/v1/parameter/device" +
        "?managed_only=true&site=PDX01&site_type=Site&tenant=TenantB&tenant=TenantA"
    );
  });

  describe("path placeholders", () => {
    it("substitutes the field value as one URL-encoded segment", () => {
      expect(
        readyUrl(buildOptionSourceRequest(API_URL, interfacesSource, { device_id: "leaf 1/a?b#c" }))
      ).toBe("http://localhost:9000/v1/parameter/device/leaf%201%2Fa%3Fb%23c/interfaces");
    });

    it("is a required dependency, listed once with depends_on", () => {
      const source: OptionSource = {
        ...interfacesSource,
        depends_on: { device: { field: "device_id" }, site: { field: "site" } },
      };

      expect(buildOptionSourceRequest(API_URL, source, {})).toEqual({
        kind: "waiting",
        missingDependencies: ["device_id", "site"],
      });
      expect(dependencyFields(source)).toEqual(["device_id", "site"]);
    });

    it.each([".", ".."])("rejects the dot segment %j", (value) => {
      expect(buildOptionSourceRequest(API_URL, interfacesSource, { device_id: value }).kind).toBe(
        "invalid"
      );
    });

    it("rejects several values for one placeholder", () => {
      expect(
        buildOptionSourceRequest(API_URL, interfacesSource, { device_id: ["a", "b"] })
      ).toMatchObject({ kind: "invalid", message: expect.stringContaining("{device_id}") });
    });

    it("keeps the API path prefix", () => {
      expect(
        readyUrl(
          buildOptionSourceRequest(
            "https://gateway.example.com/workflow-api",
            interfacesSource,
            { device_id: "d1" }
          )
        )
      ).toBe("https://gateway.example.com/workflow-api/v1/parameter/device/d1/interfaces");
    });
  });

  it("lets a filled dependency override a static param, and an empty optional one keep it", () => {
    const source: OptionSource = {
      ...overlaySource,
      params: { location: "DEFAULT", isolation_type: "x" },
      depends_on: { location: { field: "site", required: false } },
    };

    expect(readyUrl(buildOptionSourceRequest(API_URL, source, { site: "PDX01" }))).toBe(
      "http://localhost:9000/v1/parameter/overlay?isolation_type=x&location=PDX01"
    );
    expect(readyUrl(buildOptionSourceRequest(API_URL, source, { site: "" }))).toBe(
      "http://localhost:9000/v1/parameter/overlay?isolation_type=x&location=DEFAULT"
    );
  });

  it("is independent of key order and array selection order", () => {
    const source = (reverse: boolean): OptionSource => ({
      endpoint: "/v1/parameter/device",
      label_key: "name",
      value_key: "id",
      params: reverse ? { b: 1, a: 2 } : { a: 2, b: 1 },
      depends_on: reverse
        ? { role: { field: "roles" }, site: { field: "site" } }
        : { site: { field: "site" }, role: { field: "roles" } },
    });

    expect(readyUrl(buildOptionSourceRequest(API_URL, source(true), { roles: ["b", "a"], site: "S" }))).toBe(
      readyUrl(buildOptionSourceRequest(API_URL, source(false), { site: "S", roles: ["a", "b", "a"] }))
    );
  });

  it("treats false and 0 as filled dependencies", () => {
    const request = buildOptionSourceRequest(
      API_URL,
      {
        endpoint: "/v1/parameter/device",
        label_key: "name",
        value_key: "id",
        depends_on: { managed_only: { field: "managed" }, rack: { field: "rack" } },
      },
      { managed: false, rack: 0 }
    );

    expect(readyUrl(request)).toBe(
      "http://localhost:9000/v1/parameter/device?managed_only=false&rack=0"
    );
  });

  it("reads only declared dependencies, never inherited properties", () => {
    expect(
      readyUrl(buildOptionSourceRequest(API_URL, overlaySource, { site: "PDX01", overlay_id: "x" }))
    ).toBe(readyUrl(buildOptionSourceRequest(API_URL, overlaySource, { site: "PDX01" })));
    expect(
      buildOptionSourceRequest(
        API_URL,
        { ...overlaySource, depends_on: { location: { field: "constructor" } } },
        {}
      )
    ).toEqual({ kind: "waiting", missingDependencies: ["constructor"] });
  });

  it("encodes values so they cannot inject parameters", () => {
    expect(
      readyUrl(buildOptionSourceRequest(API_URL, overlaySource, { site: "PDX 01&isolation_type=other#x" }))
    ).toBe(
      "http://localhost:9000/v1/parameter/overlay" +
        "?isolation_type=spectrum_x_vrf&location=PDX+01%26isolation_type%3Dother%23x"
    );
  });

  it("rejects an endpoint whose placeholder escapes the API path even without dependencies", () => {
    expect(
      buildOptionSourceRequest(
        "https://gateway.example.com/workflow-api",
        { ...interfacesSource, endpoint: "/../{device_id}" },
        {}
      ).kind
    ).toBe("invalid");
  });
});

describe("dependencySignature", () => {
  const filled = { site: "PDX01", site_type: "Site", unrelated: 1 };

  it("is stable for equal values regardless of key order", () => {
    expect(dependencySignature(overlaySource, { unrelated: 1, site_type: "Site", site: "PDX01" })).toBe(
      dependencySignature(overlaySource, filled)
    );
  });

  it.each([
    ["the required value", { ...filled, site: "RNO1" }],
    ["only the location type", { ...filled, site_type: "Module" }],
    ["an optional dependency becoming empty", { ...filled, site_type: undefined }],
  ])("changes with %s", (_label, changed) => {
    expect(dependencySignature(overlaySource, changed)).not.toBe(
      dependencySignature(overlaySource, filled)
    );
  });

  it("ignores unrelated values and static params", () => {
    expect(dependencySignature(overlaySource, { ...filled, unrelated: 2 })).toBe(
      dependencySignature(overlaySource, filled)
    );
    expect(dependencySignature({ ...overlaySource, params: {} }, filled)).toBe(
      dependencySignature(overlaySource, filled)
    );
  });

  it("covers path placeholders", () => {
    expect(dependencySignature(interfacesSource, { device_id: "d1" })).not.toBe(
      dependencySignature(interfacesSource, { device_id: "d2" })
    );
  });
});

describe("mapOptionRows", () => {
  it("maps rows through label_key and value_key", () => {
    expect(
      mapOptionRows(
        [
          { id: "PDX01", name: "Portland" },
          { id: "RNO1", name: "Reno" },
        ],
        "name",
        "id"
      )
    ).toEqual({
      options: [
        { label: "Portland", value: "PDX01" },
        { label: "Reno", value: "RNO1" },
      ],
      skippedRows: 0,
      duplicateRows: 0,
    });
  });

  it("keeps numeric and boolean values typed", () => {
    expect(
      mapOptionRows(
        [
          { id: 7, name: "Seven" },
          { id: true, name: "Yes" },
        ],
        "name",
        "id"
      )?.options
    ).toEqual([
      { label: "Seven", value: 7 },
      { label: "Yes", value: true },
    ]);
  });

  it.each([
    ["an object", { results: [] }],
    ["null", null],
    ["a string", "PDX01"],
    ["undefined", undefined],
  ])("returns null for %s instead of a list", (_label, payload) => {
    expect(mapOptionRows(payload, "name", "id")).toBeNull();
  });

  it("skips rows without a usable value", () => {
    const mapping = mapOptionRows(
      [
        { name: "no id" },
        { id: null, name: "null id" },
        { id: "", name: "empty id" },
        { id: { nested: 1 }, name: "object id" },
        { id: Number.NaN, name: "NaN id" },
        null,
        "PDX01",
        ["PDX01"],
        { id: "ok", name: "Ok" },
      ],
      "name",
      "id"
    );

    expect(mapping).toEqual({
      options: [{ label: "Ok", value: "ok" }],
      skippedRows: 8,
      duplicateRows: 0,
    });
  });

  it("falls back to the value when the label is missing or unusable", () => {
    expect(
      mapOptionRows(
        [{ id: "a" }, { id: "b", name: null }, { id: "c", name: "" }, { id: "d", name: 4 }],
        "name",
        "id"
      )?.options
    ).toEqual([
      { label: "a", value: "a" },
      { label: "b", value: "b" },
      { label: "c", value: "c" },
      { label: "4", value: "d" },
    ]);
  });

  it("never reads inherited properties as label or value", () => {
    expect(mapOptionRows([{ id: "a" }], "toString", "id")?.options).toEqual([
      { label: "a", value: "a" },
    ]);
    expect(mapOptionRows([{ id: "a" }], "name", "constructor")?.skippedRows).toBe(1);
  });

  it("keeps the first row for duplicate values, including 1 and '1'", () => {
    expect(
      mapOptionRows(
        [
          { id: "a", name: "First A" },
          { id: "b", name: "B" },
          { id: "a", name: "Second A" },
          { id: 1, name: "One" },
          { id: "1", name: "One again" },
        ],
        "name",
        "id"
      )
    ).toEqual({
      options: [
        { label: "First A", value: "a" },
        { label: "B", value: "b" },
        { label: "One", value: 1 },
      ],
      skippedRows: 0,
      duplicateRows: 2,
    });
  });

  describe("with a type_key (site_reference)", () => {
    it("adds each row's type, or null when the row has none", () => {
      expect(
        mapOptionRows(
          [
            { id: "PDX01", name: "PDX01", location_type: "Site" },
            { id: "RNO1", name: "RNO1" },
            { id: "M1", name: "Module 1", location_type: "" },
          ],
          "name",
          "id",
          "location_type"
        )?.options
      ).toEqual([
        { label: "PDX01", value: "PDX01", type: "Site" },
        { label: "RNO1", value: "RNO1", type: null },
        { label: "Module 1", value: "M1", type: null },
      ]);
    });

    it("keeps rows that share a value but differ in type", () => {
      // The same provider id may name a Site and a Module.
      expect(
        mapOptionRows(
          [
            { id: "42", name: "SJC01", location_type: "Site" },
            { id: "42", name: "Module 1", location_type: "Module" },
            { id: "42", name: "SJC01 again", location_type: "Site" },
          ],
          "name",
          "id",
          "location_type"
        )
      ).toEqual({
        options: [
          { label: "SJC01", value: "42", type: "Site" },
          { label: "Module 1", value: "42", type: "Module" },
        ],
        skippedRows: 0,
        duplicateRows: 1,
      });
    });

    it("never reads an inherited property as the type", () => {
      expect(mapOptionRows([{ id: "a" }], "name", "id", "constructor")?.options).toEqual([
        { label: "a", value: "a", type: null },
      ]);
    });
  });

  it("omits type when no type_key is given", () => {
    expect(
      mapOptionRows([{ id: "a", location_type: "Site" }], "name", "id")?.options[0]
    ).not.toHaveProperty("type");
  });
});


describe("deriveOptionSourceState", () => {
  const ready: OptionSourceRequest = { kind: "ready", url: `${API_URL}/v1/parameter/site` };
  const someOptions = { options: [{ label: "A", value: "a" }], skippedRows: 0, duplicateRows: 0 };

  it("is idle without a request", () => {
    expect(
      deriveOptionSourceState({ request: undefined, error: undefined, mapping: undefined })
    ).toMatchObject({ status: "idle", options: [], missingDependencies: [] });
  });

  it("is idle and names the properties while waiting on dependencies", () => {
    const missingDependencies = ["site", "site_type"];

    expect(
      deriveOptionSourceState({
        request: { kind: "waiting", missingDependencies: [...missingDependencies] },
        error: undefined,
        mapping: undefined,
      })
    ).toMatchObject({ status: "idle", missingDependencies });
  });

  it("is an error for an invalid endpoint", () => {
    const state = deriveOptionSourceState({
      request: { kind: "invalid", message: "bad endpoint" },
      error: undefined,
      mapping: undefined,
    });

    expect(state.status).toBe("error");
    expect(state.error?.message).toBe("bad endpoint");
  });

  it("is loading until rows arrive", () => {
    expect(
      deriveOptionSourceState({ request: ready, error: undefined, mapping: undefined }).status
    ).toBe("loading");
  });

  it("is an error for a failed request, even if rows were mapped", () => {
    const error = new Error("HTTP 500");
    const state = deriveOptionSourceState({ request: ready, error, mapping: someOptions });

    expect(state).toMatchObject({ status: "error", error, options: [] });
  });

  it("is an error for a non-list response", () => {
    expect(
      deriveOptionSourceState({ request: ready, error: undefined, mapping: null }).status
    ).toBe("error");
  });

  it("distinguishes empty from success", () => {
    expect(
      deriveOptionSourceState({
        request: ready,
        error: undefined,
        mapping: { options: [], skippedRows: 2, duplicateRows: 0 },
      }).status
    ).toBe("empty");
    expect(
      deriveOptionSourceState({ request: ready, error: undefined, mapping: someOptions })
    ).toMatchObject({ status: "success", options: someOptions.options });
  });
});
