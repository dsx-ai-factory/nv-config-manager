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
 * A small RJSF theme drawn with this app's `components/ui` primitives (`@rjsf/shadcn`
 * targets Tailwind 4). Only RJSF's extension points are used: templates, the widgets
 * the v1 contract names (text, textarea, checkbox, select; RJSF's own hidden widget),
 * and the four core fields. Numbers, enums, arrays of enums, and string lists use
 * RJSF's default fields with these templates.
 *
 * `ui:globalOptions.hideSchemaDescriptions` (read from `registry.globalUiOptions`)
 * hides schema descriptions; `ui:description` and `ui:help` still show.
 */
import * as React from "react";
import { Plus, Trash2 } from "lucide-react";
import {
  ariaDescribedByIds,
  descriptionId,
  enumOptionsIndexForValue,
  enumOptionsValueForIndex,
  getInputProps,
  getUiOptions,
  type ArrayFieldItemTemplateProps,
  type ArrayFieldTemplateProps,
  type BaseInputTemplateProps,
  type FieldTemplateProps,
  type GlobalUISchemaOptions,
  type IconButtonProps,
  type ObjectFieldTemplateProps,
  type RegistryFieldsType,
  type RegistryWidgetsType,
  type TemplatesType,
  type UiSchema,
  type WidgetProps,
} from "@rjsf/utils";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SelectBox } from "@/components/ui/selectbox";
import { Textarea } from "@/components/ui/textarea";

import { ApiOptionsField } from "./fields/api-options-field";
import { DeviceField } from "./fields/device-field";
import { LocationField } from "./fields/location-field";
import { RequiredMark } from "./fields/shared";
import { VariantRowsField } from "./fields/variant-rows-field";

/** `ui:description`, else the schema description unless schema descriptions are hidden. */
const descriptionText = (
  uiSchema: UiSchema | undefined,
  schemaDescription: unknown,
  globalUiOptions: GlobalUISchemaOptions | undefined
): string | undefined => {
  const { description } = getUiOptions(uiSchema);
  if (typeof description === "string") return description;
  if (globalUiOptions?.hideSchemaDescriptions === true) return undefined;
  return typeof schemaDescription === "string" && schemaDescription ? schemaDescription : undefined;
};

const Muted = ({ id, children }: { id?: string; children: React.ReactNode }) => (
  <p id={id} className="text-sm text-muted-foreground">
    {children}
  </p>
);

/** Errors no field shows: the form-level (root) errors, server ones included. */
const FormErrors = ({ errors }: { errors: string[] }) => (
  <Alert variant="destructive">
    <AlertTitle>The workflow input is invalid</AlertTitle>
    <AlertDescription>
      <ul className="mt-2 list-disc pl-5">
        {[...new Set(errors)].map((error) => (
          <li key={error}>{error}</li>
        ))}
      </ul>
    </AlertDescription>
  </Alert>
);

const FieldTemplate = ({
  id,
  label,
  children,
  errors,
  rawErrors,
  help,
  hidden,
  required,
  displayLabel,
  schema,
  uiSchema,
  registry,
  fieldPathId,
}: FieldTemplateProps) => {
  if (hidden) return <div className="hidden">{children}</div>;
  if (fieldPathId.path.length === 0) {
    return (
      <div className="space-y-6">
        {children}
        {rawErrors && rawErrors.length > 0 ? <FormErrors errors={rawErrors} /> : null}
      </div>
    );
  }
  // Booleans draw their label beside the checkbox; core fields and lists draw their own.
  const showLabel = displayLabel && schema.type !== "boolean";
  const description = showLabel
    ? descriptionText(uiSchema, schema.description, registry.globalUiOptions)
    : undefined;
  return (
    <div className="space-y-2">
      {showLabel ? (
        <Label htmlFor={id}>
          {label}
          <RequiredMark required={required} />
        </Label>
      ) : null}
      {children}
      {description ? <Muted id={descriptionId(id)}>{description}</Muted> : null}
      {errors}
      {help}
    </div>
  );
};

