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
  VariantRowsFieldOptions,
  VariantRowsMode,
} from "../ui-schema";

type Row = Record<string, string>;

const columnId = (column: VariantRowsColumn): string =>
  JSON.stringify([column.arrayProperty, column.itemProperty ?? null]);

const propertiesOfMode = (mode: VariantRowsMode): string[] => [
  ...new Set(mode.columns.map((column) => column.arrayProperty)),
];

const textValue = (value: unknown): string =>
  value === undefined || value === null ? "" : String(value);

const arrayValue = (data: Readonly<FormData>, property: string): unknown[] => {
  const value = data[property];
  return Array.isArray(value) ? value : [];
};

const rowsFromData = (
  mode: VariantRowsMode,
  data: Readonly<FormData>,
  minimumRows: number
): Row[] => {
  const count = Math.max(
    minimumRows,
    1,
    ...mode.columns.map((column) => arrayValue(data, column.arrayProperty).length)
  );
  return Array.from({ length: count }, (_, index) =>
    Object.fromEntries(
      mode.columns.map((column) => {
        const items = arrayValue(data, column.arrayProperty);
        const item = items[index];
        const value =
          column.itemProperty === undefined || typeof item !== "object" || item === null
            ? column.itemProperty === undefined
              ? item
              : undefined
            : (item as Record<string, unknown>)[column.itemProperty];
        return [columnId(column), textValue(value)];
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

export const activeMode = (
  config: VariantRowsFieldOptions,
  data: Readonly<FormData>
): VariantRowsMode => {
  const populated = config.modes.find((mode) =>
    propertiesOfMode(mode).some((property) => hasFilledProperty(data, property))
  );
  if (populated) return populated;

  // A blank row still identifies the mode the user selected. Inactive modes are
  // removed by patchForRows, so prefer any mode that retains a row before falling
  // back to the declaration's default.
  return (
    config.modes.find((mode) =>
      propertiesOfMode(mode).some((property) => arrayValue(data, property).length > 0)
    ) ?? config.modes[0]
  );
};

const patchForRows = (
  config: VariantRowsFieldOptions,
  mode: VariantRowsMode,
  rows: readonly Row[]
): FormData => {
  const patch: FormData = config.clearInactive
    ? Object.fromEntries(config.ownedProperties.map((property) => [property, undefined]))
    : {};
  for (const property of propertiesOfMode(mode)) {
    const columns = mode.columns.filter((column) => column.arrayProperty === property);
    const keyed = columns.filter((column) => column.itemProperty !== undefined);
    patch[property] =
      keyed.length > 0
        ? rows.map((row) =>
            Object.fromEntries(
              keyed.map((column) => [column.itemProperty as string, row[columnId(column)] ?? ""])
            )
          )
        : rows.map((row) => row[columnId(columns[0])] ?? "");
  }
  return patch;
};

const rowHasValue = (row: Readonly<Row>): boolean =>
  Object.values(row).some((value) => value.trim() !== "");

/** Client-side messages for the active generic row mode. */
export const validateVariantRowsValues = (
  config: VariantRowsFieldOptions,
  data: Readonly<FormData>
): string[] => {
  const mode = activeMode(config, data);
  const rows = rowsFromData(mode, data, 0).filter(rowHasValue);
  const messages: string[] = [];
  const minimumRows = config.minimumRows ?? 1;
  if (rows.length < minimumRows) {
    messages.push(`At least ${minimumRows} row${minimumRows === 1 ? " is" : "s are"} required.`);
  }
  rows.forEach((row, index) => {
    mode.columns.forEach((column) => {
      const value = row[columnId(column)]?.trim() ?? "";
      if (column.required && value === "") {
        messages.push(`${column.label} is required in row ${index + 1}.`);
      } else if (value !== "" && column.pattern && !new RegExp(column.pattern).test(value)) {
        messages.push(`${column.label} in row ${index + 1} has an invalid format.`);
      }
    });
  });
  return messages;
};

/** Trim and remove blank rows while retaining aligned parallel-array values. */
export const normalizeVariantRowsPayload = (
  config: VariantRowsFieldOptions,
  data: Readonly<FormData>
): FormData => {
  const mode = activeMode(config, data);
  const rows = rowsFromData(mode, data, 0)
    .map((row) => Object.fromEntries(Object.entries(row).map(([key, value]) => [key, value.trim()])))
    .filter(rowHasValue);
  const patch = patchForRows(config, mode, rows);
  const result: FormData = { ...data };
  for (const [property, value] of Object.entries(patch)) {
    if (value === undefined) delete result[property];
    else result[property] = value;
  }
  return result;
};

const RowInput = ({
  column,
  value,
  rowNumber,
  disabled,
  onChange,
}: {
  column: VariantRowsColumn;
  value: string;
  rowNumber: number;
  disabled: boolean;
  onChange(value: string): void;
}) =>
  column.kind === "select" ? (
    <Select value={value || undefined} disabled={disabled} onValueChange={onChange}>
      <SelectTrigger aria-label={`${column.label} for row ${rowNumber}`}>
        <SelectValue placeholder={column.placeholder ?? `Select ${column.label}...`} />
      </SelectTrigger>
      <SelectContent>
        {(column.choices ?? []).map((choice) => (
          <SelectItem key={choice.value} value={choice.value}>
            {choice.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  ) : (
    <Input
      value={value}
      placeholder={column.placeholder}
      aria-label={`${column.label} ${rowNumber}`}
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
  const config = getUiOptions(uiSchema) as unknown as VariantRowsFieldOptions;
  const owner: Owner = `field:${name}`;
  const minimumRows = config.minimumRows ?? 1;
  const [modeId, setModeId] = React.useState(
    () => activeMode(config, context.formData).id
  );
  const [rowsByMode, setRowsByMode] = React.useState<Record<string, Row[]>>(() =>
    Object.fromEntries(
      config.modes.map((mode) => [
        mode.id,
        rowsFromData(mode, context.formData, minimumRows),
      ])
    )
  );
  const isDisabled = Boolean(disabled || readonly);
  const initialized = React.useRef(false);
  const mode = config.modes.find((candidate) => candidate.id === modeId) ?? config.modes[0];
  const rows = rowsByMode[mode.id] ?? rowsFromData(mode, context.formData, minimumRows);

  const publish = React.useCallback(
    (nextMode: VariantRowsMode, nextRows: readonly Row[]) =>
      context.setFields(owner, patchForRows(config, nextMode, nextRows), "user"),
    [config, context, owner]
  );

  React.useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;
    publish(mode, rows);
    if (context.pending.has(owner)) context.settle(owner);
  }, [context, mode, owner, publish, rows]);

  const changeRows = (next: Row[]) => {
    setRowsByMode((current) => ({ ...current, [mode.id]: next }));
    publish(mode, next);
  };
  const changeMode = (id: string) => {
    const nextMode = config.modes.find((candidate) => candidate.id === id);
    if (!nextMode) return;
    const nextRows = rowsByMode[id] ?? rowsFromData(nextMode, context.formData, minimumRows);
    setModeId(id);
    setRowsByMode((current) => ({ ...current, [id]: nextRows }));
    publish(nextMode, nextRows);
  };

  return (
    <div className="space-y-6">
      <div className="space-y-3">
        <Label>Input method</Label>
        <RadioGroup
          value={mode.id}
          onValueChange={changeMode}
          className="flex flex-row gap-6"
          disabled={isDisabled}
        >
          {config.modes.map((candidate) => (
            <label key={candidate.id} className="flex cursor-pointer items-center gap-2">
              <RadioGroupItem value={candidate.id} />
              <span>{candidate.label}</span>
            </label>
          ))}
        </RadioGroup>
      </div>

      <div className="space-y-2">
        <Label>{mode.label}</Label>
        {rows.map((row, index) => (
          <div key={index} className="flex items-start gap-2">
            {mode.columns.map((column) => (
              <div key={columnId(column)} className="min-w-0 flex-1">
                <RowInput
                  column={column}
                  value={row[columnId(column)] ?? ""}
                  rowNumber={index + 1}
                  disabled={isDisabled}
                  onChange={(value) =>
                    changeRows(
                      rows.map((item, rowIndex) =>
                        rowIndex === index ? { ...item, [columnId(column)]: value } : item
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
              Object.fromEntries(mode.columns.map((column) => [columnId(column), ""])),
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
