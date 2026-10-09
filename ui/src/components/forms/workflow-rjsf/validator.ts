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
 * The Ajv validator RJSF uses for workflow forms: draft 2020-12 (Pydantic v2), every
 * error reported, unknown keywords kept as annotations, `ajv-formats`, and the
 * Pydantic formats `ajv-formats` lacks accepted as annotations (the server stays the
 * validator of record).
 */
import type Ajv from "ajv";
import Ajv2020 from "ajv/dist/2020";
import type { CustomValidator, ErrorTransformer } from "@rjsf/utils";
import { customizeValidator } from "@rjsf/validator-ajv8";

import { validateVariantRowsValues } from "./fields/variant-rows-field";
import type { FormLayout } from "./context";
import { editsWholeValue } from "./server-errors";
import type { FormData } from "./state";
import {
  deviceOptionsOf,
  labelOf,
  fieldComparisonsOf,
  own,
  propertiesOf,
  requiredOf,
  resolveRef,
  variantRowsDeclarations,
} from "./ui-schema";

type ValidationFilters = Readonly<
  Record<
    string,
    {
      site?: unknown;
      tenant?: readonly string[];
      status?: readonly string[];
    }
  >
>;

/** Formats Pydantic v2 emits that `ajv-formats` does not define. */
const PYDANTIC_ANNOTATION_FORMATS = [
  "base64url",
  "binary",
  "color",
  "directory-path",
  "file-path",
  "ipv4interface",
  "ipv4network",
  "ipv6interface",
  "ipv6network",
  "ipvanyaddress",
  "ipvanyinterface",
  "ipvanynetwork",
  "json-string",
  "multi-host-uri",
  "name-email",
  "password",
  "path",
  "payment-card-number",
  "phone",
  "uuid1",
  "uuid3",
  "uuid4",
  "uuid5",
] as const;

/** One validator for every form; it caches compiled schemas by content. */
export const workflowValidator = customizeValidator({
  // Ajv2020 is an Ajv subclass; the option is typed with the draft-07 class.
  AjvClass: Ajv2020 as unknown as typeof Ajv,
  ajvOptionsOverrides: { allErrors: true, strict: false },
  // Also replaces RJSF's own `color` regex: Pydantic's `color` is an annotation here.
  customFormats: Object.fromEntries(
    PYDANTIC_ANNOTATION_FORMATS.map((format) => [format, () => true])
  ),
});

const pathOf = (property: string | undefined): string[] =>
  (property ?? "").replace(/^\./, "").split(".").filter(Boolean);

/** Label of the property at `path`: `ui:title`/title at the top, schema title below. */
const labelAt = (
  schema: unknown,
  uiSchema: unknown,
  path: readonly string[]
): string => {
  if (path.length <= 1) return labelOf(schema, uiSchema, path[0] ?? "");
  let node = resolveRef(schema, propertiesOf(schema)[path[0]]);
  for (const segment of path.slice(1)) {
    node = /^\d+$/.test(segment)
      ? resolveRef(schema, node.items)
      : resolveRef(schema, own(node.properties, segment));
  }
  return typeof node.title === "string" ? node.title : path[path.length - 1];
};

const capitalize = (message: string): string =>
  message ? message[0].toUpperCase() + message.slice(1) : message;

const hasValue = (value: unknown): boolean =>
  value !== undefined &&
  value !== null &&
  (typeof value !== "string" || value.trim() !== "") &&
  (!Array.isArray(value) || value.length > 0);

const scopeHasValue = (
  scope: ValidationFilters[string] | undefined
): boolean =>
  scope?.site !== undefined ||
  Boolean(scope?.tenant?.length) ||
  Boolean(scope?.status?.length);

const joinModes = (modes: readonly string[]): string => {
  if (modes.length <= 1) return modes[0] ?? "an input mode";
  if (modes.length === 2) return `${modes[0]} or ${modes[1]}`;
  return `${modes.slice(0, -1).join(", ")}, or ${modes.at(-1)}`;
};

const completeModeLabel = (
  schema: unknown,
  uiSchema: unknown,
  group: NonNullable<FormLayout["exclusiveGroups"]>[number]
): string => {
  const labels = group.fields.map((field) => labelOf(schema, uiSchema, field));
  for (const deviceField of group.deviceFields) {
    const options = deviceOptionsOf(uiSchema, deviceField);
    if (!options.siteRequired) continue;
    if (options.siteField) {
      labels.unshift(labelOf(schema, uiSchema, options.siteField));
    } else {
      labels.unshift("Site");
    }
  }
  return [...new Set(labels)].join(", ");
};

