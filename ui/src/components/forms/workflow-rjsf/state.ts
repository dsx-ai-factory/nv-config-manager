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
 * The controlled shell's state: projected form data, non-model device filter scopes,
 * pending URL prefill owners, and mapped server errors, behind one reducer.
 *
 * Core fields own option loading, dependency handling, and query matching; the reducer
 * only applies their patches atomically and guards prefill writes.
 */
import {
  deepEquals,
  getDefaultFormState,
  type ErrorSchema,
  type Experimental_DefaultFormStateBehavior,
  type RJSFSchema,
} from "@rjsf/utils";

import { initialPending, standardPrefill } from "./prefill";
import { propertiesOf, type ExclusiveGroup } from "./ui-schema";
import { workflowValidator } from "./validator";

export type FormData = Record<string, unknown>;
export type Owner = `field:${string}` | `scope:${string}`;
export type Source = "user" | "prefill";
export type QuerySnapshot = Readonly<Record<string, readonly string[]>>;

/** Shared Site/Tenant/Status state of a device filter scope; never submitted. */
export interface ScopeFilters {
  site?: { id: string; type: string };
  tenant: readonly string[];
  status: readonly string[];
}

export const EMPTY_SCOPE: ScopeFilters = Object.freeze({
  tenant: [],
  status: [],
});

/**
 * Passed both to the initial `getDefaultFormState` and to `<Form>`, so the shell and
 * RJSF compute the same defaults.
 *
 * - `arrayMinItems.populate: "never"`: required lists start as `[]`, never with
 *   synthetic `undefined` items.
 * - `emptyObjectFields: "populateRequiredDefaults"`: an optional nested object is not
 *   created from its children's defaults, so it stays empty and submittable. That
 *   option also skips optional top-level defaults, which {@link createInitialState}
 *   therefore seeds itself.
 */
export const RJSF_DEFAULT_STATE_BEHAVIOR: Experimental_DefaultFormStateBehavior =
  {
    arrayMinItems: { populate: "never" },
    emptyObjectFields: "populateRequiredDefaults",
  };

export interface ShellState {
  /** Projected properties only. */
  formData: Readonly<FormData>;
  pending: ReadonlySet<Owner>;
  filters: Readonly<Record<string, ScopeFilters>>;
  /** Mapped 422 errors (`extraErrors`). */
  serverErrors: ErrorSchema | undefined;
}

export type ShellAction =
  | {
      type: "field-patch";
      owner: Owner;
      patch: FormData;
      source: Source;
      exclusiveGroups?: readonly ExclusiveGroup[];
    }
  | {
      type: "filter-patch";
      scope: string;
      patch: Partial<ScopeFilters>;
      source: Source;
      exclusiveGroups?: readonly ExclusiveGroup[];
    }
  | { type: "settle"; owner: Owner }
  /** `base` is the form data passed to the RJSF render that produced `next`. */
  | {
      type: "rjsf-change";
      base: Readonly<FormData>;
      next: Readonly<FormData>;
      exclusiveGroups?: readonly ExclusiveGroup[];
    }
  | { type: "server-errors"; errors: ErrorSchema | undefined };

/**
 * Initial state: standard URL prefills, then top-level schema defaults for properties
 * still absent, then RJSF's own default computation with the shared behavior.
 * Explicit prefills win over defaults.
 */
export const createInitialState = (
  schema: RJSFSchema,
  uiSchema: unknown,
  query: QuerySnapshot,
  exclusiveGroups: readonly ExclusiveGroup[] = []
): ShellState => {
  const data = standardPrefill(schema, uiSchema, query);
  for (const [name, property] of Object.entries(propertiesOf(schema))) {
    if (!(name in data) && property.default !== undefined) {
      data[name] = structuredClone(property.default);
    }
  }
  const formData = getDefaultFormState(
    workflowValidator,
    schema,
    data,
    schema,
    false,
    RJSF_DEFAULT_STATE_BEHAVIOR
  );
  const initialFormData = (formData ?? {}) as FormData;
  const active = activeExclusiveGroups(initialFormData, {}, exclusiveGroups);
  const normalized =
    active.length > 1
      ? clearInactiveGroups(
          initialFormData,
          {},
          new Set(),
          exclusiveGroups,
          active.at(-1)!
        ).formData
      : initialFormData;
  return {
    formData: normalized,
    pending: initialPending(schema, uiSchema, query),
    filters: {},
    serverErrors: undefined,
  };
};