const FieldErrorTemplate = ({ errors, fieldPathId }: { errors?: React.ReactNode[]; fieldPathId: { $id: string } }) =>
  errors && errors.length > 0 ? (
    <div id={`${fieldPathId.$id}__error`} className="space-y-1">
      {errors.map((error, index) => (
        <p key={index} className="text-sm font-medium text-destructive">
          {error}
        </p>
      ))}
    </div>
  ) : null;

const FieldHelpTemplate = ({ help, fieldPathId }: { help?: string | React.ReactElement; fieldPathId: { $id: string } }) =>
  help ? <Muted id={`${fieldPathId.$id}__help`}>{help}</Muted> : null;

/** The root object is the form itself; a nested object is a fieldset. */
const ObjectFieldTemplate = ({
  title,
  schema,
  uiSchema,
  registry,
  properties,
  fieldPathId,
}: ObjectFieldTemplateProps) => {
  const body = properties.map((property) => (
    <React.Fragment key={property.name}>{property.content}</React.Fragment>
  ));
  if (fieldPathId.path.length === 0) return <>{body}</>;
  const description = descriptionText(uiSchema, schema.description, registry.globalUiOptions);
  return (
    <fieldset className="space-y-6 rounded-md border p-4">
      {title ? <legend className="px-1 text-sm font-semibold">{title}</legend> : null}
      {description ? <Muted>{description}</Muted> : null}
      {body}
    </fieldset>
  );
};

const ArrayFieldTemplate = ({
  title,
  schema,
  uiSchema,
  registry,
  required,
  items,
  canAdd,
  onAddClick,
  disabled,
  readonly,
}: ArrayFieldTemplateProps) => {
  const description = descriptionText(uiSchema, schema.description, registry.globalUiOptions);
  return (
    <div className="space-y-3">
      {title ? (
        <Label>
          {title}
          <RequiredMark required={required} />
        </Label>
      ) : null}
      {items}
      {canAdd ? (
        <Button type="button" variant="outline" size="sm" disabled={disabled || readonly} onClick={onAddClick}>
          <Plus className="mr-1 h-4 w-4" />
          Add {title || "item"}
        </Button>
      ) : null}
      {description ? <Muted>{description}</Muted> : null}
    </div>
  );
};

const ArrayFieldItemTemplate = ({ children, buttonsProps, index, parentUiSchema }: ArrayFieldItemTemplateProps) => {
  const title = getUiOptions(parentUiSchema).title ?? "item";
  return (
    <div className="flex items-start gap-2">
      <div className="min-w-0 flex-1">{children}</div>
      {buttonsProps.hasRemove ? (
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="shrink-0"
          aria-label={`Remove ${String(title)} ${index + 1}`}
          disabled={buttonsProps.disabled || buttonsProps.readonly}
          onClick={buttonsProps.onRemoveItem}
        >
          <Trash2 className="h-4 w-4" />
        </Button>
      ) : null}
    </div>
  );
};

const BaseInputTemplate = ({
  id,
  htmlName,
  value,
  readonly,
  disabled,
  autofocus,
  placeholder,
  onBlur,
  onFocus,
  onChange,
  options,
  schema,
  type,
  required,
  rawErrors,
}: BaseInputTemplateProps) => {
  const inputProps = getInputProps(schema, type, options);
  const isNumber = inputProps.type === "number" || inputProps.type === "integer";
  return (
    <Input
      id={id}
      name={htmlName || id}
      {...inputProps}
      value={isNumber ? (value || value === 0 ? value : "") : (value ?? "")}
      placeholder={placeholder}
      readOnly={readonly}
      disabled={disabled}
      autoFocus={autofocus}
      aria-required={required || undefined}
      aria-invalid={rawErrors?.length ? true : undefined}
      aria-describedby={ariaDescribedByIds(id)}
      onChange={(event) => onChange(event.target.value === "" ? options.emptyValue : event.target.value)}
      onBlur={(event) => onBlur(id, event.target.value)}
      onFocus={(event) => onFocus(id, event.target.value)}
    />
  );
};

const NoButton = (_props: IconButtonProps) => null;

