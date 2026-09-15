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
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Checkbox } from "@/components/ui/checkbox";
import { useToast } from "@/components/ui/use-toast";
import { SiteBackupWorkflowInput } from "@/types/data-table.types";
import { useEnvData } from "@/hooks";
import { getErrorMessage, startWorkflow } from "@/lib/utils";
import { DEFAULT_SITE_WORKFLOW_STATUSES } from "@/lib/workflow-defaults";
import { WorkflowFormField } from "@/components/forms/formfield";

const SiteBackupFormSchema = z.object({
  site: z.string().trim().min(1, { message: "Site is required" }),
  roles: z
    .union([z.string(), z.array(z.string())])
    .optional()
    .transform((val: string | string[] | undefined) => (Array.isArray(val) ? val : []))
    .pipe(z.array(z.string())),
  status: z
    .union([z.string(), z.array(z.string())])
    .optional()
    .transform((val: string | string[] | undefined) => (Array.isArray(val) ? val : []))
    .pipe(z.array(z.string()).min(1, { message: "Device Status is required" })),
  tenant: z.string().trim().optional().default(""),
  backup_enabled_only: z.boolean().default(true),
});

type SiteBackupFormData = z.infer<typeof SiteBackupFormSchema>;
type QueryOption = { key: string; value: string };
type SingleValueField = "site" | "tenant";
type MultiValueField = "roles" | "status";

const parseBackupEnabledQuery = (value: string | null): boolean => {
  if (value === null) {
    return true;
  }
  return value.toLowerCase() !== "false";
};

const setSingleQueryValue = (
  form: UseFormReturn<SiteBackupFormData>,
  queryValue: string | null,
  options: QueryOption[],
  fieldName: SingleValueField
) => {
  const fieldValue =
    queryValue !== null && options.some((option) => option.key === queryValue)
      ? queryValue
      : "";
  form.setValue(fieldName, fieldValue);
};

const setMultiQueryValue = (
  form: UseFormReturn<SiteBackupFormData>,
  queryValues: string[],
  options: QueryOption[],
  fieldName: MultiValueField,
  emptyValue: string[] = []
) => {
  const fieldValue = queryValues.filter((queryValue) =>
    options.some((option) => option.key === queryValue)
  );
  form.setValue(fieldName, fieldValue.length > 0 ? fieldValue : emptyValue);
};

/** Renders the form for starting a site configuration backup workflow. */
export const SiteBackupWorkflowForm = () => {
  const [isSubmitting, setIsSubmitting] = React.useState<boolean>(false);
  const [isManualChange, setIsManualChange] = React.useState<boolean>(false);
  const { data: siteBackupData } = useEnvData();
  const { toast } = useToast();
  const searchParams = useSearchParams();
  const querySite = searchParams?.get("site");
  const queryRoles = React.useMemo(() => searchParams?.getAll("role") ?? [], [searchParams]);
  const queryStatuses = React.useMemo(() => searchParams?.getAll("status") ?? [], [searchParams]);
  const queryTenant = searchParams?.get("tenant");
  const queryBackupEnabledOnly = searchParams?.get("backup_enabled_only");

  const form = useForm<SiteBackupFormData>({
    resolver: zodResolver(SiteBackupFormSchema),
    defaultValues: {
      site: querySite || "",
      roles: queryRoles,
      status: queryStatuses.length > 0 ? queryStatuses : [...DEFAULT_SITE_WORKFLOW_STATUSES],
      tenant: queryTenant || "",
      backup_enabled_only: parseBackupEnabledQuery(queryBackupEnabledOnly),
    },
  });

  React.useEffect(() => {
    if (!isManualChange) {
      setSingleQueryValue(form, querySite, siteBackupData.siteData, "site");
      setMultiQueryValue(form, queryRoles, siteBackupData.rolesData, "roles");
      setMultiQueryValue(
        form,
        queryStatuses,
        siteBackupData.statusData,
        "status",
        [...DEFAULT_SITE_WORKFLOW_STATUSES]
      );
      setSingleQueryValue(form, queryTenant, siteBackupData.tenantsData, "tenant");
      form.setValue("backup_enabled_only", parseBackupEnabledQuery(queryBackupEnabledOnly));
    }
  }, [
    querySite,
    queryRoles,
    queryStatuses,
    queryTenant,
    queryBackupEnabledOnly,
    siteBackupData,
    form,
    isManualChange,
  ]);

  const onSubmit = async (data: SiteBackupFormData) => {
    setIsSubmitting(true);
    const workflowParams: SiteBackupWorkflowInput = {
      site: data.site,
      roles: data.roles,
      status: data.status,
      tenant: data.tenant || undefined,
      backup_enabled_only: data.backup_enabled_only,
    };

    await startWorkflow("/v1/workflow/ngc/site_backup", workflowParams).catch((error) => {
      toast({
        variant: "destructive",
        title: "Workflow Failed",
        description: getErrorMessage(error),
      });
    });
    setIsSubmitting(false);
  };

  const handleChange = () => {
    setIsManualChange(true);
  };

  return (
    <div className="flex items-center justify-center p-6">
      <Card className="h-full border-2 shadow-md justify-center">
        <CardHeader>
          <CardTitle>New Site Configuration Backup Workflow</CardTitle>
        </CardHeader>
        <CardContent>
          <Form {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
              <WorkflowFormField
                type="select"
                control={form.control}
                name="site"
                label="Site"
                options={siteBackupData.siteData}
                isSubmitting={isSubmitting}
                handleChange={handleChange}
              />
              <WorkflowFormField
                type="select"
                control={form.control}
                name="roles"
                label="Roles"
                options={siteBackupData.rolesData}
                isSubmitting={isSubmitting}
                handleChange={handleChange}
                multiple={true}
              />
              <WorkflowFormField
                type="select"
                control={form.control}
                name="status"
                label="Device Status"
                options={siteBackupData.statusData}
                isSubmitting={isSubmitting}
                handleChange={handleChange}
                multiple={true}
              />
              <WorkflowFormField
                type="select"
                control={form.control}
                name="tenant"
                label="Tenant"
                options={siteBackupData.tenantsData}
                isSubmitting={isSubmitting}
                handleChange={handleChange}
              />
              <FormField
                control={form.control}
                name="backup_enabled_only"
                render={({ field }) => (
                  <FormItem className="flex flex-row items-start space-x-3 space-y-0">
                    <FormControl>
                      <Checkbox
                        checked={field.value}
                        onCheckedChange={(checked) => {
                          field.onChange(checked === true);
                          handleChange();
                        }}
                        disabled={isSubmitting}
                      />
                    </FormControl>
                    <div className="space-y-1 leading-none">
                      <FormLabel>Backup enabled only</FormLabel>
                      <FormDescription>
                        Include only devices with backup enabled in the DCIM.
                        Uncheck to back up all managed devices that match the
                        other filters.
                      </FormDescription>
                    </div>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <Button type="submit" disabled={isSubmitting}>
                {isSubmitting ? "Submitting..." : "Submit"}
              </Button>
            </form>
          </Form>
        </CardContent>
      </Card>
    </div>
  );
};