/** Validate an opt-in set of mutually exclusive, complete input modes. */
const validateCompleteExclusiveGroups = (
  schema: unknown,
  uiSchema: unknown,
  formData: FormData,
  errors: Parameters<CustomValidator>[1],
  layout: FormLayout | undefined,
  filters: ValidationFilters
): void => {
  const groups = (layout?.exclusiveGroups ?? []).filter(
    (group) => group.requireComplete
  );
  if (groups.length === 0) return;

  const active = groups.filter(
    (group) =>
      group.fields.some((field) => hasValue(formData[field])) ||
      group.filterScopes.some((scope) => scopeHasValue(filters[scope]))
  );
  if (active.length === 0) {
    errors.addError(
      `Provide ${joinModes(
        groups.map((group) => completeModeLabel(schema, uiSchema, group))
      )}`
    );
    return;
  }
  if (active.length > 1) {
    errors.addError("Choose only one input mode");
    return;
  }

  const [group] = active;
  for (const field of group.fields) {
    if (!hasValue(formData[field])) {
      errors[field]?.addError(`${labelOf(schema, uiSchema, field)} is required`);
    }
  }
  for (const deviceField of group.deviceFields) {
    const options = deviceOptionsOf(uiSchema, deviceField);
    if (!options.siteRequired) continue;
    const hasSite = options.siteField
      ? hasValue(formData[options.siteField])
      : filters[options.filterScope]?.site !== undefined;
    if (!hasSite) {
      const target = options.siteField ?? deviceField;
      errors[target]?.addError(
        `Site is required for ${labelOf(schema, uiSchema, deviceField)}`
      );
    }
  }
};

/**
 * Readable Ajv messages: a missing value (and an empty required list) is
 * "<label> is required"; a top-level list below its `minItems` is
 * "At least <n> <label> is/are required"; other messages are capitalised. A control
 * that edits a whole list shows its items' errors itself, prefixed with the item number.
 */
export const createTransformErrors =
  (schema: unknown, uiSchema: unknown, layout?: FormLayout): ErrorTransformer =>
  (errors) =>
    errors
      // `if` failures only say which branch applied; the branch's own errors follow.
      .filter((error) => error.name !== "if")
      .map((error) => {
        const path = pathOf(error.property);
        const [top, index, ...rest] = path;
        const owner = layout?.owners[top];
        const target = owner?.startsWith("field:")
          ? owner.slice("field:".length)
          : top;
        if (target !== top) {
          const emptyRequiredList =
            error.name === "minItems" &&
            error.params?.limit === 1 &&
            path.length === 1 &&
            requiredOf(schema).includes(top);
          const message =
            error.name === "required" || emptyRequiredList
              ? `${labelAt(schema, uiSchema, path)} is required`
              : capitalize(error.message ?? "is invalid");
          const location = path.length > 1 ? ` (${path.join(".")})` : "";
          return {
            ...error,
            property: `.${target}`,
            message: location
              ? `${labelAt(schema, uiSchema, path)}${location}: ${message}`
              : message,
          };
        }
        if (
          index !== undefined &&
          rest.length === 0 &&
          /^\d+$/.test(index) &&
          editsWholeValue(schema, uiSchema, top)
        ) {
          return {
            ...error,
            property: `.${top}`,
            message: `Item ${Number(index) + 1}: ${
              error.message ?? "is invalid"
            }`,
          };
        }
        const emptyRequiredList =
          error.name === "minItems" &&
          error.params?.limit === 1 &&
          path.length === 1 &&
          requiredOf(schema).includes(top);
        if (error.name === "required" || emptyRequiredList) {
          return {
            ...error,
            message: `${labelAt(schema, uiSchema, path)} is required`,
          };
        }
        if (error.name === "minItems" && path.length === 1) {
          const limit = Number(error.params?.limit);
          return {
            ...error,
            message: `At least ${limit} ${labelAt(schema, uiSchema, path)} ${
              limit === 1 ? "is" : "are"
            } required`,
          };
        }
        return { ...error, message: capitalize(error.message ?? "Is invalid") };
      });

/**
 * A required string holding only whitespace is missing: the payload trims
 * it away, so report it the way Ajv reports an absent value.
 */
export const createCustomValidate =
  (
    schema: unknown,
    uiSchema: unknown,
    layout?: FormLayout,
    filters: ValidationFilters = {}
  ): CustomValidator =>
  (formData, errors) => {
    const data = (formData ?? {}) as FormData;
    for (const name of requiredOf(schema)) {
      const value = own(data, name);
      if (typeof value === "string" && value.trim() === "") {
        errors[name]?.addError(
          `${labelOf(schema, uiSchema, name)} is required`
        );
      }
    }
    for (const { anchor, config } of variantRowsDeclarations(
      schema,
      uiSchema
    )) {
      for (const message of validateVariantRowsValues(
        config,
        data
      )) {
        errors[anchor]?.addError(message);
      }
    }
    for (const comparison of fieldComparisonsOf(uiSchema)) {
      const left = own(data, comparison.left);
      const right = own(data, comparison.right);
      if (
        comparison.operator === "lessThan" &&
        typeof left === "number" &&
        typeof right === "number" &&
        left >= right
      ) {
        errors[comparison.left]?.addError(comparison.message);
      }
    }
    validateCompleteExclusiveGroups(
      schema,
      uiSchema,
      data,
      errors,
      layout,
      filters
    );
    return errors;
  };
