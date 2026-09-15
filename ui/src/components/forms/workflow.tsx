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

import * as React from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { useSearchParams } from "next/navigation";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
} from "@/components/ui/form";
import { useEnvData, useDevices } from "@/hooks";
import { DeviceOption } from "@/types/workflow-form.types";
import { Checkbox } from "@/components/ui/checkbox";
import { WorkflowFormField } from "@/components/forms/formfield";

const deviceWorkflowFormSchema = z.object({
  site: z.string().trim().min(1, { message: "Site is required" }),
  tenant: z.array(z.string()).optional(),
  status: z.array(z.string()).optional(),
  device: z.string().trim().min(1, { message: "Device is required" }),
  commit_confirm: z.boolean().optional().default(true),
});

export type DeviceWorkflowFormSchema = z.infer<typeof deviceWorkflowFormSchema>;

type WorkflowFormProps = {
  title: string;
  onSubmit: (data: DeviceWorkflowFormSchema) => Promise<void>;
  deviceFilterParams?: string[][];
  destructiveWarning?: string;
  /** When true, show the commit-confirm checkbox (for deploy workflow) */
  showCommitConfirm?: boolean;
  /**
   * Restrict the device list to NVCM-managed devices. Defaults to true.
   * Set false for workflows that target unmanaged devices such as the UFM.
   */
  managedOnly?: boolean;
};

type TenantFieldProps = {
  form: ReturnType<typeof useForm<DeviceWorkflowFormSchema>>;
  tenants: { key: string; value: string }[];
  tenantsIsLoading: boolean;
  isSubmitting: boolean;
  onManualChange: () => void;
};

const TenantField = ({
  form,
  tenants,
  tenantsIsLoading,
  isSubmitting,
  onManualChange,
}: TenantFieldProps) => {
  return (
    <WorkflowFormField
      type="select"
      control={form.control}
      name="tenant"
      label="Tenant (optional)"
      options={tenants}
      multiple={true}
      searchable={true}
      isLoading={tenantsIsLoading}
      disabled={isSubmitting}
      handleChange={(_, value) => {
        onManualChange();
        if (Array.isArray(value)) {
          form.setValue("tenant", value);
        } else {
          form.setValue("tenant", value ? [value] : []);
        }
        // Clear device selection when tenant filter changes
        form.setValue("device", "");
      }}
    />
  );
};

type StatusFieldProps = {
  form: ReturnType<typeof useForm<DeviceWorkflowFormSchema>>;
  statuses: { key: string; value: string }[];
  statusesIsLoading: boolean;
  isSubmitting: boolean;
  onManualChange: () => void;
};

const StatusField = ({
  form,
  statuses,
  statusesIsLoading,
  isSubmitting,
  onManualChange,
}: StatusFieldProps) => {
  return (
    <WorkflowFormField
      type="select"
      control={form.control}
      name="status"
      label="Status (optional)"
      options={statuses}
      multiple={true}
      searchable={true}
      isLoading={statusesIsLoading}
      disabled={isSubmitting}
      handleChange={(_, value) => {
        onManualChange();
        if (Array.isArray(value)) {
          form.setValue("status", value);
        } else {
          form.setValue("status", value ? [value] : []);
        }
        // Clear device selection when status filter changes
        form.setValue("device", "");
      }}
    />
  );
};

