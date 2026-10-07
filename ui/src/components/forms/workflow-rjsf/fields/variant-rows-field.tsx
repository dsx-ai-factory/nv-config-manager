"use client";
/*
 * SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as React from "react";
import { getUiOptions, type FieldProps } from "@rjsf/utils";
import { Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

import { contextOf } from "../context";
import type { FormData, Owner } from "../state";
import type {
  VariantRowsColumn,
  VariantRowsOptions,
  VariantRowsVariant,
} from "../ui-schema";

type Row = Record<string, string>;

const columnId = (field: VariantRowsColumn): string =>
  JSON.stringify([field.property, field.key ?? null]);

const propertiesOfVariant = (variant: VariantRowsVariant): string[] => [
  ...new Set(variant.fields.map((field) => field.property)),
];

const textValue = (value: unknown): string =>
  value === undefined || value === null ? "" : String(value);

const arrayValue = (data: Readonly<FormData>, property: string): unknown[] => {
  const value = data[property];
  return Array.isArray(value) ? value : [];
};

const rowsFromData = (
  variant: VariantRowsVariant,
  data: Readonly<FormData>,
  minimumRows: number
): Row[] => {
  const count = Math.max(
    minimumRows,
    1,
    ...variant.fields.map((field) => arrayValue(data, field.property).length)
  );
  return Array.from({ length: count }, (_, index) =>
    Object.fromEntries(
      variant.fields.map((field) => {
        const items = arrayValue(data, field.property);
        const item = items[index];
        const value =
          field.key === undefined || typeof item !== "object" || item === null
            ? field.key === undefined
              ? item
              : undefined
            : (item as Record<string, unknown>)[field.key];
        return [columnId(field), textValue(value)];
      })
    )
  );
};

const hasFilledProperty = (data: Readonly<FormData>, property: string): boolean =>
  Array.isArray(data[property]) &&
  data[property].some((item) => {
    if (typeof item === "object" && item !== null) {
      return Object.values(item).some((value) => textValue(value).trim() !== "");
    }
    return textValue(item).trim() !== "";
  });

export const activeVariant = (
  config: VariantRowsOptions,
  data: Readonly<FormData>
): VariantRowsVariant => {
  const populated = config.variants.find((variant) =>
    propertiesOfVariant(variant).some((property) => hasFilledProperty(data, property))
  );
  if (populated) return populated;

  // A blank row still identifies the mode the user selected. Inactive variants are
  // removed by patchForRows, so prefer any variant that retains a row before falling
  // back to the declaration's default.
  return (
    config.variants.find((variant) =>
      propertiesOfVariant(variant).some((property) => arrayValue(data, property).length > 0)
    ) ?? config.variants[0]
  );
};

const patchForRows = (
  config: VariantRowsOptions,
  variant: VariantRowsVariant,
  rows: readonly Row[]
): FormData => {
  const patch: FormData = config.clearInactive
    ? Object.fromEntries(config.owns.map((property) => [property, undefined]))
    : {};
  for (const property of propertiesOfVariant(variant)) {
    const fields = variant.fields.filter((field) => field.property === property);
    const keyed = fields.filter((field) => field.key !== undefined);
    patch[property] =
      keyed.length > 0
        ? rows.map((row) =>
            Object.fromEntries(
              keyed.map((field) => [field.key as string, row[columnId(field)] ?? ""])
            )
          )
        : rows.map((row) => row[columnId(fields[0])] ?? "");
  }
  return patch;
};

const rowHasValue = (row: Readonly<Row>): boolean =>
  Object.values(row).some((value) => value.trim() !== "");

/** Client-side messages for the active generic row variant. */
export const validateVariantRowsValues = (
  config: VariantRowsOptions,
  data: Readonly<FormData>
): string[] => {
  const variant = activeVariant(config, data);
  const rows = rowsFromData(variant, data, 0).filter(rowHasValue);
  const messages: string[] = [];
  const minimumRows = config.minimumRows ?? 1;
  if (rows.length < minimumRows) {
    messages.push(`At least ${minimumRows} row${minimumRows === 1 ? " is" : "s are"} required.`);
  }
  rows.forEach((row, index) => {
    variant.fields.forEach((field) => {
      const value = row[columnId(field)]?.trim() ?? "";
      if (field.required && value === "") {
        messages.push(`${field.label} is required in row ${index + 1}.`);
      } else if (value !== "" && field.pattern && !new RegExp(field.pattern).test(value)) {
        messages.push(`${field.label} in row ${index + 1} has an invalid format.`);
      }
    });
  });
  return messages;
};

/** Trim and remove blank rows while retaining aligned parallel-array values. */
export const normalizeVariantRowsPayload = (
  config: VariantRowsOptions,
  data: Readonly<FormData>
): FormData => {
  const variant = activeVariant(config, data);
  const rows = rowsFromData(variant, data, 0)
    .map((row) => Object.fromEntries(Object.entries(row).map(([key, value]) => [key, value.trim()])))
    .filter(rowHasValue);
  const patch = patchForRows(config, variant, rows);
  const result: FormData = { ...data };
  for (const [property, value] of Object.entries(patch)) {
    if (value === undefined) delete result[property];
    else result[property] = value;
  }
  return result;
};

