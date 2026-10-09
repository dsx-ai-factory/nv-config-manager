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

/** Pieces the three core fields share: option matching and the labelled picker. */
import * as React from "react";
import {
  ariaDescribedByIds,
  descriptionId,
  getUiOptions,
  type FieldProps,
} from "@rjsf/utils";

import { Label } from "@/components/ui/label";
import { LoadingSpinner } from "@/components/ui/loading-spinner";
import { SelectBox } from "@/components/ui/selectbox";
import type { OptionSourceItem, OptionSourceState } from "@/lib/option-source";

/**
 * Options matching raw URL values, by value first and then by label (links may carry a
 * display name). A scalar field matches only its first raw value.
 */
export const matchOptions = (
  raw: readonly string[],
  options: readonly OptionSourceItem[],
  multiple: boolean
): OptionSourceItem[] => {
  const matched: OptionSourceItem[] = [];
  for (const value of multiple ? raw : raw.slice(0, 1)) {
    const option =
      options.find((candidate) => String(candidate.value) === value) ??
      options.find((candidate) => candidate.label === value);
    if (option && !matched.includes(option)) matched.push(option);
  }
  return matched;
};

/** Options have not arrived yet: in flight, or the runtime config is still loading. */
export const isLoading = (state: OptionSourceState): boolean =>
  state.status === "loading" ||
  (state.status === "idle" && state.missingDependencies.length === 0);

export const isEmptyValue = (value: unknown): boolean =>
  value === undefined ||
  value === null ||
  value === "" ||
  (Array.isArray(value) && value.length === 0);

/** A field's label: `ui:title`, schema `title`, or its name. */
export const fieldLabel = ({
  name,
  schema,
  uiSchema,
  registry,
}: FieldProps): string => {
  const { title } = getUiOptions(uiSchema, registry.globalUiOptions);
  return typeof title === "string"
    ? title
    : typeof schema.title === "string"
    ? schema.title
    : name;
};

/**
 * `ui:description`, else the schema description unless
 * `ui:globalOptions.hideSchemaDescriptions` hides schema descriptions.
 */
export const fieldDescription = ({
  schema,
  uiSchema,
  registry,
}: FieldProps): string | undefined => {
  const { description } = getUiOptions(uiSchema);
  if (typeof description === "string") return description;
  if (registry.globalUiOptions?.hideSchemaDescriptions === true)
    return undefined;
  return typeof schema.description === "string"
    ? schema.description
    : undefined;
};

/**
 * The required marker after a label. Hidden from assistive technology, so the
 * accessible name is the label alone; controls carry `aria-required` instead.
 */
export const RequiredMark = ({ required }: { required?: boolean }) =>
  required ? <span aria-hidden="true"> *</span> : null;

export interface PickerProps {
  id: string;
  label: string;
  required?: boolean;
  description?: string;
  options: readonly { key: string; value: string; description?: string }[];
  value: string | string[];
  multiple: boolean;
  disabled: boolean;
  busy: boolean;
  /** The form value failed RJSF validation (separate from an option-load error). */
  invalid?: boolean;
  error?: string;
  placeholder?: string;
  onChange: (keys: string[]) => void;
}

/** A labelled searchable picker with a loading spinner and a load error. */
export const Picker = ({
  id,
  label,
  required,
  description,
  options,
  value,
  multiple,
  disabled,
  busy,
  invalid,
  error,
  placeholder,
  onChange,
}: PickerProps) => {
  const labelId = `${id}__label`;
  const loadErrorId = `${id}__load-error`;
  const describedBy = `${ariaDescribedByIds(id)}${
    error ? ` ${loadErrorId}` : ""
  }`;
  return (
    <div className="space-y-2" data-testid={`picker-${id}`}>
      <Label id={labelId} htmlFor={id}>
        {label}
        <RequiredMark required={required} />
      </Label>
      <div className="flex items-center space-x-2">
        <SelectBox
          id={id}
          accessibleLabel={label}
          options={[...options]}
          value={value}
          onChange={(next) =>
            onChange((Array.isArray(next) ? next : [next]).filter(Boolean))
          }
          placeholder={
            placeholder ?? `Select ${multiple ? "" : "a "}${label}...`
          }
          inputPlaceholder={`Search ${label}`}
          emptyPlaceholder={`No ${label} found.`}
          multiple={multiple}
          disabled={disabled}
          describedBy={describedBy}
          required={required}
          invalid={Boolean(invalid || error)}
        />
        {busy ? <LoadingSpinner /> : null}
      </div>
      {description ? (
        <p id={descriptionId(id)} className="text-sm text-muted-foreground">
          {description}
        </p>
      ) : null}
      {error ? (
        <p id={loadErrorId} className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
};

/** Run `onChange` after a render in which `signature` differs from the previous one. */
export const useSignatureChange = (signature: string, onChange: () => void) => {
  const previous = React.useRef(signature);
  const latest = React.useRef(onChange);
  latest.current = onChange;
  React.useEffect(() => {
    if (previous.current === signature) return;
    previous.current = signature;
    latest.current();
  }, [signature]);
};
