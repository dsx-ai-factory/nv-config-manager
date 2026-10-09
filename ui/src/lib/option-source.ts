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
 * Framework-free request building and row mapping for v1 option sources. The React
 * hook in `@/hooks/useOptionSource` is a thin SWR wrapper around these helpers.
 *
 * Dependencies are read from the form's projected data: a dependency names a sibling
 * property, such as a location field's `typeField` sibling for its location type.
 */
import { sanitizeUrl } from "@/lib/utils";
import type {
  OptionSource,
  OptionSourceParamValue,
  ScalarValue,
} from "@/types/workflow-catalog.types";

export interface OptionSourceItem {
  label: string;
  value: ScalarValue;
  description?: string;
  group?: string;
  /**
   * The row's `type_key` value; set only when the source has a `type_key`
   * (location fields), and `null` when the row has none.
   */
  type?: ScalarValue | null;
}

export interface OptionSourceMeta {
  matching_device_count?: number;
  warnings?: string[];
}

/** Form data dependencies are read from: projected properties by name. */
export type DependencyValues = Readonly<Record<string, unknown>>;

/** Extra query parameters a core field adds (device scope filters); arrays repeat. */
export type ExtraParams = Readonly<Record<string, readonly string[]>>;

export type OptionSourceRequest =
  /** `url` doubles as the SWR cache key; it never carries headers or credentials. */
  | { kind: "ready"; url: string }
  /** At least one required dependency (or path placeholder) is empty; names the properties. */
  | { kind: "waiting"; missingDependencies: string[] }
  /** The endpoint, or a value substituted into it, is unusable. */
  | { kind: "invalid"; message: string };

const hasOwn = (record: object, key: string): boolean =>
  Object.prototype.hasOwnProperty.call(record, key);

const readOwn = (record: object, key: string): unknown =>
  hasOwn(record, key) ? (record as Record<string, unknown>)[key] : undefined;

const isPlainObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/**
 * Turn one form value into query-parameter values. Returns `[]` for "empty":
 * absent, `null`, `""`, and `[]`, plus values with no query-string form (objects,
 * non-finite numbers). Array items that are empty are dropped; the rest are
 * de-duplicated and sorted so selection order does not change the request or its
 * cache key.
 */
export const normalizeDependencyValue = (value: unknown): string[] => {
  if (value === undefined || value === null) return [];
  if (typeof value === "string") return value === "" ? [] : [value];
  if (typeof value === "number") return Number.isFinite(value) ? [String(value)] : [];
  if (typeof value === "boolean") return [String(value)];
  if (Array.isArray(value)) {
    const items = value.flatMap((item) =>
      Array.isArray(item) ? [] : normalizeDependencyValue(item)
    );
    return [...new Set(items)].sort();
  }
  return [];
};