const RowInput = ({
  field,
  value,
  rowNumber,
  disabled,
  onChange,
}: {
  field: VariantRowsColumn;
  value: string;
  rowNumber: number;
  disabled: boolean;
  onChange(value: string): void;
}) =>
  field.kind === "select" ? (
    <Select value={value || undefined} disabled={disabled} onValueChange={onChange}>
      <SelectTrigger aria-label={`${field.label} for row ${rowNumber}`}>
        <SelectValue placeholder={field.placeholder ?? `Select ${field.label}...`} />
      </SelectTrigger>
      <SelectContent>
        {(field.options ?? []).map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  ) : (
    <Input
      value={value}
      placeholder={field.placeholder}
      aria-label={`${field.label} ${rowNumber}`}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
    />
  );

/** Mutually exclusive repeatable rows configured entirely by the Python declaration. */
export const VariantRowsField = ({
  name,
  uiSchema,
  registry,
  disabled,
  readonly,
}: FieldProps) => {
  const context = contextOf(registry.formContext);
  const config = getUiOptions(uiSchema) as unknown as VariantRowsOptions;
  const owner: Owner = `field:${name}`;
  const minimumRows = config.minimumRows ?? 1;
  const [variantId, setVariantId] = React.useState(
    () => activeVariant(config, context.formData).id
  );
  const [rowsByVariant, setRowsByVariant] = React.useState<Record<string, Row[]>>(() =>
    Object.fromEntries(
      config.variants.map((variant) => [
        variant.id,
        rowsFromData(variant, context.formData, minimumRows),
      ])
    )
  );
  const isDisabled = Boolean(disabled || readonly);
  const initialized = React.useRef(false);
  const variant = config.variants.find((candidate) => candidate.id === variantId) ?? config.variants[0];
  const rows = rowsByVariant[variant.id] ?? rowsFromData(variant, context.formData, minimumRows);

  const publish = React.useCallback(
    (nextVariant: VariantRowsVariant, nextRows: readonly Row[]) =>
      context.setFields(owner, patchForRows(config, nextVariant, nextRows), "user"),
    [config, context, owner]
  );

  React.useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;
    publish(variant, rows);
    if (context.pending.has(owner)) context.settle(owner);
  }, [context, owner, publish, rows, variant]);

  const changeRows = (next: Row[]) => {
    setRowsByVariant((current) => ({ ...current, [variant.id]: next }));
    publish(variant, next);
  };
  const changeVariant = (id: string) => {
    const nextVariant = config.variants.find((candidate) => candidate.id === id);
    if (!nextVariant) return;
    const nextRows = rowsByVariant[id] ?? rowsFromData(nextVariant, context.formData, minimumRows);
    setVariantId(id);
    setRowsByVariant((current) => ({ ...current, [id]: nextRows }));
    publish(nextVariant, nextRows);
  };

  return (
    <div className="space-y-6">
      <div className="space-y-3">
        <Label>Input method</Label>
        <RadioGroup
          value={variant.id}
          onValueChange={changeVariant}
          className="flex flex-row gap-6"
          disabled={isDisabled}
        >
          {config.variants.map((candidate) => (
            <label key={candidate.id} className="flex cursor-pointer items-center gap-2">
              <RadioGroupItem value={candidate.id} />
              <span>{candidate.label}</span>
            </label>
          ))}
        </RadioGroup>
      </div>

      <div className="space-y-2">
        <Label>{variant.label}</Label>
        {rows.map((row, index) => (
          <div key={index} className="flex items-start gap-2">
            {variant.fields.map((field) => (
              <div key={columnId(field)} className="min-w-0 flex-1">
                <RowInput
                  field={field}
                  value={row[columnId(field)] ?? ""}
                  rowNumber={index + 1}
                  disabled={isDisabled}
                  onChange={(value) =>
                    changeRows(
                      rows.map((item, rowIndex) =>
                        rowIndex === index ? { ...item, [columnId(field)]: value } : item
                      )
                    )
                  }
                />
              </div>
            ))}
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={`Remove row ${index + 1}`}
              disabled={isDisabled || rows.length <= minimumRows}
              onClick={() => changeRows(rows.filter((_, rowIndex) => rowIndex !== index))}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        ))}
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={isDisabled}
          onClick={() =>
            changeRows([
              ...rows,
              Object.fromEntries(variant.fields.map((field) => [columnId(field), ""])),
            ])
          }
        >
          <Plus className="mr-1 h-4 w-4" />
          Add Row
        </Button>
      </div>

      {config.warning ? (
        <div
          role="alert"
          className="rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive"
        >
          {config.warning}
        </div>
      ) : null}
    </div>
  );
};
