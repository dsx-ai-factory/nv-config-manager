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
} from "@/components/ui/form";
import { useToast } from "@/components/ui/use-toast";
import { SiteCableValidationWorkflowInput } from "@/types/data-table.types";
import { useEnvData } from "@/hooks";
import { getErrorMessage, startWorkflow } from "@/lib/utils";
import {
  resolveLocationFormValue,
  resolveLocationOption,
} from "@/lib/location-options";
import { DEFAULT_SITE_WORKFLOW_STATUSES } from "@/lib/workflow-defaults";
import { WorkflowFormField } from "@/components/forms/formfield";


const SiteCableValidationFormSchema = z.object({
  site: z.string().trim().min(1, { message: "Site is required" }),
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

type SiteCableValidationFormData = z.infer<typeof SiteCableValidationFormSchema>;
type QueryOption = { key: string; value: string };
type SingleValueField = "site" | "tenant";
type MultiValueField = "roles" | "status";

/** Maps a single query parameter key to its form option value or clears it. */
const setSingleQueryValue = (
  form: UseFormReturn<SiteCableValidationFormData>,
  queryValue: string | null,
  options: QueryOption[],
  fieldName: SingleValueField
) => {
  const fieldValue =
    options.find((option) => option.key === queryValue)?.value ?? "";
  form.setValue(fieldName, fieldValue);
};

/** Keeps valid multi-value query keys and clears values without an option. */
const setMultiQueryValue = (
  form: UseFormReturn<SiteCableValidationFormData>,
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

/** Renders the form for starting a site cable validation workflow. */
export const SiteCableValidationWorkflowForm = () => {
  const [isSubmitting, setIsSubmitting] = React.useState<boolean>(false);
  const [isManualChange, setIsManualChange] = React.useState<boolean>(false);
  const { data: siteCableData } = useEnvData();
  const { toast } = useToast();
  const searchParams = useSearchParams();
  const querySite = searchParams?.get("site");
  const queryRoles = React.useMemo(() => searchParams?.getAll("role") ?? [], [searchParams]);
  const queryStatuses = React.useMemo(() => searchParams?.getAll("status") ?? [], [searchParams]);
  const queryTenant = searchParams?.get("tenant");

  const form = useForm<SiteCableValidationFormData>({
    resolver: zodResolver(SiteCableValidationFormSchema),
    defaultValues: {
      site: querySite || "",
      roles: queryRoles,
      status: queryStatuses.length > 0 ? queryStatuses : [...DEFAULT_SITE_WORKFLOW_STATUSES],
      tenant: queryTenant || "",
    },
  });

  // NOTE: might need this to change the api route look at Workflow.tsx. Revist when rest of api routes are defined.
  //const filterParams = [["site", site], ...deviceFilterParams];
  //const params = new URLSearchParams(filterParams).toString();
  React.useEffect(() => {
    if (!isManualChange) {
      form.setValue(
        "site",
        resolveLocationFormValue(siteCableData.siteData, querySite) ?? ""
      );
      setMultiQueryValue(form, queryRoles, siteCableData.rolesData, "roles");
      setMultiQueryValue(
        form,
        queryStatuses,
        siteCableData.statusData,
        "status",
        [...DEFAULT_SITE_WORKFLOW_STATUSES]
      );
      setSingleQueryValue(
        form,
        queryTenant,
        siteCableData.tenantsData,
        "tenant"
      );
    }
  }, [
    querySite,
    queryRoles,
    queryStatuses,
    queryTenant,
    siteCableData,
    form,
    isManualChange,
  ]);

  /** Starts the workflow with the validated form data. */
  const onSubmit = async (data: SiteCableValidationFormData) => {
      setIsSubmitting(true);
      const location = resolveLocationOption(siteCableData.siteData, data.site);
      const workflowParams: SiteCableValidationWorkflowInput = {
        site: location?.id ?? data.site,
        site_type: location?.locationType,
        roles: data.roles,
        status: data.status,
        tenant: data.tenant || undefined,
        // Hardcode these values, not relevant to end users
        // only for advanced scenarios
        device_type_ids: [],
        raise_for_invalid: false,
      };

      await startWorkflow(
        "/v1/workflow/ngc/site_cable_validation",
        workflowParams
      ).catch((error) => {
        toast({
          variant: "destructive",
          title: "Workflow Failed",
        description: getErrorMessage(error),
      });
    });
    setIsSubmitting(false);
  };

  /** Prevents later query synchronization from replacing a manual selection. */
  const handleChange = () => {
    setIsManualChange(true);
  };

  return (
    <div className="flex items-center justify-center p-6">
      <Card className="h-full border-2 shadow-md justify-center">
        <CardHeader>
          <CardTitle>New Site Cable Validation Workflow</CardTitle>
        </CardHeader>
        <CardContent>
          <Form {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
              <WorkflowFormField
                type="select"
                control={form.control}
                name="site"
                label="Site"
                options={siteCableData.siteData}
                isSubmitting={isSubmitting}
                handleChange={handleChange}
              />
              <WorkflowFormField
                type="select"
                control={form.control}
                name="roles"
                label="Roles"
                options={siteCableData.rolesData}
                isSubmitting={isSubmitting}
                handleChange={handleChange}
                multiple={true}
              />
              <WorkflowFormField
                type="select"
                control={form.control}
                name="status"
                label="Device Status"
                options={siteCableData.statusData}
                isSubmitting={isSubmitting}
                handleChange={handleChange}
                multiple={true}
              />
              <WorkflowFormField
                type="select"
                control={form.control}
                name="tenant"
                label="Tenant"
                options={siteCableData.tenantsData}
                isSubmitting={isSubmitting}
                handleChange={handleChange}
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
