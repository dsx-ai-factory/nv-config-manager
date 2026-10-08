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
import { useSearchParams } from "next/navigation";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm, type UseFormReturn } from "react-hook-form";
import { z } from "zod";
import useSWR from "swr";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Form } from "@/components/ui/form";
import { useToast } from "@/components/ui/use-toast";
import { SitePasswordRotationWorkflowInput } from "@/types/data-table.types";
import { useEnvData, useDevices } from "@/hooks";
import { startWorkflow, sanitizeUrl } from "@/lib/utils";
import { DEFAULT_SITE_WORKFLOW_STATUSES } from "@/lib/workflow-defaults";
import { useRuntimeConfig } from "@/config/runtime";
import { WorkflowFormField } from "@/components/forms/formfield";
import { fetcher } from "@/lib/fetcher";
import {
  resolveLocationFormValue,
  resolveLocationOption,
} from "@/lib/location-options";
import type {
  DeviceOption,
  LocationOption,
  Option,
} from "@/types/workflow-form.types";

const SitePasswordRotationWorkflowFormSchema = z.object({
  location: z.string().trim().min(1, { message: "Location is required" }),
  selected_secret: z.string().trim().min(1, { message: "Secret is required" }),
  roles: z.union([z.string(), z.array(z.string())])
    .optional()
    .transform((val: string | string[] | undefined) => (Array.isArray(val) ? val : []))
    .pipe(z.array(z.string())),
  status: z.union([z.string(), z.array(z.string())])
    .optional()
    .transform((val: string | string[] | undefined) => (Array.isArray(val) ? val : []))
    .pipe(z.array(z.string()).min(1, { message: "Device Status is required" })),
  tenant: z.string().trim().optional().default(""),
});

type SitePasswordRotationFormData = z.infer<
  typeof SitePasswordRotationWorkflowFormSchema
>;
type QueryOption = { key: string; value: string };
type SingleValueField = "location" | "tenant";
type MultiValueField = "roles" | "status";
type PasswordUser = { name: string; description: string };

const getOptionValue = (
  queryValue: string | null,
  options: QueryOption[]
) => {
  if (!queryValue) {
    return null;
  }

  for (const option of options) {
    if (option.key === queryValue) {
      return option.value;
    }
  }

  return null;
};

const getValidQueryValues = (
  queryValues: string[],
  options: QueryOption[]
) => {
  const optionKeys = new Set<string>();
  for (const option of options) {
    optionKeys.add(option.key);
  }

  const validValues: string[] = [];
  for (const value of queryValues) {
    if (optionKeys.has(value)) {
      validValues.push(value);
    }
  }
  return validValues;
};

const setSingleQueryValue = (
  form: UseFormReturn<SitePasswordRotationFormData>,
  queryValue: string | null,
  data: QueryOption[],
  fieldName: SingleValueField
) => {
  const validValue = getOptionValue(queryValue, data);
  if (validValue) {
    form.setValue(fieldName, validValue);
  }
};

const setMultiQueryValue = (
  form: UseFormReturn<SitePasswordRotationFormData>,
  queryValues: string[],
  data: QueryOption[],
  fieldName: MultiValueField
) => {
  if (queryValues.length === 0) {
    return;
  }

  const validValues = getValidQueryValues(queryValues, data);
  if (validValues.length > 0) {
    form.setValue(fieldName, validValues);
  }
};

const syncQueryValues = (
  form: UseFormReturn<SitePasswordRotationFormData>,
  queryLocation: string | null,
  queryRoles: string[],
  queryStatuses: string[],
  queryTenant: string | null,
  envData: {
    siteData: LocationOption[];
    rolesData: QueryOption[];
    statusData: QueryOption[];
    tenantsData: QueryOption[];
  }
) => {
  const locationValue = resolveLocationFormValue(envData.siteData, queryLocation);
  if (locationValue) form.setValue("location", locationValue);
  setMultiQueryValue(form, queryRoles, envData.rolesData, "roles");
  setMultiQueryValue(form, queryStatuses, envData.statusData, "status");
  setSingleQueryValue(form, queryTenant, envData.tenantsData, "tenant");
};

