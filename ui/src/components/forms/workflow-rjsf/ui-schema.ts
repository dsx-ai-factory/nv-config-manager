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
 * Read-only accessors for the projected schema and the v1 `ui_schema`. The loader has
 * already validated both against the wire schema and the backend has checked every
 * model-aware reference, so these only read; they never interpret or rewrite.
 */
import type { OptionSource } from "@/types/workflow-catalog.types";

export type JsonObject = Readonly<Record<string, unknown>>;

export type CoreFieldName =
  | "apiOptions"
  | "device"
  | "location"
  | "variantRows";

export interface ApiOptionsOptions {
  source: OptionSource;
  queryAliases?: string[];
  querySeparator?: string;
  presentation?: "select" | "grouped-checkboxes";
  selectAll?: boolean;
  showDescriptions?: boolean;
  disableWhenNoMatches?: boolean;
  metaText?: { key: "matching_device_count"; label: string };
}

export interface VariantRowsChoice {
  label: string;
  value: string;
}

export interface VariantRowsColumn {
  /** Projected array property. Columns with an `itemProperty` share object-array rows. */
  arrayProperty: string;
  itemProperty?: string;
  label: string;
  kind: "text" | "select";
  placeholder?: string;
  required?: boolean;
  pattern?: string;
  choices?: VariantRowsChoice[];
}

export interface VariantRowsMode {
  id: string;
  label: string;
  columns: VariantRowsColumn[];
}

export interface VariantRowsFieldOptions {
  ownedProperties: string[];
  modes: VariantRowsMode[];
  minimumRows?: number;
  clearInactive?: true;
  warning?: string;
}

export interface LocationOptions {
  source: OptionSource;
  typeField?: string;
  queryAliases?: string[];
}

export type DeviceFilter = "site" | "tenant" | "status";

export interface DeviceOptions {
  source: OptionSource;
  filters: DeviceFilter[];
  siteRequired: boolean;
  siteField?: string;
  filterScope: string;
  queryParam?: string;
  queryAliases?: string[];
}

export interface ExclusiveGroupDeclaration {
  fields?: string[];
  deviceFilters?: string[];
}

export interface ExclusiveGroup {
  fields: string[];
  filterScopes: string[];
}

export interface FieldComparison {
  left: string;
  operator: "lessThan";
  right: string;
  message: string;
}

export const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

export const own = (record: unknown, key: string): unknown =>
  isObject(record) && Object.prototype.hasOwnProperty.call(record, key)
    ? record[key]
    : undefined;

/** The projected schema's top-level properties, in schema order. */
export const propertiesOf = (schema: unknown): Record<string, JsonObject> => {
  const properties = own(schema, "properties");
  return isObject(properties) ? (properties as Record<string, JsonObject>) : {};
};

export const requiredOf = (schema: unknown): string[] => {
  const required = own(schema, "required");
  return Array.isArray(required)
    ? required.filter((name) => typeof name === "string")
    : [];
};

/** Follow one local `#/$defs/...` reference. */
export const resolveRef = (root: unknown, node: unknown): JsonObject => {
  const ref = own(node, "$ref");
  if (typeof ref !== "string" || !ref.startsWith("#/"))
    return isObject(node) ? node : {};
  let target: unknown = root;
  for (const token of ref.slice(2).split("/")) {
    target = own(target, token.replaceAll("~1", "/").replaceAll("~0", "~"));
  }
  if (!isObject(target)) return {};
  // Keywords beside `$ref` (Pydantic puts `title`, `default`, `description` there) win.
  const siblings = Object.entries(node as JsonObject).filter(
    ([key]) => key !== "$ref"
  );
  return { ...target, ...Object.fromEntries(siblings) };
};

/** A property's ui_schema entry (`{}` when it has none). */
export const fieldUi = (uiSchema: unknown, name: string): JsonObject => {
  const ui = own(uiSchema, name);
  return isObject(ui) ? ui : {};
};

export const fieldOptions = (uiSchema: unknown, name: string): JsonObject => {
  const options = own(fieldUi(uiSchema, name), "ui:options");
  return isObject(options) ? options : {};
};

