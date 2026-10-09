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
 * `ui:field: "device"`: a device picker (multiple when the property is an array)
 * narrowed by Site, Tenant, and Status filters.
 *
 * Every device field belongs to a filter scope (`filterScope`: explicit, or
 * `implicit:<property>`). Scope filters live in shell state, never in form data, and
 * are rendered once, by the scope's first device field in effective `ui:order`. With
 * `siteField`, Site is the value of that location property and its `typeField`, and
 * the scope holds only Tenant and Status.
 *
 * The device request is `source.endpoint` + `source.params` + `site`/`site_type` when a
 * site is known + repeated `tenant`/`status`. A required Site that is empty holds it.
 * A filter or site change clears the device unless its own prefill is pending. A
 * pending device prefill waits for the scope (and the site field) to settle, then
 * matches its raw values against the loaded devices once.
 */
import * as React from "react";
import { getUiOptions, type FieldProps } from "@rjsf/utils";

import useOptionSource from "@/hooks/useOptionSource";
import type { ExtraParams, OptionSourceItem } from "@/lib/option-source";

import { contextOf, type ShellFormContext } from "../context";
import {
  corePrefillParams,
  queryValues,
  scopePrefillFilters,
} from "../prefill";
import { EMPTY_SCOPE, type Owner, type ScopeFilters } from "../state";
import type { DeviceFilter, DeviceOptions } from "../ui-schema";
import {
  fieldDescription,
  fieldLabel,
  isEmptyValue,
  isLoading,
  matchOptions,
  Picker,
  useSignatureChange,
} from "./shared";

const NO_VALUES = {};

const siteKey = (item: OptionSourceItem): string =>
  JSON.stringify([
    String(item.value),
    item.type == null ? null : String(item.type),
  ]);

interface ScopeControlsProps {
  scope: string;
  filters: readonly DeviceFilter[];
  filterSources: DeviceOptions["filterSources"];
  /** Whether this scope renders its own Site control (no `siteField`). */
  ownSite: boolean;
  siteRequired: boolean;
  context: ShellFormContext;
  disabled: boolean;
  idPrefix: string;
}

/**
 * The scope's filter controls and its prefill: one terminal `setFilters(..., "prefill")`
 * (or `settle`) after every requested filter has matched, missed, or failed to load.
 */
const ScopeControls = ({
  scope,
  filters,
  filterSources,
  ownSite,
  siteRequired,
  context,
  disabled,
  idPrefix,
}: ScopeControlsProps) => {
  const { query, pending, setFilters, settle } = context;
  const current = context.filters[scope] ?? EMPTY_SCOPE;
  const owner: Owner = `scope:${scope}`;
  const scopePending = pending.has(owner);
  const showTenant = filters.includes("tenant");
  const showStatus = filters.includes("status");

  const sites = useOptionSource(
    ownSite ? filterSources.site : undefined,
    NO_VALUES
  );
  const tenants = useOptionSource(
    showTenant ? filterSources.tenant : undefined,
    NO_VALUES
  );
  const statuses = useOptionSource(
    showStatus ? filterSources.status : undefined,
    NO_VALUES
  );

  React.useEffect(() => {
    if (!scopePending) return;
    const raw = (filter: DeviceFilter) => queryValues(query, [filter]);
    const requested = (filter: DeviceFilter, shown: boolean) =>
      shown && raw(filter).length > 0;
    const wantSite = requested("site", ownSite);
    const wantTenant = requested("tenant", showTenant);
    const wantStatus = requested("status", showStatus);
    if (
      (wantSite && isLoading(sites)) ||
      (wantTenant && isLoading(tenants)) ||
      (wantStatus && isLoading(statuses))
    ) {
      return;
    }
    const patch: Partial<ScopeFilters> = {};
    if (wantSite) {
      const [site] = matchOptions(raw("site"), sites.options, false);
      if (site)
        patch.site = {
          id: String(site.value),
          type: site.type == null ? "" : String(site.type),
        };
    }
    if (wantTenant) {
      const matched = matchOptions(raw("tenant"), tenants.options, true);
      if (matched.length > 0)
        patch.tenant = matched.map((item) => String(item.value));
    }
    if (wantStatus) {
      const matched = matchOptions(raw("status"), statuses.options, true);
      if (matched.length > 0)
        patch.status = matched.map((item) => String(item.value));
    }
    if (Object.keys(patch).length > 0) setFilters(scope, patch, "prefill");
    else settle(owner);
  }, [
    scopePending,
    query,
    ownSite,
    showTenant,
    showStatus,
    sites,
    tenants,
    statuses,
    scope,
    owner,
    setFilters,
    settle,
  ]);

  const locked = disabled || scopePending;
  return (
    <>
      {ownSite ? (
        <Picker
          id={`${idPrefix}__site`}
          label="Site"
          required={siteRequired}
          options={sites.options.map((item) => ({
            key: item.label,
            value: siteKey(item),
          }))}
          value={
            current.site
              ? JSON.stringify([current.site.id, current.site.type || null])
              : ""
          }
          multiple={false}
          disabled={locked || isLoading(sites)}
          busy={sites.status === "loading"}
          error={
            sites.status === "error"
              ? "Could not load Site options."
              : undefined
          }
          onChange={([key]) => {
            const item = sites.options.find(
              (option) => siteKey(option) === key
            );
            setFilters(
              scope,
              {
                site: item
                  ? {
                      id: String(item.value),
                      type: item.type == null ? "" : String(item.type),
                    }
                  : undefined,
              },
              "user"
            );
          }}
        />
      ) : null}
      {showTenant ? (
        <Picker
          id={`${idPrefix}__tenant`}
          label="Tenant (optional)"
          options={tenants.options.map((item) => ({
            key: item.label,
            value: String(item.value),
          }))}
          value={[...current.tenant]}
          multiple
          disabled={locked || isLoading(tenants)}
          busy={tenants.status === "loading"}
          error={
            tenants.status === "error"
              ? "Could not load Tenant options."
              : undefined
          }
          onChange={(keys) => setFilters(scope, { tenant: keys }, "user")}
        />
      ) : null}
      {showStatus ? (
        <Picker
          id={`${idPrefix}__status`}
          label="Status (optional)"
          options={statuses.options.map((item) => ({
            key: item.label,
            value: String(item.value),
          }))}
          value={[...current.status]}
          multiple
          disabled={locked || isLoading(statuses)}
          busy={statuses.status === "loading"}
          error={
            statuses.status === "error"
              ? "Could not load Status options."
              : undefined
          }
          onChange={(keys) => setFilters(scope, { status: keys }, "user")}
        />
      ) : null}
    </>
  );
};