const addFilterValues = (
  params: string[][],
  filterName: string,
  values: string[] = []
) => {
  for (const value of values) {
    params.push([filterName, value]);
  }
};

const buildDeviceFilterParams = (
  location: string,
  roles: string[],
  status: string[],
  tenant: string
) => {
  const params: string[][] = [["managed_only", "true"]];
  if (location) {
    params.push(["site", location]);
  }
  addFilterValues(params, "role", roles);
  addFilterValues(params, "status", status);
  if (tenant) {
    params.push(["tenant", tenant]);
  }
  return params;
};

const getFirstDeviceId = (
  devices: DeviceOption[],
  devicesLoading: boolean
): string | null => {
  if (devicesLoading || devices.length === 0) {
    return null;
  }
  return devices[0].value;
};

const getPasswordUsersUrl = (
  workflowApiUrl: string | undefined,
  firstDeviceId: string | null,
  devicesLoading: boolean
): string | null => {
  if (!workflowApiUrl || !firstDeviceId || devicesLoading) {
    return null;
  }
  return sanitizeUrl(
    `${workflowApiUrl}/v1/parameter/device/${firstDeviceId}/password_users`
  );
};

const mapSecretOptions = (passwordUsers: PasswordUser[] | undefined): Option[] => {
  if (!passwordUsers) {
    return [];
  }

  const secretOptions: Option[] = [];
  for (const user of passwordUsers) {
    secretOptions.push({
      value: user.name,
      key: user.name
    });
  }
  return secretOptions;
};

const getSecretLabel = (
  location: string,
  devicesLoading: boolean,
  firstDeviceId: string | null,
  secretsLoading: boolean,
  secretCount: number
) => {
  if (!location) {
    return "Secret to Rotate (select location first)";
  }
  if (devicesLoading) {
    return "Secret to Rotate (loading devices...)";
  }
  if (!firstDeviceId) {
    return "Secret to Rotate (no matching devices)";
  }
  if (secretsLoading) {
    return "Secret to Rotate (loading secrets...)";
  }
  if (secretCount === 0) {
    return "Secret to Rotate (no secrets found)";
  }
  return "Secret to Rotate";
};

const DeviceMatchStatus = ({
  location,
  devicesLoading,
  deviceCount,
}: {
  location: string;
  devicesLoading: boolean;
  deviceCount: number;
}) => {
  if (!location) {
    return null;
  }

  if (devicesLoading) {
    return (
      <div className="text-sm text-muted-foreground mb-4">
        <div className="flex items-center gap-2">
          <div className="animate-spin h-4 w-4 border-2 border-primary border-t-transparent rounded-full"></div>
          Loading devices...
        </div>
      </div>
    );
  }

  if (deviceCount > 0) {
    return (
      <div className="text-sm text-muted-foreground mb-4">
        <div className="text-green-600">
          ✓ Found {deviceCount} matching device{deviceCount === 1 ? "" : "s"}
        </div>
      </div>
    );
  }

  return (
    <div className="text-sm text-muted-foreground mb-4">
      <div className="text-amber-600">
        ⚠ No devices found matching the selected criteria
      </div>
    </div>
  );
};

