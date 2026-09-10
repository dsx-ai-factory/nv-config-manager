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

import { useState, useEffect } from "react";
import { useSearchParams } from "next/navigation";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Form } from "@/components/ui/form";
import { useToast } from "@/components/ui/use-toast";
import {
  useEnvData,
  useNamespaceTags,
  useSyncSelectFromQuery,
  useTenants,
} from "@/hooks";
import { WorkflowFormField } from "@/components/forms/formfield";
import { getErrorMessage, startWorkflow } from "@/lib/utils";
import {
  resolveLocationFormValue,
  resolveLocationOption,
} from "@/lib/location-options";
import { SpXOverlayCreationWorkflowInput } from "@/types/data-table.types";

const SpXOverlayCreationFormSchema = z
  .object({
    site: z.string().trim().min(1, { message: "Site is required" }),
    overlay_id: z.string().trim().min(1, { message: "Overlay ID is required" }),
    tenant: z.string().trim().min(1, { message: "Tenant is required" }),
    namespace_tag: z.string().trim().min(1, { message: "Namespace Tag is required" }),
    rd_min: z.number().min(0).max(65535),
    rd_max: z.number().min(0).max(65535),
  })
  .refine((data) => data.rd_min < data.rd_max, {
    message: "RD Min must be less than RD Max",
    path: ["rd_min"],
  });

export const SpXOverlayCreationWorkflowForm = () => {
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const { toast } = useToast();
  const searchParams = useSearchParams();
  const querySite = searchParams?.get("site") || "";
  const queryOverlayId = searchParams?.get("overlay_id") || "";
  const queryTenant = searchParams?.get("tenant") || "";
  const queryNamespaceTag =
    searchParams?.get("namespace_tag") ||
    searchParams?.get("namespace") ||
    "spectrumx";
  const queryRDMin = Number(searchParams?.get("rd_min")) || 60000;
  const queryRDMax = Number(searchParams?.get("rd_max")) || 65000;
  const {
    data: { siteData: sites },
    isLoading: { siteIsLoading },
  } = useEnvData();
  const {
    tenants,
    hasLoaded: tenantsHaveLoaded,
    isLoading: tenantsAreLoading,
  } = useTenants();

  const form = useForm<z.infer<typeof SpXOverlayCreationFormSchema>>({
    resolver: zodResolver(SpXOverlayCreationFormSchema),
    defaultValues: {
      site: querySite,
      overlay_id: queryOverlayId,
      tenant: queryTenant,
      namespace_tag: queryNamespaceTag,
      rd_min: queryRDMin,
      rd_max: queryRDMax,
    },
  });
  const selectedSite = form.watch("site");
  const {
    namespaceTags,
    hasLoaded: namespaceTagsHasLoaded,
    isLoading: namespaceTagsIsLoading,
  } = useNamespaceTags(selectedSite);

  useEffect(() => {
    if (!siteIsLoading && sites && querySite) {
      const siteValue = resolveLocationFormValue(sites, querySite);
      if (siteValue) {
        // Set the site value if it exists and the form value is empty
        if (!form.getValues("site")) {
          form.setValue("site", siteValue);
        }
      } else {
        form.setValue("site", "");
      }
    }
  }, [sites, querySite, siteIsLoading, form]);

  useSyncSelectFromQuery({
    fieldName: "tenant",
    form,
    hasLoaded: tenantsHaveLoaded,
    isLoading: tenantsAreLoading,
    options: tenants,
    queryValue: queryTenant,
  });

  useEffect(() => {
    if (!namespaceTagsHasLoaded || namespaceTagsIsLoading) return;

    const namespaceTag = form.getValues("namespace_tag");
    const namespaceTagExists = namespaceTags.some(
      (tag) => tag.value === namespaceTag
    );
    if (namespaceTag && !namespaceTagExists) {
      form.setValue("namespace_tag", "", { shouldValidate: true });
    }
  }, [namespaceTags, namespaceTagsHasLoaded, namespaceTagsIsLoading, form]);

  const onSubmit = async (data: z.infer<typeof SpXOverlayCreationFormSchema>) => {
    setIsSubmitting(true);
    const location = resolveLocationOption(sites, data.site);
    const submissionData: SpXOverlayCreationWorkflowInput = {
      site: location?.id ?? data.site,
      site_model: location?.model,
      overlay_id: data.overlay_id,
      tenant: data.tenant,
      namespace_tag: data.namespace_tag,
      rd_min: data.rd_min,
      rd_max: data.rd_max,
    };
    await startWorkflow(
      "/v1/workflow/ngc/spx_overlay_creation",
      submissionData
    ).catch((error) => {
      toast({
        variant: "destructive",
        title: "Workflow Failed",
        description: getErrorMessage(error),
      });
    });
    setIsSubmitting(false);
  };

  return (
    <div className="flex items-center justify-center p-6">
      <Card className="h-full border-2 shadow-md justify-center">
        <CardHeader>
          <CardTitle>New SpX Overlay Creation Workflow</CardTitle>
        </CardHeader>
        <CardContent>
          <Form {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
              <WorkflowFormField
                type="select"
                control={form.control}
                name="site"
                label="Site"
                options={sites}
                isLoading={siteIsLoading}
                isSubmitting={isSubmitting}
              />
              <WorkflowFormField
                type="input"
                control={form.control}
                name="overlay_id"
                label="Overlay ID"
                isSubmitting={isSubmitting}
              />
              <WorkflowFormField
                type="select"
                control={form.control}
                name="tenant"
                label="Tenant"
                options={tenants}
                isLoading={tenantsAreLoading}
                isSubmitting={isSubmitting}
                searchable
              />
              <WorkflowFormField
                type="select"
                control={form.control}
                name="namespace_tag"
                label="Namespace Tag"
                options={namespaceTags}
                isLoading={namespaceTagsIsLoading}
                isSubmitting={isSubmitting}
                searchable
              />
              <WorkflowFormField
                type="number"
                control={form.control}
                name="rd_min"
                label="RD Min"
                isSubmitting={isSubmitting}
              />
              <WorkflowFormField
                type="number"
                control={form.control}
                name="rd_max"
                label="RD Max"
                isSubmitting={isSubmitting}
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