export const DeviceField = (props: FieldProps) => {
  const {
    name,
    schema,
    uiSchema,
    disabled,
    readonly,
    registry,
    required,
    rawErrors,
    fieldPathId,
  } = props;
  const context = contextOf(registry.formContext);
  const { formData, pending, layout, query, setFields, settle } = context;
  const options = getUiOptions(uiSchema) as unknown as DeviceOptions;
  const {
    source,
    filters,
    filterSources,
    siteRequired,
    siteField,
    filterScope,
  } = options;
  const owner: Owner = `field:${name}`;
  const multiple = schema.type === "array";
  const label = fieldLabel(props);
  const prefillParams = React.useMemo(
    () =>
      corePrefillParams(
        "device",
        name,
        options as unknown as Record<string, unknown>
      ),
    // `options` is rebuilt every render; its inputs are the field's ui_schema and name.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [uiSchema, name]
  );

  const scope = context.filters[filterScope] ?? EMPTY_SCOPE;
  const usesSite = filters.includes("site");
  let site: { id: string; type?: string } | undefined;
  if (usesSite && siteField !== undefined) {
    const id = formData[siteField];
    const typeField = layout.typeFields[siteField];
    const type = typeField !== undefined ? formData[typeField] : undefined;
    if (typeof id === "string" && id !== "") {
      site = {
        id,
        type: typeof type === "string" && type !== "" ? type : undefined,
      };
    }
  } else if (usesSite && scope.site) {
    site = { id: scope.site.id, type: scope.site.type || undefined };
  }
  const tenant = filters.includes("tenant") ? scope.tenant : [];
  const status = filters.includes("status") ? scope.status : [];
  const siteMissing = usesSite && siteRequired && site === undefined;

  const extra = React.useMemo<ExtraParams>(() => {
    const params: Record<string, readonly string[]> = { tenant, status };
    if (site) {
      params.site = [site.id];
      if (site.type) params.site_type = [site.type];
    }
    return params;
  }, [site?.id, site?.type, tenant, status]); // eslint-disable-line react-hooks/exhaustive-deps

  const devices = useOptionSource(
    siteMissing ? undefined : source,
    formData,
    extra
  );

  const ownPending = pending.has(owner);
  const siteOwner =
    siteField !== undefined ? layout.owners[siteField] : undefined;
  const dependencyPending =
    pending.has(`scope:${filterScope}`) ||
    (siteOwner !== undefined && pending.has(siteOwner));

  React.useEffect(() => {
    if (!ownPending || dependencyPending) return;
    if (!siteMissing && isLoading(devices)) return;
    const matched =
      !siteMissing && devices.status === "success"
        ? matchOptions(
            queryValues(query, prefillParams),
            devices.options,
            multiple
          )
        : [];
    if (matched.length === 0) {
      settle(owner);
      return;
    }
    const values = matched.map((item) => String(item.value));
    setFields(owner, { [name]: multiple ? values : values[0] }, "prefill");
  }, [
    ownPending,
    dependencyPending,
    siteMissing,
    devices,
    query,
    prefillParams,
    multiple,
    owner,
    name,
    setFields,
    settle,
  ]);

  const value = formData[name];
  useSignatureChange(JSON.stringify([site ?? null, tenant, status]), () => {
    if (ownPending || isEmptyValue(value)) return;
    setFields(owner, { [name]: undefined }, "user");
  });

  const locked = Boolean(disabled || readonly);
  const selected = multiple
    ? (Array.isArray(value) ? value : []).map(String)
    : isEmptyValue(value)
    ? ""
    : String(value);

  return (
    <div className="space-y-6">
      {layout.scopeHosts[filterScope] === name ? (
        <ScopeControls
          scope={filterScope}
          filters={scopePrefillFilters(options)}
          filterSources={filterSources}
          ownSite={usesSite && siteField === undefined}
          siteRequired={siteRequired}
          context={context}
          disabled={locked}
          idPrefix={fieldPathId.$id}
        />
      ) : null}
      <Picker
        id={fieldPathId.$id}
        label={label}
        required={required}
        description={fieldDescription(props)}
        options={devices.options.map((item) => ({
          key: item.label,
          value: String(item.value),
        }))}
        value={selected}
        multiple={multiple}
        disabled={
          locked ||
          ownPending ||
          dependencyPending ||
          siteMissing ||
          isLoading(devices)
        }
        busy={devices.status === "loading" || ownPending}
        invalid={Boolean(rawErrors?.length)}
        error={
          devices.status === "error"
            ? `Could not load ${label} options.`
            : undefined
        }
        placeholder={siteMissing ? "Select a Site first" : undefined}
        onChange={(keys) =>
          setFields(
            owner,
            {
              [name]: multiple ? (keys.length > 0 ? keys : undefined) : keys[0],
            },
            "user"
          )
        }
      />
    </div>
  );
};
