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
 * The form context core fields read and write through (RJSF `formContext`).
 */
import * as React from "react";
import type { RJSFSchema } from "@rjsf/utils";

import {
  createInitialState,
  shellReducer,
  type FormData,
  type Owner,
  type QuerySnapshot,
  type ScopeFilters,
  type ShellAction,
  type ShellState,
  type Source,
} from "./state";
import {
  coreFieldOf,
  deviceOptionsOf,
  effectiveOrder,
  locationOptionsOf,
  propertiesOf,
  variantRowsFieldOptionsOf,
} from "./ui-schema";

export interface FormContext {
  formData: Readonly<FormData>; // projected properties only
  query: QuerySnapshot; // immutable snapshot taken at mount
  pending: ReadonlySet<Owner>; // prefills not yet settled
  filters: Readonly<Record<string, ScopeFilters>>; // non-model device filters

  setFields(owner: Owner, patch: FormData, source: Source): void;
  setFilters(scope: string, patch: Partial<ScopeFilters>, source: Source): void;
  settle(owner: Owner): void;
}

/** Facts derived once from the schema and ui_schema that core fields look up. */
export interface FormLayout {
  /** Property → the core-field owner that writes it (a location's `typeField` included). */
  owners: Readonly<Record<string, Owner>>;
  /** Location property → its `typeField` sibling. */
  typeFields: Readonly<Record<string, string>>;
  /** Scope id → the device property, first in effective order, that renders its filters. */
  scopeHosts: Readonly<Record<string, string>>;
}

/** What the shell hands RJSF as `formContext`. */
export interface ShellFormContext extends FormContext {
  layout: FormLayout;
}

export const buildLayout = (schema: unknown, uiSchema: unknown): FormLayout => {
  const owners: Record<string, Owner> = {};
  const typeFields: Record<string, string> = {};
  const scopeHosts: Record<string, string> = {};
  for (const name of Object.keys(propertiesOf(schema))) {
    const coreField = coreFieldOf(uiSchema, name);
    if (coreField) owners[name] = `field:${name}`;
    if (coreField === "variantRows") {
      for (const owned of variantRowsFieldOptionsOf(uiSchema, name).ownedProperties) {
        owners[owned] = `field:${name}`;
      }
    }
    if (coreField === "location") {
      const { typeField } = locationOptionsOf(uiSchema, name);
      if (typeField) {
        owners[typeField] = `field:${name}`;
        typeFields[name] = typeField;
      }
    }
  }
  for (const name of effectiveOrder(schema, uiSchema)) {
    if (coreFieldOf(uiSchema, name) !== "device") continue;
    const { filterScope } = deviceOptionsOf(uiSchema, name);
    if (!(filterScope in scopeHosts)) scopeHosts[filterScope] = name;
  }
  return { owners, typeFields, scopeHosts };
};

/**
 * `setFields` may write only its owner's property and declared sibling (a location's
 * `typeField`), and only projected properties. Development builds throw otherwise.
 */
export const checkFieldPatch = (
  layout: FormLayout,
  schema: unknown,
  owner: Owner,
  patch: FormData
): void => {
  if (process.env.NODE_ENV === "production") return;
  const projected = propertiesOf(schema);
  for (const key of Object.keys(patch)) {
    if (!(key in projected) || layout.owners[key] !== owner) {
      throw new Error(`${owner} may not write "${key}"`);
    }
  }
};

export const contextOf = (formContext: unknown): ShellFormContext =>
  formContext as ShellFormContext;

export interface ShellController {
  state: ShellState;
  dispatch: React.Dispatch<ShellAction>;
  context: ShellFormContext;
}

/** The shell's reducer and the form context built over it. */
export const useShellState = (
  schema: RJSFSchema,
  uiSchema: unknown,
  query: QuerySnapshot
): ShellController => {
  const [state, dispatch] = React.useReducer(shellReducer, undefined, () =>
    createInitialState(schema, uiSchema, query)
  );
  const layout = React.useMemo(() => buildLayout(schema, uiSchema), [schema, uiSchema]);

  const setFields = React.useCallback(
    (owner: Owner, patch: FormData, source: Source) => {
      checkFieldPatch(layout, schema, owner, patch);
      dispatch({ type: "field-patch", owner, patch, source });
    },
    [layout, schema]
  );
  const setFilters = React.useCallback(
    (scope: string, patch: Partial<ScopeFilters>, source: Source) =>
      dispatch({ type: "filter-patch", scope, patch, source }),
    []
  );
  const settle = React.useCallback((owner: Owner) => dispatch({ type: "settle", owner }), []);

  const context = React.useMemo<ShellFormContext>(
    () => ({
      formData: state.formData,
      query,
      pending: state.pending,
      filters: state.filters,
      setFields,
      setFilters,
      settle,
      layout,
    }),
    [state.formData, query, state.pending, state.filters, setFields, setFilters, settle, layout]
  );
  return { state, dispatch, context };
};