export const coreFieldOf = (
  uiSchema: unknown,
  name: string
): CoreFieldName | undefined => {
  const field = own(fieldUi(uiSchema, name), "ui:field");
  return field === "apiOptions" ||
    field === "device" ||
    field === "location" ||
    field === "variantRows"
    ? field
    : undefined;
};

export const deviceOptionsOf = (
  uiSchema: unknown,
  name: string
): DeviceOptions => fieldOptions(uiSchema, name) as unknown as DeviceOptions;

export const locationOptionsOf = (
  uiSchema: unknown,
  name: string
): LocationOptions =>
  fieldOptions(uiSchema, name) as unknown as LocationOptions;

export const variantRowsFieldOptionsOf = (
  uiSchema: unknown,
  name: string
): VariantRowsFieldOptions =>
  fieldOptions(uiSchema, name) as unknown as VariantRowsFieldOptions;

export const variantRowsDeclarations = (
  schema: unknown,
  uiSchema: unknown
): Array<{ anchor: string; config: VariantRowsFieldOptions }> =>
  Object.keys(propertiesOf(schema)).flatMap((anchor) =>
    coreFieldOf(uiSchema, anchor) === "variantRows"
      ? [{ anchor, config: variantRowsFieldOptionsOf(uiSchema, anchor) }]
      : []
  );

/** Resolve declarative device-filter owners to the scopes held in shell state. */
export const exclusiveGroupsOf = (uiSchema: unknown): ExclusiveGroup[] => {
  const globalOptions = own(uiSchema, "ui:globalOptions");
  const declarations = own(globalOptions, "exclusiveGroups");
  if (!Array.isArray(declarations)) return [];
  return declarations.map((value) => {
    const declaration = value as ExclusiveGroupDeclaration;
    const deviceFilters = Array.isArray(declaration.deviceFilters)
      ? declaration.deviceFilters
      : [];
    return {
      fields: Array.isArray(declaration.fields) ? declaration.fields : [],
      filterScopes: deviceFilters.map(
        (name) => deviceOptionsOf(uiSchema, name).filterScope
      ),
    };
  });
};

export const fieldComparisonsOf = (uiSchema: unknown): FieldComparison[] => {
  const globalOptions = own(uiSchema, "ui:globalOptions");
  const comparisons = own(globalOptions, "fieldComparisons");
  return Array.isArray(comparisons) ? (comparisons as FieldComparison[]) : [];
};

export const isHidden = (uiSchema: unknown, name: string): boolean =>
  own(fieldUi(uiSchema, name), "ui:widget") === "hidden";

export const isTextarea = (uiSchema: unknown, name: string): boolean =>
  own(fieldUi(uiSchema, name), "ui:widget") === "textarea";

/** Display label of a top-level property: `ui:title`, schema `title`, or its name. */
export const labelOf = (
  schema: unknown,
  uiSchema: unknown,
  name: string
): string => {
  const title = own(fieldUi(uiSchema, name), "ui:title");
  if (typeof title === "string") return title;
  const property = resolveRef(schema, own(propertiesOf(schema), name));
  return typeof property.title === "string" ? property.title : name;
};

/**
 * Top-level properties in rendering order: `ui:order` with `*` expanded to the
 * remaining properties in schema order, and any property `ui:order` omits placed last
 * in schema order.
 */
export const effectiveOrder = (
  schema: unknown,
  uiSchema: unknown
): string[] => {
  const names = Object.keys(propertiesOf(schema));
  const order = own(uiSchema, "ui:order");
  if (!Array.isArray(order)) return names;
  const listed = order.filter((name): name is string => names.includes(name));
  const rest = names.filter((name) => !listed.includes(name));
  const wildcard = order.indexOf("*");
  if (wildcard === -1) return [...listed, ...rest];
  const before = order
    .slice(0, wildcard)
    .filter((name) => names.includes(name));
  const after = order
    .slice(wildcard + 1)
    .filter((name) => names.includes(name));
  return [...before, ...rest, ...after];
};
