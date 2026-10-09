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
 * URL prefill. The query string is read once, at mount, into an immutable
 * {@link QuerySnapshot}. Standard projected fields are coerced straight into the
 * initial form data and are never pending; core fields and device filter scopes read
 * their own raw values from the snapshot later and start pending only when one of
 * their parameters is present.
 *
 * | Owner                    | Parameters                                          |
 * | ------------------------ | --------------------------------------------------- |
 * | standard projected field | its property name (not hidden, no `ui:field`)        |
 * | `field:<apiOptions>`     | property name + `queryAliases`                      |
 * | `field:<location>`       | property name + `queryAliases`                      |
 * | `field:<device>`         | `queryParam` + `queryAliases` (none without one)    |
 * | `scope:<scope id>`       | `site` (own Site filter), `tenant`, `status`        |
 */
import type { FormData, Owner, QuerySnapshot } from "./state";
import {
  coreFieldOf,
  deviceOptionsOf,
  fieldOptions,
  isHidden,
  propertiesOf,
  resolveRef,
  type CoreFieldName,
  type DeviceFilter,
  type DeviceOptions,
} from "./ui-schema";

/** Anything with `forEach` over `(value, name)`, e.g. `URLSearchParams`. */
export interface SearchParamsLike {
  forEach(callback: (value: string, name: string) => void): void;
}

/** Every non-empty value of every parameter, in URL order. Taken once, at mount. */
export const snapshotQuery = (
  params: SearchParamsLike | null | undefined
): QuerySnapshot => {
  const snapshot: Record<string, string[]> = {};
  params?.forEach((value, name) => {
    if (value === "") return;
    if (!Object.prototype.hasOwnProperty.call(snapshot, name))
      snapshot[name] = [];
    snapshot[name].push(value);
  });
  return snapshot;
};

/** Values of the first parameter in `names` (precedence order) that has any. */
export const queryValues = (
  query: QuerySnapshot,
  names: readonly string[]
): string[] => {
  for (const name of names) {
    const values = Object.prototype.hasOwnProperty.call(query, name)
      ? query[name]
      : undefined;
    if (values && values.length > 0) return [...values];
  }
  return [];
};

/** Split one field's legacy multi-value query encoding when its declaration requests it. */
export const optionQueryValues = (
  query: QuerySnapshot,
  names: readonly string[],
  multiple: boolean,
  separator?: string
): string[] => {
  const values = queryValues(query, names);
  return multiple && separator
    ? values.flatMap((value) =>
        value
          .split(separator)
          .map((item) => item.trim())
          .filter(Boolean)
      )
    : values;
};

/** URL parameters a core field's own prefill reads, in precedence order. */
export const corePrefillParams = (
  core: CoreFieldName,
  name: string,
  options: Readonly<Record<string, unknown>>
): string[] => {
  const aliases = Array.isArray(options.queryAliases)
    ? (options.queryAliases as string[])
    : [];
  if (core !== "device") return [name, ...aliases];
  const { queryParam } = options as unknown as DeviceOptions;
  return queryParam ? [queryParam, ...aliases] : [];
};

const fieldPrefillParams = (uiSchema: unknown, name: string): string[] => {
  const core = coreFieldOf(uiSchema, name);
  return core
    ? corePrefillParams(core, name, fieldOptions(uiSchema, name))
    : [];
};

/** Scope filters a device field's scope prefills; Site only when it is the scope's own. */
export const scopePrefillFilters = (options: DeviceOptions): DeviceFilter[] =>
  options.filters.filter(
    (filter) => filter !== "site" || options.siteField === undefined
  );

const coerce = (raw: string, type: unknown): unknown => {
  if (type === "boolean")
    return raw === "true" ? true : raw === "false" ? false : undefined;
  if (type === "integer" || type === "number") {
    const value = Number(raw);
    if (raw.trim() === "" || !Number.isFinite(value)) return undefined;
    return type === "integer" && !Number.isInteger(value) ? undefined : value;
  }
  return raw;
};

/**
 * Coerce standard-field URL values into form data by schema type. Only projected
 * properties are read, so server-owned and excluded inputs cannot be set; hidden and
 * core fields are skipped, and unknown parameters are ignored.
 */
export const standardPrefill = (
  schema: unknown,
  uiSchema: unknown,
  query: QuerySnapshot
): FormData => {
  const data: FormData = {};
  for (const [name, node] of Object.entries(propertiesOf(schema))) {
    if (isHidden(uiSchema, name) || coreFieldOf(uiSchema, name)) continue;
    const values = queryValues(query, [name]);
    if (values.length === 0) continue;
    const property = resolveRef(schema, node);
    if (property.type === "array") {
      const itemType = resolveRef(schema, property.items).type;
      const items = values.map((value) => coerce(value, itemType));
      if (items.every((item) => item !== undefined)) data[name] = items;
      continue;
    }
    const value = coerce(values[0], property.type);
    if (value !== undefined) data[name] = value;
  }
  return data;
};

/** Owners whose prefill starts pending: exactly those with a value in the URL. */
export const initialPending = (
  schema: unknown,
  uiSchema: unknown,
  query: QuerySnapshot
): Set<Owner> => {
  const pending = new Set<Owner>();
  for (const name of Object.keys(propertiesOf(schema))) {
    const core = coreFieldOf(uiSchema, name);
    if (!core) continue;
    if (queryValues(query, fieldPrefillParams(uiSchema, name)).length > 0) {
      pending.add(`field:${name}`);
    }
    if (core !== "device") continue;
    const options = deviceOptionsOf(uiSchema, name);
    if (
      scopePrefillFilters(options).some(
        (filter) => queryValues(query, [filter]).length > 0
      )
    ) {
      pending.add(`scope:${options.filterScope}`);
    }
  }
  return pending;
};