const withoutOwner = (
  pending: ReadonlySet<Owner>,
  owner: Owner
): ReadonlySet<Owner> => {
  if (!pending.has(owner)) return pending;
  const next = new Set(pending);
  next.delete(owner);
  return next;
};

/** Drop mapped server errors for changed keys, and form-level ones on any change. */
const clearServerErrors = (
  errors: ErrorSchema | undefined,
  keys: readonly string[]
): ErrorSchema | undefined => {
  if (!errors || keys.length === 0) return errors;
  const rest = Object.fromEntries(
    Object.entries(errors).filter(
      ([key]) => key !== "__errors" && !keys.includes(key)
    )
  );
  return Object.keys(rest).length > 0 ? (rest as ErrorSchema) : undefined;
};

/** Keys whose value differs between `before` and `after` (absent equals `undefined`). */
const changedKeys = (
  before: Readonly<FormData>,
  after: Readonly<FormData>
): string[] =>
  [...new Set([...Object.keys(before), ...Object.keys(after)])].filter(
    (key) => !deepEquals(before[key], after[key])
  );

/** Merge a patch; `undefined` deletes a property. */
const applyPatch = (
  data: Readonly<FormData>,
  patch: Readonly<FormData>
): FormData => {
  const next: FormData = { ...data };
  for (const [key, value] of Object.entries(patch)) {
    if (value === undefined) delete next[key];
    else next[key] = value;
  }
  return next;
};

const hasValue = (value: unknown): boolean =>
  value !== undefined &&
  value !== null &&
  value !== "" &&
  (!Array.isArray(value) || value.length > 0);

const scopeHasValue = (scope: ScopeFilters | undefined): boolean =>
  scope?.site !== undefined ||
  Boolean(scope?.tenant.length) ||
  Boolean(scope?.status.length);

/** Input modes that currently contain a projected value or a device-filter value. */
export const activeExclusiveGroups = (
  formData: Readonly<FormData>,
  filters: Readonly<Record<string, ScopeFilters>>,
  groups: readonly ExclusiveGroup[]
): number[] =>
  groups.flatMap((group, index) =>
    group.fields.some((field) => hasValue(formData[field])) ||
    group.filterScopes.some((scope) => scopeHasValue(filters[scope]))
      ? [index]
      : []
  );

const clearInactiveGroups = (
  formData: Readonly<FormData>,
  filters: Readonly<Record<string, ScopeFilters>>,
  pending: ReadonlySet<Owner>,
  groups: readonly ExclusiveGroup[],
  active: number
): {
  formData: FormData;
  filters: Record<string, ScopeFilters>;
  pending: ReadonlySet<Owner>;
} => {
  const nextData = { ...formData };
  const nextFilters = { ...filters };
  let nextPending = pending;
  groups.forEach((group, index) => {
    if (index === active) return;
    for (const field of group.fields) {
      delete nextData[field];
      nextPending = withoutOwner(nextPending, `field:${field}`);
    }
    for (const scope of group.filterScopes) {
      delete nextFilters[scope];
      nextPending = withoutOwner(nextPending, `scope:${scope}`);
    }
  });
  return { formData: nextData, filters: nextFilters, pending: nextPending };
};

const activatingFieldGroup = (
  groups: readonly ExclusiveGroup[],
  changed: readonly string[],
  formData: Readonly<FormData>
): number | undefined => {
  const index = groups.findIndex((group) =>
    changed.some(
      (field) => group.fields.includes(field) && hasValue(formData[field])
    )
  );
  return index < 0 ? undefined : index;
};

const activatingFilterGroup = (
  groups: readonly ExclusiveGroup[],
  scope: string,
  filters: Readonly<Record<string, ScopeFilters>>
): number | undefined => {
  const index = groups.findIndex(
    (group) =>
      group.filterScopes.includes(scope) && scopeHasValue(filters[scope])
  );
  return index < 0 ? undefined : index;
};

