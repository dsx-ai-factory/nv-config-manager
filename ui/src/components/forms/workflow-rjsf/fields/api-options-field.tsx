"use client";
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
 * `ui:field: "apiOptions"`: an API-backed scalar or multiple selection (multiple when
 * the property is an array). The `location` field is the same picker with a typed
 * option identity and a `typeField` sibling.
 *
 * - Options load from `source`; required dependencies hold the request, empty optional
 *   ones are omitted, and SWR keys by URL so stale responses are never shown.
 * - With `clear_on_change`, a dependency change clears the value, unless the field's
 *   own prefill is pending.
 * - A pending prefill waits until no dependency owner is pending, then matches its raw
 *   URL values against the loaded options exactly once: a match is written with
 *   `setFields(..., "prefill")`; a miss, a load error, or an empty required dependency
 *   settles the owner.
 */
import * as React from "react";
import { getUiOptions, type FieldProps } from "@rjsf/utils";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { LoadingSpinner } from "@/components/ui/loading-spinner";
import useOptionSource from "@/hooks/useOptionSource";
import {
  dependencyFields,
  dependencySignature,
  type OptionSourceItem,
  type OptionSourceState,
} from "@/lib/option-source";
import type { OptionSource } from "@/types/workflow-catalog.types";

import { contextOf } from "../context";
import { corePrefillParams, queryValues } from "../prefill";
import type { FormData, Owner } from "../state";
import type { ApiOptionsOptions } from "../ui-schema";
import {
  fieldDescription,
  fieldLabel,
  isEmptyValue,
  isLoading,
  matchOptions,
  Picker,
  useSignatureChange,
} from "./shared";

interface SourceFieldProps {
  field: FieldProps;
  source: OptionSource;
  /** Location fields: the sibling property that receives the selected row's type. */
  typeField?: string;
  /** Whole URL parameters the field's prefill reads, in precedence order. */
  prefillParams: string[];
  presentation?: "select" | "grouped-checkboxes";
  selectAll?: boolean;
  showDescriptions?: boolean;
  metaText?: { key: "matching_device_count"; label: string };
  prune?: boolean;
}

/** A location's Site and Module may share an id, so its option key carries the type. */
const keyOf = (value: unknown, type: unknown, typed: boolean): string =>
  typed ? JSON.stringify([String(value), type == null ? null : String(type)]) : String(value);

interface GroupedOptionsProps {
  id: string;
  label: string;
  required?: boolean;
  description?: string;
  loaded: OptionSourceState;
  selected: string[];
  disabled: boolean;
  selectAll: boolean;
  showDescriptions: boolean;
  metaText?: { key: "matching_device_count"; label: string };
  onChange(keys: string[]): void;
}