const PLACEHOLDER = /\{([A-Za-z_][A-Za-z0-9_]*)\}/g;
const PLACEHOLDER_SEGMENT = /^\{([A-Za-z_][A-Za-z0-9_]*)\}$/;
const UNSAFE_ENDPOINT_CHARACTERS = /[\s\\?#\u0000-\u001f\u007f]/;

/**
 * Names of the `{field}` path placeholders in an endpoint, in order of first
 * appearance. Returns `null` unless every brace expression is a whole path segment
 * containing an ASCII identifier.
 */
export const endpointPlaceholders = (endpoint: string): string[] | null => {
  const names: string[] = [];
  for (const segment of endpoint.split("/")) {
    if (!/[{}]/.test(segment)) continue;
    const match = PLACEHOLDER_SEGMENT.exec(segment);
    if (!match) return null;
    const name = match[1];
    if (!names.includes(name)) names.push(name);
  }
  return names;
};

const fillPlaceholders = (endpoint: string, segment: (name: string) => string): string =>
  endpoint.replace(PLACEHOLDER, (_match, name: string) => segment(name));

/**
 * Resolve an option-source path under the workflow API URL, following the existing
 * `sanitizeUrl(apiURL + path)` convention. Returns `null` unless the path is a plain
 * absolute path (no scheme, authority, query, fragment, backslash, or whitespace) and
 * the resolved URL keeps the API origin and path prefix.
 */
export const resolveOptionSourceEndpoint = (
  apiURL: string,
  endpoint: string
): URL | null => {
  if (
    !endpoint.startsWith("/") ||
    endpoint.startsWith("//") ||
    UNSAFE_ENDPOINT_CHARACTERS.test(endpoint)
  ) {
    return null;
  }

  let base: URL;
  let resolved: URL;
  try {
    base = new URL(apiURL);
    resolved = new URL(sanitizeUrl(`${apiURL}${endpoint}`));
  } catch {
    return null;
  }

  if (resolved.origin !== base.origin || resolved.username || resolved.password) {
    return null;
  }
  const basePath = base.pathname.replace(/\/+$/, "");
  if (resolved.pathname !== basePath && !resolved.pathname.startsWith(`${basePath}/`)) {
    return null;
  }
  return resolved;
};

interface ResolvedDependencies {
  /** Placeholder name → normalized field values (`[]` when empty). */
  path: Map<string, string[]>;
  /** Query parameter → normalized dependency values (`[]` when empty). */
  query: Map<string, string[]>;
  /** Required dependencies that are empty, de-duplicated, in declaration order. */
  missing: string[];
}

const resolveDependencies = (
  source: OptionSource,
  placeholders: readonly string[],
  values: DependencyValues
): ResolvedDependencies => {
  const missing: string[] = [];
  const addMissing = (name: string) => {
    if (!missing.includes(name)) missing.push(name);
  };

  const path = new Map<string, string[]>();
  for (const name of placeholders) {
    const normalized = normalizeDependencyValue(readOwn(values, name));
    path.set(name, normalized);
    // Path placeholders are always required.
    if (normalized.length === 0) addMissing(name);
  }

  const query = new Map<string, string[]>();
  for (const [param, dependency] of Object.entries(source.depends_on ?? {})) {
    const normalized = normalizeDependencyValue(readOwn(values, dependency.field));
    query.set(param, normalized);
    if (normalized.length === 0 && dependency.required !== false) {
      addMissing(dependency.field);
    }
  }

  return { path, query, missing };
};

/** Properties an option source reads: its path placeholders and `depends_on` fields. */
export const dependencyFields = (source: OptionSource): string[] => [
  ...new Set([
    ...(endpointPlaceholders(source.endpoint) ?? []),
    ...Object.values(source.depends_on ?? {}).map((dependency) => dependency.field),
  ]),
];

/**
 * A stable string that changes exactly when a value the source depends on changes
 * (path placeholders and every `depends_on` entry, required or not). Fields compare it
 * across renders to apply `clear_on_change`. Static `params` and unrelated data never
 * affect it.
 */
export const dependencySignature = (
  source: OptionSource,
  values: DependencyValues
): string => {
  const { path, query } = resolveDependencies(
    source,
    endpointPlaceholders(source.endpoint) ?? [],
    values
  );
  const sorted = (map: Map<string, string[]>) =>
    [...map.entries()].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  return JSON.stringify([sorted(path), sorted(query)]);
};

const staticParamValues = (value: OptionSourceParamValue): string[] =>
  Array.isArray(value) ? value.map(String) : [String(value)];

/**
 * Build the request for an option source from the current form data.
 *
 * - Only the properties named by `depends_on` and the endpoint's `{field}`
 *   placeholders are read from `values`.
 * - Path placeholders are required and URL-encoded as one segment; a placeholder
 *   whose field holds several values, or a value that would form a dot segment, makes
 *   the request invalid.
 * - An empty required dependency keeps the request waiting; an empty optional
 *   (`required: false`) dependency is simply not sent.
 * - Array values (static or dependency) become repeated parameters.
 * - A filled dependency overrides a static parameter of the same name; parameters are
 *   emitted in sorted name order so equal inputs always yield the same URL (the cache
 *   key).
 * - `extra` parameters (a device field's scope filters) override both.
 */
export const buildOptionSourceRequest = (
  apiURL: string,
  source: OptionSource,
  values: DependencyValues,
  extra: ExtraParams = {}
): OptionSourceRequest => {
  const invalidEndpoint: OptionSourceRequest = {
    kind: "invalid",
    message: `Option source endpoint "${source.endpoint}" is not a relative path under the workflow API.`,
  };

  // Check the template before reading any value, so a bad endpoint is reported even
  // while dependencies are still empty.
  const placeholders = endpointPlaceholders(source.endpoint);
  if (
    placeholders === null ||
    !resolveOptionSourceEndpoint(apiURL, fillPlaceholders(source.endpoint, () => "_"))
  ) {
    return invalidEndpoint;
  }

  const { path, query, missing } = resolveDependencies(source, placeholders, values);
  if (missing.length > 0) return { kind: "waiting", missingDependencies: missing };

  const segments = new Map<string, string>();
  for (const [name, pathValues] of path) {
    if (pathValues.length > 1) {
      return {
        kind: "invalid",
        message: `Path placeholder {${name}} needs a single value; "${name}" has ${pathValues.length}.`,
      };
    }
    const segment = encodeURIComponent(pathValues[0]);
    if (segment === "." || segment === "..") {
      return {
        kind: "invalid",
        message: `The value of "${name}" cannot be used as a path segment.`,
      };
    }
    segments.set(name, segment);
  }

  const url = resolveOptionSourceEndpoint(
    apiURL,
    fillPlaceholders(source.endpoint, (name) => segments.get(name) ?? "")
  );
  if (!url) return invalidEndpoint;

  const merged = new Map<string, string[]>();
  for (const [name, value] of Object.entries(source.params ?? {})) {
    merged.set(name, staticParamValues(value));
  }
  for (const [param, normalized] of query) {
    if (normalized.length > 0) merged.set(param, normalized);
  }
  for (const [param, extraValues] of Object.entries(extra)) {
    if (extraValues.length > 0) merged.set(param, [...extraValues]);
  }

  const search = new URLSearchParams();
  for (const name of [...merged.keys()].sort()) {
    for (const value of merged.get(name) ?? []) {
      search.append(name, value);
    }
  }
  url.search = search.toString();
  return { kind: "ready", url: url.toString() };
};

export interface OptionRowsMapping {
  options: OptionSourceItem[];
  meta?: OptionSourceMeta;
  /** Rows dropped because they were not objects or had no usable `value_key`. */
  skippedRows: number;
  /** Rows dropped because an earlier row already produced the same option. */
  duplicateRows: number;
}

const isOptionValue = (value: unknown): value is ScalarValue =>
  (typeof value === "string" && value !== "") ||
  (typeof value === "number" && Number.isFinite(value)) ||
  typeof value === "boolean";

/**
 * Map parameter-endpoint rows to options through `label_key`/`value_key` (and
 * `type_key` for location fields). Returns `null` when the response is not a list. A
 * row without a usable value is skipped; a row without a usable label falls back to its
 * value; a row without a usable type gets `type: null`. The first row for an option
 * wins, where an option is its value, or its value and type when `typeKey` is given
 * (the same id may exist as a Site and as a Module).
 */
export const mapOptionRows = (
  rows: unknown,
  labelKey: string,
  valueKey: string,
  typeKey?: string
): OptionRowsMapping | null => {
  if (!Array.isArray(rows)) return null;

  const options: OptionSourceItem[] = [];
  const seen = new Set<string>();
  let skippedRows = 0;
  let duplicateRows = 0;

  for (const row of rows) {
    if (!isPlainObject(row)) {
      skippedRows += 1;
      continue;
    }
    const value = readOwn(row, valueKey);
    if (!isOptionValue(value)) {
      skippedRows += 1;
      continue;
    }
    let type: ScalarValue | null | undefined;
    if (typeKey !== undefined) {
      const rawType = readOwn(row, typeKey);
      type = isOptionValue(rawType) ? rawType : null;
    }
    // Rendered option values are strings, so 1 and "1" are the same option.
    const identity =
      typeKey === undefined
        ? String(value)
        : JSON.stringify([String(value), type === null ? null : String(type)]);
    if (seen.has(identity)) {
      duplicateRows += 1;
      continue;
    }
    seen.add(identity);

    const label = readOwn(row, labelKey);
    const item: OptionSourceItem = {
      label: isOptionValue(label) ? String(label) : String(value),
      value,
    };
    if (type !== undefined) item.type = type;
    options.push(item);
  }

  return { options, skippedRows, duplicateRows };
};

/** Map the normalized `options-v1` response used by enriched direct option sources. */
export const mapOptionEnvelope = (response: unknown): OptionRowsMapping | null => {
  if (!isPlainObject(response) || !Array.isArray(response.items)) return null;

  const options: OptionSourceItem[] = [];
  const seen = new Set<string>();
  let skippedRows = 0;
  let duplicateRows = 0;
  for (const row of response.items) {
    if (!isPlainObject(row) || !isOptionValue(row.value)) {
      skippedRows += 1;
      continue;
    }
    // Grouped choices may intentionally repeat one submitted value in several
    // sections (for example a diagnostic command supported by two, but not all,
    // selected platforms). De-duplicate within a group while retaining those
    // cross-group appearances.
    const group = typeof row.group === "string" && row.group !== "" ? row.group : undefined;
    const identity = group ? JSON.stringify([String(row.value), group]) : String(row.value);
    if (seen.has(identity)) {
      duplicateRows += 1;
      continue;
    }
    seen.add(identity);
    const item: OptionSourceItem = {
      label: isOptionValue(row.label) ? String(row.label) : identity,
      value: row.value,
    };
    if (typeof row.description === "string" && row.description !== "") {
      item.description = row.description;
    }
    if (group) item.group = group;
    options.push(item);
  }

  let meta: OptionSourceMeta | undefined;
  if (isPlainObject(response.meta)) {
    const count = response.meta.matching_device_count;
    const warnings = response.meta.warnings;
    meta = {
      ...(typeof count === "number" && Number.isInteger(count) && count >= 0
        ? { matching_device_count: count }
        : {}),
      ...(Array.isArray(warnings) && warnings.every((item) => typeof item === "string")
        ? { warnings }
        : {}),
    };
  }
  return { options, meta, skippedRows, duplicateRows };
};

interface OptionSourceStateBase {
  options: OptionSourceItem[];
  meta: OptionSourceMeta | undefined;
  /** Required dependencies (property names) that must be filled before options load. */
  missingDependencies: string[];
  error: Error | undefined;
}

export type OptionSourceState = OptionSourceStateBase &
  (
    | { status: "idle" }
    | { status: "loading" }
    | { status: "empty" }
    | { status: "success" }
    | { status: "error"; error: Error }
  );

export type OptionSourceStatus = OptionSourceState["status"];

export interface OptionSourceStateInput {
  /** The request as built for the current state; `undefined` when there is nothing to load. */
  request: OptionSourceRequest | undefined;
  /** Fetch error for the current request URL. */
  error: Error | undefined;
  /** Mapped rows for the current request URL; `undefined` while none have arrived. */
  mapping: OptionRowsMapping | null | undefined;
}

const NO_OPTIONS: OptionSourceItem[] = [];
const NO_MISSING: string[] = [];

/**
 * Derive the public state from the current request and its response. Inputs must
 * belong to the current request URL; the hook guarantees that by keying SWR on it.
 */
export const deriveOptionSourceState = ({
  request,
  error,
  mapping,
}: OptionSourceStateInput): OptionSourceState => {
  const missingDependencies =
    request?.kind === "waiting" ? request.missingDependencies : NO_MISSING;
  const base = { options: NO_OPTIONS, meta: undefined, missingDependencies, error: undefined };

  if (request?.kind === "invalid") {
    return { ...base, status: "error", error: new Error(request.message) };
  }
  if (request?.kind !== "ready") return { ...base, status: "idle" };
  if (error) return { ...base, status: "error", error };
  if (mapping === null) {
    return {
      ...base,
      status: "error",
      error: new Error("The option source did not return a list."),
    };
  }
  if (mapping === undefined) return { ...base, status: "loading" };
  return {
    ...base,
    status: mapping.options.length === 0 ? "empty" : "success",
    options: mapping.options,
    meta: mapping.meta,
  };
};