const templates: Partial<TemplatesType> = {
  BaseInputTemplate,
  FieldTemplate,
  FieldErrorTemplate,
  FieldHelpTemplate,
  ObjectFieldTemplate,
  ArrayFieldTemplate,
  ArrayFieldItemTemplate,
  // FieldTemplate, ObjectFieldTemplate, and ArrayFieldTemplate draw titles and
  // descriptions themselves (respecting hideSchemaDescriptions).
  DescriptionFieldTemplate: () => null,
  TitleFieldTemplate: () => null,
  ArrayFieldTitleTemplate: () => null,
  ArrayFieldDescriptionTemplate: () => null,
  ButtonTemplates: {
    // The form renders its own submit button (disabled while prefill is pending).
    SubmitButton: () => null,
    AddButton: NoButton,
    CopyButton: NoButton,
    MoveDownButton: NoButton,
    MoveUpButton: NoButton,
    RemoveButton: NoButton,
    ClearButton: NoButton,
  },
};

const TextareaWidget = ({
  id,
  htmlName,
  value,
  readonly,
  disabled,
  autofocus,
  placeholder,
  onBlur,
  onFocus,
  onChange,
  options,
  required,
  rawErrors,
}: WidgetProps) => (
  <Textarea
    id={id}
    name={htmlName || id}
    value={value ?? ""}
    placeholder={placeholder}
    readOnly={readonly}
    disabled={disabled}
    autoFocus={autofocus}
    rows={typeof options.rows === "number" ? options.rows : undefined}
    aria-required={required || undefined}
    aria-invalid={rawErrors?.length ? true : undefined}
    aria-describedby={ariaDescribedByIds(id)}
    onChange={(event) => onChange(event.target.value === "" ? options.emptyValue : event.target.value)}
    onBlur={(event) => onBlur(id, event.target.value)}
    onFocus={(event) => onFocus(id, event.target.value)}
  />
);

const CheckboxWidget = ({
  id,
  value,
  disabled,
  readonly,
  label,
  required,
  schema,
  uiSchema,
  registry,
  onChange,
}: WidgetProps) => {
  const description = descriptionText(uiSchema, schema.description, registry.globalUiOptions);
  return (
    <div className="flex flex-row items-start space-x-3 space-y-0">
      <Checkbox
        id={id}
        checked={value === true}
        disabled={disabled || readonly}
        aria-describedby={ariaDescribedByIds(id)}
        onCheckedChange={(checked) => onChange(checked === true)}
      />
      <div className="space-y-1 leading-none">
        <Label htmlFor={id}>
          {label}
          <RequiredMark required={required} />
        </Label>
        {description ? <Muted id={descriptionId(id)}>{description}</Muted> : null}
      </div>
    </div>
  );
};

/** `enum`s and arrays of them (`multiple`), as the searchable SelectBox. */
const SelectWidget = ({
  id,
  value,
  multiple,
  disabled,
  readonly,
  label,
  placeholder,
  options,
  onChange,
}: WidgetProps) => {
  const enumOptions = options.enumOptions ?? [];
  const toKey = (item: unknown) => {
    const index = enumOptionsIndexForValue(item, enumOptions, false);
    return typeof index === "string" ? index : "";
  };
  const selected = multiple
    ? (Array.isArray(value) ? value : []).map(toKey)
    : value === undefined
      ? ""
      : toKey(value);
  return (
    <SelectBox
      id={id}
      options={enumOptions.map((option, index) => ({ key: option.label, value: String(index) }))}
      value={selected}
      onChange={(keys) =>
        onChange(
          enumOptionsValueForIndex(
            multiple ? (Array.isArray(keys) ? keys : [keys]) : String(keys),
            enumOptions,
            options.emptyValue
          )
        )
      }
      placeholder={placeholder || `Select ${multiple ? "" : "a "}${label}...`}
      inputPlaceholder={`Search ${label}`}
      emptyPlaceholder={`No ${label} found.`}
      multiple={multiple}
      searchable={enumOptions.length > 7}
      disabled={disabled || readonly}
      describedBy={ariaDescribedByIds(id)}
    />
  );
};

const widgets: RegistryWidgetsType = {
  CheckboxWidget,
  SelectWidget,
  TextareaWidget,
};

/** The permanent core fields (`ui:field`). */
const fields: RegistryFieldsType = {
  apiOptions: ApiOptionsField,
  device: DeviceField,
  location: LocationField,
  variantRows: VariantRowsField,
};

export const workflowTheme = { templates, widgets, fields };