const GroupedOptions = ({
  id,
  label,
  required,
  description,
  loaded,
  selected,
  disabled,
  selectAll,
  showDescriptions,
  metaText,
  onChange,
}: GroupedOptionsProps) => {
  const groups = new Map<string, OptionSourceItem[]>();
  for (const item of loaded.options) {
    const group = item.group ?? "";
    groups.set(group, [...(groups.get(group) ?? []), item]);
  }
  const allKeys = [...new Set(loaded.options.map((item) => String(item.value)))];
  const count = metaText ? loaded.meta?.[metaText.key] : undefined;
  const waiting = loaded.missingDependencies.length > 0;
  const busy = isLoading(loaded);

  return (
    <div className="space-y-3" id={id}>
      <div className="flex items-center justify-between gap-4">
        <Label>
          {label}
          {required ? <span aria-hidden="true"> *</span> : null}
        </Label>
        {selectAll && allKeys.length > 0 ? (
          <div className="flex gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={disabled || selected.length === allKeys.length}
              onClick={() => onChange(allKeys)}
            >
              Select all
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={disabled || selected.length === 0}
              onClick={() => onChange([])}
            >
              Clear
            </Button>
          </div>
        ) : null}
      </div>

      {busy ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <LoadingSpinner /> Loading {label}...
        </div>
      ) : waiting ? (
        <p className="text-sm text-muted-foreground">Select the required fields to load {label}.</p>
      ) : loaded.status === "error" ? (
        <p className="text-sm text-destructive">Could not load {label} options.</p>
      ) : loaded.status === "empty" ? (
        <p className="text-sm text-muted-foreground">No {label} found.</p>
      ) : (
        [...groups.entries()].map(([group, items], groupIndex) => (
          <fieldset key={group || "ungrouped"} className="space-y-2 rounded-md border p-3">
            {group ? <legend className="px-1 text-sm font-semibold">{group}</legend> : null}
            {items.map((item, itemIndex) => {
              const key = String(item.value);
              const optionId = `${id}-${groupIndex}-${itemIndex}`;
              return (
                <div key={key} className="flex items-start gap-2">
                  <Checkbox
                    id={optionId}
                    checked={selected.includes(key)}
                    disabled={disabled}
                    onCheckedChange={(checked) =>
                      onChange(
                        checked === true
                          ? [...new Set([...selected, key])]
                          : selected.filter((value) => value !== key)
                      )
                    }
                  />
                  <div className="space-y-1">
                    <Label htmlFor={optionId}>{item.label}</Label>
                    {showDescriptions && item.description ? (
                      <p className="text-sm text-muted-foreground">{item.description}</p>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </fieldset>
        ))
      )}

      {description ? <p className="text-sm text-muted-foreground">{description}</p> : null}
      {typeof count === "number" ? (
        <p className="text-sm text-muted-foreground">{metaText?.label}: {count}</p>
      ) : null}
      {(loaded.meta?.warnings ?? []).map((warning) => (
        <p key={warning} className="text-sm text-amber-700 dark:text-amber-300">{warning}</p>
      ))}
    </div>
  );
};

export const SourceOptionsField = ({
  field,
  source,
  typeField,
  prefillParams,
  presentation = "select",
  selectAll = false,
  showDescriptions = false,
  metaText,
  prune = false,
}: SourceFieldProps) => {
  const { name, schema, disabled, readonly, registry, required, fieldPathId } = field;
  const context = contextOf(registry.formContext);
  const { formData, pending, layout, setFields, settle, query } = context;
  const owner: Owner = `field:${name}`;
  const multiple = schema.type === "array";
  const typed = typeField !== undefined;
  const label = fieldLabel(field);

  const loaded = useOptionSource(source, formData);
  const ownPending = pending.has(owner);
  const dependencyPending = dependencyFields(source).some((dependency) => {
    const dependencyOwner = layout.owners[dependency];
    return dependencyOwner !== undefined && dependencyOwner !== owner && pending.has(dependencyOwner);
  });

  const patchFor = React.useCallback(
    (selected: readonly OptionSourceItem[]): FormData => {
      // A cleared list is `[]`, not absent: a form-only `minItems` then still applies
      // (e.g. "at least one Device Status"), and the payload omits an empty optional
      // list whose default is empty.
      if (multiple) {
        return { [name]: [...new Set(selected.map((item) => String(item.value)))] };
      }
      const [item] = selected;
      const patch: FormData = { [name]: item === undefined ? undefined : String(item.value) };
      if (typeField !== undefined) {
        patch[typeField] = item?.type == null ? undefined : String(item.type);
      }
      return patch;
    },
    [multiple, name, typeField]
  );

  React.useEffect(() => {
    if (!ownPending || dependencyPending || isLoading(loaded)) return;
    const matched =
      loaded.status === "success"
        ? matchOptions(queryValues(query, prefillParams), loaded.options, multiple)
        : [];
    if (matched.length > 0) setFields(owner, patchFor(matched), "prefill");
    else settle(owner);
  }, [ownPending, dependencyPending, loaded, query, prefillParams, multiple, owner, patchFor, setFields, settle]);

  const value = formData[name];
  const type = typeField !== undefined ? formData[typeField] : undefined;
  useSignatureChange(dependencySignature(source, formData), () => {
    if (ownPending || !source.clear_on_change) return;
    if (isEmptyValue(value) && isEmptyValue(type)) return;
    setFields(owner, patchFor([]), "user");
  });

  React.useEffect(() => {
    if (
      !prune ||
      ownPending ||
      dependencyPending ||
      loaded.missingDependencies.length === 0 ||
      (isEmptyValue(value) && isEmptyValue(type))
    ) {
      return;
    }
    setFields(owner, patchFor([]), "user");
  }, [
    prune,
    ownPending,
    dependencyPending,
    loaded.missingDependencies.length,
    value,
    type,
    owner,
    patchFor,
    setFields,
  ]);

  React.useEffect(() => {
    if (
      !prune ||
      ownPending ||
      dependencyPending ||
      (loaded.status !== "success" && loaded.status !== "empty")
    ) {
      return;
    }
    const current = multiple
      ? (Array.isArray(value) ? value : []).map(String)
      : isEmptyValue(value)
        ? []
        : [keyOf(value, type, typed)];
    const available = new Set(
      loaded.options.map((item) => keyOf(item.value, item.type, typed))
    );
    const retained = loaded.options.filter((item) =>
      current.includes(keyOf(item.value, item.type, typed))
    );
    if (current.some((key) => !available.has(key))) {
      setFields(owner, patchFor(retained), "user");
    }
  }, [
    prune,
    ownPending,
    dependencyPending,
    loaded,
    multiple,
    value,
    type,
    typed,
    owner,
    patchFor,
    setFields,
  ]);

  const items = loaded.options;
  const selected = multiple
    ? (Array.isArray(value) ? value : []).map((item) => keyOf(item, undefined, false))
    : isEmptyValue(value)
      ? ""
      : keyOf(value, type, typed);
  const waiting = loaded.missingDependencies.length > 0;
  const hasNoMatchingDevices = loaded.meta?.matching_device_count === 0;
  const pickerDisabled =
    Boolean(disabled || readonly) ||
    ownPending ||
    dependencyPending ||
    waiting ||
    isLoading(loaded) ||
    hasNoMatchingDevices;

  const feedback = (
    <>
      {presentation !== "grouped-checkboxes" && waiting ? (
        <p className="text-sm text-muted-foreground">Select the required fields to load {label}.</p>
      ) : null}
      {presentation !== "grouped-checkboxes" && loaded.status === "empty" ? (
        <p className="text-sm text-muted-foreground">No {label} found.</p>
      ) : null}
      {presentation !== "grouped-checkboxes" && metaText && typeof loaded.meta?.[metaText.key] === "number" ? (
        <p className="text-sm text-muted-foreground">
          {metaText.label}: {loaded.meta[metaText.key]}
        </p>
      ) : null}
      {presentation !== "grouped-checkboxes"
        ? (loaded.meta?.warnings ?? []).map((warning) => (
            <p key={warning} className="text-sm text-amber-700 dark:text-amber-300">
              {warning}
            </p>
          ))
        : null}
    </>
  );

  return presentation === "grouped-checkboxes" ? (
    <GroupedOptions
      id={fieldPathId.$id}
      label={label}
      required={required}
      description={fieldDescription(field)}
      loaded={loaded}
      selected={Array.isArray(selected) ? selected : selected ? [selected] : []}
      disabled={pickerDisabled}
      selectAll={selectAll}
      showDescriptions={showDescriptions}
      metaText={metaText}
      onChange={(keys) =>
        setFields(
          owner,
          patchFor(items.filter((item) => keys.includes(keyOf(item.value, item.type, typed)))),
          "user"
        )
      }
    />
  ) : (
    <div className="space-y-2">
      <Picker
        id={fieldPathId.$id}
        label={label}
        required={required}
        description={fieldDescription(field)}
        options={items.map((item) => ({
          key: item.label,
          value: keyOf(item.value, item.type, typed),
          ...(showDescriptions && item.description ? { description: item.description } : {}),
        }))}
        value={selected}
        multiple={multiple}
        disabled={pickerDisabled}
        busy={loaded.status === "loading" || ownPending}
        error={loaded.status === "error" ? `Could not load ${label} options.` : undefined}
        placeholder={getUiOptions(field.uiSchema).placeholder as string | undefined}
        onChange={(keys) =>
          setFields(
            owner,
            patchFor(items.filter((item) => keys.includes(keyOf(item.value, item.type, typed)))),
            "user"
          )
        }
      />
      {feedback}
    </div>
  );
};

export const ApiOptionsField = (props: FieldProps) => {
  const options = getUiOptions(props.uiSchema) as unknown as ApiOptionsOptions;
  const prefillParams = React.useMemo(
    () => corePrefillParams("apiOptions", props.name, options as unknown as Record<string, unknown>),
    // `options` is rebuilt every render; its inputs are the field's ui_schema and name.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [props.uiSchema, props.name]
  );
  return (
    <SourceOptionsField
      field={props}
      source={options.source as OptionSource}
      prefillParams={prefillParams}
      presentation={options.presentation}
      selectAll={options.selectAll}
      showDescriptions={options.showDescriptions}
      metaText={options.metaText}
      prune
    />
  );
};