const preferExistingPrefill = (
  source: Source,
  activeBefore: readonly number[],
  activating: number | undefined
): boolean =>
  source === "prefill" &&
  activating !== undefined &&
  activeBefore.some((index) => index !== activating);

export const shellReducer = (
  state: ShellState,
  action: ShellAction
): ShellState => {
  switch (action.type) {
    case "field-patch": {
      const { owner, patch, source, exclusiveGroups = [] } = action;
      // A prefill applies only while its owner is still pending: a late result after a
      // user edit or an earlier settlement is ignored.
      if (source === "prefill" && !state.pending.has(owner)) return state;
      let formData = applyPatch(state.formData, patch);
      let changed = changedKeys(state.formData, formData);
      let pending = withoutOwner(state.pending, owner);
      let filters = state.filters;
      const activating = activatingFieldGroup(
        exclusiveGroups,
        changed,
        formData
      );
      if (
        preferExistingPrefill(
          source,
          activeExclusiveGroups(state.formData, state.filters, exclusiveGroups),
          activating
        )
      ) {
        formData = state.formData as FormData;
        changed = [];
      } else if (activating !== undefined) {
        ({ formData, filters, pending } = clearInactiveGroups(
          formData,
          filters,
          pending,
          exclusiveGroups,
          activating
        ));
        changed = changedKeys(state.formData, formData);
      }
      if (changed.length === 0 && pending === state.pending) return state;
      return {
        ...state,
        formData: changed.length > 0 ? formData : state.formData,
        filters,
        pending,
        serverErrors:
          source === "user"
            ? clearServerErrors(
                state.serverErrors,
                owner.startsWith("field:")
                  ? [...changed, owner.slice("field:".length)]
                  : changed
              )
            : state.serverErrors,
      };
    }
    case "filter-patch": {
      const { scope, patch, source, exclusiveGroups = [] } = action;
      const owner: Owner = `scope:${scope}`;
      if (source === "prefill" && !state.pending.has(owner)) return state;
      const current = state.filters[scope] ?? EMPTY_SCOPE;
      const next: ScopeFilters = { ...current, ...patch };
      if ("site" in patch && patch.site === undefined) delete next.site;
      let pending = withoutOwner(state.pending, owner);
      if (deepEquals(current, next) && pending === state.pending) return state;
      let formData = state.formData as FormData;
      let filters: Record<string, ScopeFilters> = {
        ...state.filters,
        [scope]: next,
      };
      const activating = activatingFilterGroup(exclusiveGroups, scope, filters);
      if (
        preferExistingPrefill(
          source,
          activeExclusiveGroups(state.formData, state.filters, exclusiveGroups),
          activating
        )
      ) {
        filters = state.filters as Record<string, ScopeFilters>;
      } else if (activating !== undefined) {
        ({ formData, filters, pending } = clearInactiveGroups(
          formData,
          filters,
          pending,
          exclusiveGroups,
          activating
        ));
      }
      return { ...state, formData, filters, pending };
    }
    case "settle": {
      const pending = withoutOwner(state.pending, action.owner);
      return pending === state.pending ? state : { ...state, pending };
    }
    case "rjsf-change": {
      // Three-way merge: apply only what this event changed relative to the data its
      // render was given, so a field patch applied since that render survives.
      const changed = changedKeys(action.base, action.next);
      if (changed.length === 0) return state;
      let formData = applyPatch(
        state.formData,
        Object.fromEntries(changed.map((key) => [key, action.next[key]]))
      );
      let pending = state.pending;
      for (const key of changed)
        pending = withoutOwner(pending, `field:${key}`);
      let filters = state.filters;
      const exclusiveGroups = action.exclusiveGroups ?? [];
      const activating = activatingFieldGroup(
        exclusiveGroups,
        changed,
        formData
      );
      if (activating !== undefined) {
        ({ formData, filters, pending } = clearInactiveGroups(
          formData,
          filters,
          pending,
          exclusiveGroups,
          activating
        ));
      }
      return {
        ...state,
        formData,
        filters,
        pending,
        serverErrors: clearServerErrors(state.serverErrors, changed),
      };
    }
    case "server-errors":
      return { ...state, serverErrors: action.errors };
  }
};
