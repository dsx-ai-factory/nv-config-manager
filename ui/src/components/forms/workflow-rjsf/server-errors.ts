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
 * Map a workflow API 422 response to RJSF `extraErrors`.
 *
 * - FastAPI body validation: `detail: [{loc, msg, ...}]`. The leading `body` segment is
 *   removed and each message is placed on that field; extra entry keys are ignored.
 * - `canonicalize_input` failures use the same list shape with `loc: ["body"]`, so
 *   they become form-level errors. String details remain supported for older APIs.
 *
 * Unknown or excluded locations, and hidden standard fields, become form-level
 * (root `__errors`). A location's hidden type sibling reports on the location field.
 * A field that edits a whole list in one control (a core field or a multi-select)
 * shows its items' errors itself, prefixed with the item number.
 */
import type { ErrorSchema } from "@rjsf/utils";

import type { FormLayout } from "./context";
import { coreFieldOf, fieldUi, isHidden, isObject, propertiesOf, resolveRef } from "./ui-schema";

interface DetailItem {
  loc: Array<string | number>;
  msg: string;
}

const isDetailItem = (item: unknown): item is DetailItem =>
  isObject(item) &&
  Array.isArray(item.loc) &&
  item.loc.every((segment) => typeof segment === "string" || typeof segment === "number") &&
  typeof item.msg === "string";

/** Whether one control renders the whole value, so nested paths have no field of their own. */
export const editsWholeValue = (schema: unknown, uiSchema: unknown, name: string): boolean => {
  if (fieldUi(uiSchema, name)["ui:field"] !== undefined) return true;
  const property = resolveRef(schema, propertiesOf(schema)[name]);
  if (property.type !== "array" || property.uniqueItems !== true) return false;
  return Array.isArray(resolveRef(schema, property.items).enum);
};

type ErrorNode = { __errors?: string[]; [key: string]: unknown };

const addError = (root: ErrorNode, path: readonly (string | number)[], message: string) => {
  let node = root;
  for (const segment of path) {
    const key = String(segment);
    const child = node[key];
    node = (isObject(child) ? child : (node[key] = {})) as ErrorNode;
  }
  (node.__errors ??= []).push(message);
};

/** `null` when `detail` has no supported 422 shape; the caller then shows a toast. */
export const mapServerErrors = (
  schema: unknown,
  uiSchema: unknown,
  layout: FormLayout,
  detail: unknown
): ErrorSchema | null => {
  if (typeof detail === "string" && detail.trim() !== "") {
    return { __errors: [detail] } as ErrorSchema;
  }
  if (!Array.isArray(detail) || detail.length === 0 || !detail.every(isDetailItem)) {
    return null;
  }
  const properties = propertiesOf(schema);
  const root: ErrorNode = {};
  for (const { loc, msg } of detail) {
    const path = loc[0] === "body" ? loc.slice(1) : loc;
    const top = path.length > 0 ? String(path[0]) : "";
    const owner = layout.owners[top];
    const target = owner && owner !== `field:${top}` ? owner.slice("field:".length) : top;
    const known = Object.prototype.hasOwnProperty.call(properties, target);
    if (!known || (target === top && isHidden(uiSchema, top) && !coreFieldOf(uiSchema, top))) {
      addError(root, [], path.length > 0 ? `${path.join(".")}: ${msg}` : msg);
    } else if (target !== top || path.length === 1) {
      addError(root, [target], msg);
    } else if (editsWholeValue(schema, uiSchema, target)) {
      const rest = path.slice(1);
      const prefix =
        typeof rest[0] === "number" ? `Item ${rest[0] + 1}` : rest.map(String).join(".");
      addError(root, [target], `${prefix}: ${msg}`);
    } else {
      addError(root, path, msg);
    }
  }
  return root as ErrorSchema;
};