export const DeviceWorkflowForm = ({
  title,
  onSubmit,
  deviceFilterParams = [],
  destructiveWarning,
  showCommitConfirm = false,
  managedOnly = true,
}: WorkflowFormProps) => {

  const [isSubmitting, setIsSubmitting] = React.useState<boolean>(false);
  const [isManualSiteChange, setIsManualSiteChange] = React.useState(false);
  const [isManualDeviceChange, setIsManualDeviceChange] = React.useState(false);
  const [isManualTenantChange, setIsManualTenantChange] = React.useState(false);
  const [isManualStatusChange, setIsManualStatusChange] = React.useState(false);
  const {
    data: { siteData: sites, tenantsData: tenants, statusData: statuses },
    errors: { siteError, tenantsError, statusesError },
    isLoading: { siteIsLoading, tenantsIsLoading, statusesIsLoading },
  } = useEnvData();

  const searchParams = useSearchParams();
  const querySite = searchParams?.get("site");
  const queryDeviceId = searchParams?.get("device-id");
  const queryTenants = React.useMemo(
    () => searchParams?.getAll("tenant").filter(Boolean) ?? [],
    [searchParams]
  );
  const queryStatuses = React.useMemo(
    () => searchParams?.getAll("status").filter(Boolean) ?? [],
    [searchParams]
  );

  const form = useForm<DeviceWorkflowFormSchema>({
    resolver: zodResolver(deviceWorkflowFormSchema),
    defaultValues: {
      site: querySite || "",
      tenant: queryTenants,
      status: queryStatuses,
      device: queryDeviceId || "",
      commit_confirm: true,
    },
  });

  if (siteError) console.error(`Failed to query devices: ${siteError}`);
  if (tenantsError) console.error(`Failed to query tenants: ${tenantsError}`);
  if (statusesError) console.error(`Failed to query statuses: ${statusesError}`);

  const submitWrapper = async (data: DeviceWorkflowFormSchema) => {
    setIsSubmitting(true);
    try {
      await onSubmit(data);
    } catch (error) {
      console.error(error);
      setIsSubmitting(false);
    }
  };

  const site = form.watch("site");
  const selectedTenants = form.watch("tenant");
  const selectedStatuses = form.watch("status");
  
  // Build filter params: site + selected tenants + selected statuses + any custom params
  const tenantFilters = selectedTenants && selectedTenants.length > 0
    ? selectedTenants.map(tenant => ["tenant", tenant] as [string, string])
    : [];
  const statusFilters = selectedStatuses && selectedStatuses.length > 0
    ? selectedStatuses.map(status => ["status", status] as [string, string])
    : [];
  const filterParams = [
    ["site", site],
    ...(managedOnly ? [["managed_only", "true"]] : []),
    ...tenantFilters,
    ...statusFilters,
    ...deviceFilterParams,
  ];
  
  const {
    devices: deviceData,
    error: deviceError,
    isLoading: deviceIsLoading,
  } = useDevices({ site, filterParams: filterParams });

  if (deviceError) console.error(`Failed to query devices: ${deviceError}`);

  React.useEffect(() => {
    if (querySite && !isManualSiteChange) {
      const isSiteValid = sites.some((option) => option.key === querySite);
      const siteId = sites.find((option) => option.key === querySite)?.value;

      if (isSiteValid) {
        if (siteId && form.getValues("site") !== siteId) {
          form.setValue("site", siteId); // Set valid site from URL
        }
      } else {
        if (form.getValues("site") !== "") {
          form.setValue("site", ""); // Clear site if invalid
        }
        if (form.getValues("device") !== "") {
          form.setValue("device", ""); // Clear device if site is invalid
        }
      }
    }
  }, [querySite, sites, form, isManualSiteChange]);

  React.useEffect(() => {
    if (deviceData && queryDeviceId && !isManualDeviceChange) {
      const isDeviceValid = deviceData.some(
        (item: DeviceOption) => item.value === queryDeviceId
      );

      if (isDeviceValid) {
        if (form.getValues("device") !== queryDeviceId) {
          form.setValue("device", queryDeviceId); // Set valid device from URL
        }
      } else if (form.getValues("device") !== "") {
        form.setValue("device", ""); // Clear device if invalid
      }
    }
  }, [queryDeviceId, deviceData, form, isManualDeviceChange]);

  React.useEffect(() => {
    if (site) {
      form.setValue("device", ""); // Clear device field when site changes
    }
  }, [site, form]);

  React.useEffect(() => {
    setIsManualTenantChange(false);
  }, [queryTenants]);
  React.useEffect(() => {
    setIsManualStatusChange(false);
  }, [queryStatuses]);

  // Pre-select the Tenant filter from the URL, keeping only values that exist
  // in the loaded options so a stale link cannot hide the target device.
  React.useEffect(() => {
    if (!queryTenants.length || !tenants.length || isManualTenantChange) return;
    const valid = queryTenants.filter((t) =>
      tenants.some((option) => option.value === t)
    );
    const current = form.getValues("tenant") ?? [];
    if (valid.join("\u0000") !== current.join("\u0000")) {
      form.setValue("tenant", valid);
    }
  }, [queryTenants, tenants, form, isManualTenantChange]);

  // Pre-select the Status filter from the URL, validated the same way.
  React.useEffect(() => {
    if (!queryStatuses.length || !statuses.length || isManualStatusChange) return;
    const valid = queryStatuses.filter((s) =>
      statuses.some((option) => option.value === s)
    );
    const current = form.getValues("status") ?? [];
    if (valid.join("\u0000") !== current.join("\u0000")) {
      form.setValue("status", valid);
    }
  }, [queryStatuses, statuses, form, isManualStatusChange]);

  const handleSiteChange = (newSite: string | string[]) => {
    setIsManualSiteChange(true);

    // NOTE: It should never be an array since this does not support multiselect, but if it is select just the first value
    if (Array.isArray(newSite)) {
      form.setValue("site", newSite[0]);
    } else {
      form.setValue("site", newSite);
    }
    form.setValue("device", "");
  };

  const handleDeviceChange = (newDevice: string | string[]) => {
    setIsManualDeviceChange(true);

    // NOTE: It should never be an array since this does not support multiselect, but if it is select just the first value
    if (Array.isArray(newDevice)) {
      form.setValue("device", newDevice[0]);
    } else {
      form.setValue("device", newDevice);
    }
  };

  const SiteField = () => {
    return (
      <WorkflowFormField
        type="select"
        control={form.control}
        name="site"
        label="Site"
        options={sites}
        isLoading={siteIsLoading}
        disabled={isSubmitting || deviceIsLoading}
        handleChange={(_, value) => handleSiteChange(value)}
      />
    );
  };



  const DeviceField = () => {
    return (
      <WorkflowFormField
        type="select"
        control={form.control}
        name="device"
        label="Device"
        options={deviceData}
        isLoading={deviceIsLoading || siteIsLoading}
        disabled={isSubmitting || deviceIsLoading}
        handleChange={(_, value) => handleDeviceChange(value)}
      />
    );
  };

  return (
    <div className="flex items-center justify-center p-6">
      <Card className="h-full border-2 shadow-md justify-center">
        <CardHeader>
          <CardTitle>{title}</CardTitle>
        </CardHeader>
        <CardContent>
          <Form {...form}>
            <form
              onSubmit={form.handleSubmit(submitWrapper)}
              className="space-y-6"
            >
              <SiteField />
              <TenantField
                form={form}
                tenants={tenants}
                tenantsIsLoading={tenantsIsLoading}
                isSubmitting={isSubmitting}
                onManualChange={() => setIsManualTenantChange(true)}
              />
              <StatusField
                form={form}
                statuses={statuses}
                statusesIsLoading={statusesIsLoading}
                isSubmitting={isSubmitting}
                onManualChange={() => setIsManualStatusChange(true)}
              />
              <DeviceField />
              {showCommitConfirm && (
                <FormField
                  control={form.control}
                  name="commit_confirm"
                  render={({ field }) => (
                    <FormItem className="flex flex-row items-start space-x-3 space-y-0">
                      <FormControl>
                        <Checkbox
                          checked={field.value}
                          onCheckedChange={field.onChange}
                          disabled={isSubmitting}
                        />
                      </FormControl>
                      <div className="space-y-1 leading-none">
                        <FormLabel>Use commit-confirm</FormLabel>
                        <FormDescription>
                          Rollback if device becomes unreachable after apply.
                          Disable for changes that are expected to interrupt connectivity.
                        </FormDescription>
                      </div>
                    </FormItem>
                  )}
                />
              )}
              {destructiveWarning ? (
                <div
                  role="alert"
                  className="rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive"
                >
                  {destructiveWarning}
                </div>
              ) : null}
              <Button
                type="submit"
                disabled={
                  deviceIsLoading ||
                  siteIsLoading ||
                  tenantsIsLoading ||
                  statusesIsLoading ||
                  isSubmitting
                }
              >
                {isSubmitting ? "Submitting..." : "Submit"}
              </Button>
            </form>
          </Form>
        </CardContent>
      </Card>
    </div>
  );
};
