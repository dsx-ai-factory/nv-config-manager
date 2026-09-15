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
} from "@/components/ui/form";
import { useToast } from "@/components/ui/use-toast";
import { Checkbox } from "@/components/ui/checkbox";
import { WorkflowFormField } from "@/components/forms/formfield";
import { useEnvData } from "@/hooks";
import { startWorkflow } from "@/lib/utils";
import {
  resolveLocationFormValue,
  resolveLocationOption,
} from "@/lib/location-options";
import { MultiDeployWorkflowInput } from "@/types/data-table.types";

const multiDeployFormSchema = z.object({
  role: z.string().trim().min(1, { message: "Role is required" }),
  max_batch_size: z.coerce
    .number()
    .min(1, { message: "Batch size must be at least 1" })
    .max(100, { message: "Batch size cannot exceed 100" })
    .default(10),
  location: z.string().trim().optional(),
  status: z
    .union([z.string(), z.array(z.string())])
    .transform((val) => (Array.isArray(val) ? val : []))
    .pipe(z.array(z.string()))
    .optional(),
  tenant: z.string().trim().optional(),
  commit_confirm: z.boolean().optional().default(true),
});

type MultiDeployFormData = z.infer<typeof multiDeployFormSchema>;
type QueryOption = { key: string; value: string };
type SingleValueField = "role" | "location" | "tenant";

/** Maps a single query parameter key to its form option value or clears it. */
const setSingleQueryValue = (
  form: UseFormReturn<MultiDeployFormData>,
  queryValue: string | null,
  options: QueryOption[],
  fieldName: SingleValueField
) => {
  const fieldValue =
    options.find((option) => option.key === queryValue)?.value ?? "";
  form.setValue(fieldName, fieldValue);
};

/** Keeps valid status query keys and clears values without an option. */
const setMultiQueryValue = (
  form: UseFormReturn<MultiDeployFormData>,
  queryValues: string[],
  options: QueryOption[],
  fieldName: "status"
) => {
  const fieldValue = queryValues.filter((queryValue) =>
    options.some((option) => option.key === queryValue)
  );
  form.setValue(fieldName, fieldValue);
};

/** Renders the form for starting a multi-configuration deploy workflow. */
export const MultiDeployWorkflowForm = () => {
  const [isSubmitting, setIsSubmitting] = React.useState<boolean>(false);
  const [isManualChange, setIsManualChange] = React.useState<boolean>(false);
  const { data: envData } = useEnvData();
  const { toast } = useToast();
  const searchParams = useSearchParams();

  const queryRole = searchParams?.get("role");
  const queryBatchSize = searchParams?.get("max_batch_size");
  const queryLocation = searchParams?.get("location");
  const queryStatuses = React.useMemo(() => searchParams?.getAll("status") ?? [], [searchParams]);
  const queryTenant = searchParams?.get("tenant");

  const form = useForm<MultiDeployFormData>({
    resolver: zodResolver(multiDeployFormSchema),
    defaultValues: {
      role: "",
      max_batch_size: 10,
      location: "",
      status: [],
      tenant: "",
      commit_confirm: true,
    },
  });

  React.useEffect(() => {
    if (!isManualChange) {
      setSingleQueryValue(form, queryRole, envData.rolesData, "role");
      form.setValue(
        "location",
        resolveLocationFormValue(envData.siteData, queryLocation) ?? ""
      );
      setMultiQueryValue(form, queryStatuses, envData.statusData, "status");
      setSingleQueryValue(form, queryTenant, envData.tenantsData, "tenant");

      if (queryBatchSize) {
        const batchSize = Number.parseInt(queryBatchSize, 10);
        if (!Number.isNaN(batchSize) && batchSize >= 1 && batchSize <= 100) {
          form.setValue("max_batch_size", batchSize);
        } else {
          form.setValue("max_batch_size", 10);
        }
      }
    }
  }, [
    queryRole,
    queryBatchSize,
    queryLocation,
    queryStatuses,
    queryTenant,
    envData,
    form,
    isManualChange,
  ]);

  /** Prevents later query synchronization from replacing a manual selection. */
  const handleChange = () => {
    setIsManualChange(true);
  };

  /** Starts the workflow with the validated form data. */
  const onSubmit = async (data: MultiDeployFormData) => {
    setIsSubmitting(true);
    const endpoint = "/v1/workflow/ngc/multi_deploy";
    const location = resolveLocationOption(envData.siteData, data.location);
    const params: MultiDeployWorkflowInput = {
      role: data.role,
      max_batch_size: data.max_batch_size || 10,
      location: location?.id ?? (data.location?.trim() || null),
      location_type: location?.locationType,
      status: data.status && data.status.length > 0 ? data.status : null,
      tenant: data.tenant?.trim() || null,
      commit_confirm: data.commit_confirm ?? true,
    } as MultiDeployWorkflowInput;

    try {
      await startWorkflow(endpoint, params);
    } catch (error) {
      toast({
        variant: "destructive",
        title: "Workflow Failed",
        description: `Failed to create workflow: ${error}`,
      });
      setIsSubmitting(false);
    }
  };

  return (
    <div className="flex items-center justify-center p-6">
      <Card className="h-full border-2 shadow-md justify-center">
        <CardHeader>
          <CardTitle>New Multi-Configuration Deploy Workflow</CardTitle>
        </CardHeader>
        <CardContent>
          <Form {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
              <WorkflowFormField
                type="select"
                control={form.control}
                name="role"
                label="Role"
                options={envData.rolesData}
                isSubmitting={isSubmitting}
                handleChange={handleChange}
                multiple={false}
              />
              <WorkflowFormField
                type="number"
                control={form.control}
                name="max_batch_size"
                label="Max Batch Size"
                placeholder="10"
                isSubmitting={isSubmitting}
                handleChange={handleChange}
              />
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