export const SitePasswordRotationWorkflowForm = () => {
  const [isSubmitting, setIsSubmitting] = React.useState<boolean>(false);
  const [isManualChange, setIsManualChange] = React.useState<boolean>(false);
  const { config: runtimeConfig } = useRuntimeConfig();
  const { data: envData } = useEnvData();
  const { toast } = useToast();
  const searchParams = useSearchParams();
  
  // Get query parameters
  const queryLocation = searchParams?.get("location");
  const querySecret = searchParams?.get("selected_secret");
  const queryRoles = React.useMemo(() => searchParams?.getAll("role") ?? [], [searchParams]);
  const queryStatuses = React.useMemo(() => searchParams?.getAll("status") ?? [], [searchParams]);
  const queryTenant = searchParams?.get("tenant");

  const form = useForm<SitePasswordRotationFormData>({
    resolver: zodResolver(SitePasswordRotationWorkflowFormSchema),
    defaultValues: {
      location: queryLocation || "",
      selected_secret: querySecret || "",
      roles: queryRoles,
      status: queryStatuses.length > 0 ? queryStatuses : [...DEFAULT_SITE_WORKFLOW_STATUSES],
      tenant: queryTenant || "",
    },
  });

  // Get form values for filtering
  const location = form.watch("location");
  const roles = form.watch("roles");
  const status = form.watch("status");
  const tenant = form.watch("tenant");

  const filterParams = React.useMemo(() => {
    return buildDeviceFilterParams(location, roles, status, tenant);
  }, [location, roles, status, tenant]);

  const { devices, isLoading: devicesLoading } = useDevices({
    site: location,
    filterParams
  });

  const firstDeviceId = getFirstDeviceId(devices, devicesLoading);
  const passwordUsersUrl = getPasswordUsersUrl(
    runtimeConfig?.workflowApiUrl,
    firstDeviceId,
    devicesLoading
  );

  const { data: passwordUsers, isLoading: secretsLoading } =
    useSWR<PasswordUser[]>(passwordUsersUrl, fetcher);

  const secretOptions = React.useMemo(() => {
    return mapSecretOptions(passwordUsers);
  }, [passwordUsers]);
  const secretLabel = getSecretLabel(
    location,
    devicesLoading,
    firstDeviceId,
    secretsLoading,
    secretOptions.length
  );

  React.useEffect(() => {
    form.setValue("selected_secret", "");
  }, [location, roles, status, tenant, form]);

  React.useEffect(() => {
    if (!isManualChange) {
      syncQueryValues(
        form,
        queryLocation,
        queryRoles,
        queryStatuses,
        queryTenant,
        envData
      );
    }
  }, [
    queryLocation,
    queryRoles,
    queryStatuses,
    queryTenant,
    envData,
    form,
    isManualChange,
  ]);

  const onSubmit = async (data: SitePasswordRotationFormData) => {
    setIsSubmitting(true);
    const location = resolveLocationOption(envData.siteData, data.location);

    const workflowParams: SitePasswordRotationWorkflowInput = {
      location: location?.id ?? data.location,
      location_type: location?.locationType,
      selected_secret: data.selected_secret,
      roles: data.roles,
      status: data.status,
      tenant: data.tenant || undefined,
    };

    try {
      await startWorkflow(
        "/v1/workflow/ngc/site_password_rotation",
        workflowParams
      );
      toast({
        title: "Workflow Started",
        description: "Site password rotation workflow has been initiated.",
      });
      form.reset();
    } catch (error) {
      toast({
        variant: "destructive",
        title: "Workflow Failed",
        description: `Failed to create workflow: ${error}`,
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleChange = () => {
    setIsManualChange(true);
  };

  return (
    <div className="flex items-center justify-center p-6">
      <Card className="w-full max-w-4xl border-2 shadow-md">
        <CardHeader>
          <CardTitle>New Site Password Rotation Workflow</CardTitle>
        </CardHeader>
        <CardContent>
          <Form {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
              <WorkflowFormField
                type="select"
                control={form.control}
                name="location"
                label="Location"
                options={envData.siteData}
                isSubmitting={isSubmitting}
                handleChange={handleChange}
              />
              
              <WorkflowFormField
                type="select"
                control={form.control}
                name="roles"
                label="Roles"
                options={envData.rolesData}
                isSubmitting={isSubmitting}
                handleChange={handleChange}
                multiple={true}
              />
              
              <WorkflowFormField
                type="select"
                control={form.control}
                name="status"
                label="Device Status"
                options={envData.statusData}
                isSubmitting={isSubmitting}
                handleChange={handleChange}
                multiple={true}
              />
              
              <WorkflowFormField
                type="select"
                control={form.control}
                name="tenant"
                label="Tenant"
                options={envData.tenantsData}
                isSubmitting={isSubmitting}
                handleChange={handleChange}
              />

              <DeviceMatchStatus
                location={location}
                devicesLoading={devicesLoading}
                deviceCount={devices.length}
              />

              <WorkflowFormField
                type="select"
                control={form.control}
                name="selected_secret"
                label={secretLabel}
                options={secretOptions}
                disabled={!firstDeviceId || isSubmitting}
                isSubmitting={isSubmitting}
                handleChange={handleChange}
              />
              
              <Button 
                type="submit" 
                disabled={isSubmitting}
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
