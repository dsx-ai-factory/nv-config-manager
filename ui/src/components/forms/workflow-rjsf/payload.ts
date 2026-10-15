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
 * Shallow payload preparation from the shell's projected form data. It never reads
 * device filters, the query snapshot, or pending owners, so non-model state cannot be
 * submitted; Pydantic performs the authoritative normalization and validation.
 */
import type { FormData } from "./state";
import { normalizeVariantRowsPayload } from "./fields/variant-rows-field";
import {
  isTextarea,
  propertiesOf,
  requiredOf,
  resolveRef,
  variantRowsDeclarations,
} from "./ui-schema";

const isEmptyDefault = (value: unknown): boolean =>
  value === undefined || (Array.isArray(value) && value.length === 0);

/**
 * - only projected top-level properties; `undefined` omitted;
 * - single-line strings trimmed; blank strings omitted; textarea values kept verbatim
 *   unless blank;
 * - an empty optional list whose default is absent or empty is omitted (the server
 *   default applies);
 * - nested arrays and objects are passed through without recursive cleanup;
 * - hidden derived values (a location's type sibling, `trigger`) are kept.
 */
export const buildPayload = (
  schema: unknown,
  uiSchema: unknown,
  formData: Readonly<FormData>
): FormData => {
  const payload: FormData = {};
  const required = requiredOf(schema);
  for (const [name, node] of Object.entries(propertiesOf(schema))) {
    let value = Object.prototype.hasOwnProperty.call(formData, name) ? formData[name] : undefined;
    if (value === undefined) continue;
    if (typeof value === "string") {
      if (value.trim() === "") continue;
      if (!isTextarea(uiSchema, name)) value = value.trim();
    }
    if (
      Array.isArray(value) &&
      value.length === 0 &&
      !required.includes(name) &&
      isEmptyDefault(resolveRef(schema, node).default)
    ) {
      continue;
    }
    payload[name] = value;
  }
  return variantRowsDeclarations(schema, uiSchema).reduce(
    (next, { config }) => normalizeVariantRowsPayload(config, next),
    payload
  );
};
