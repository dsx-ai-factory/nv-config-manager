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

import { useState, useEffect, useRef } from "react";
import { useSearchParams } from "next/navigation";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Form } from "@/components/ui/form";
import { useToast } from "@/components/ui/use-toast";
import {
  SPX_OVERLAY_ISOLATION_TYPE,
  useEnvData,
  useNamespaceTags,
  useOverlays,
  useSyncSelectFromQuery,
} from "@/hooks";
import { WorkflowFormField } from "@/components/forms/formfield";
import { getErrorMessage, startWorkflow } from "@/lib/utils";
import {
  resolveLocationFormValue,
  resolveLocationOption,
} from "@/lib/location-options";
import { SpXOverlayDeletionWorkflowInput } from "@/types/data-table.types";

const SpXOverlayDeletionFormSchema = z.object({
  site: z.string().trim().min(1, { message: "Site is required" }),
  overlay_id: z.string().trim().min(1, { message: "Overlay ID is required" }),
  namespace_tag: z.string().trim().min(1, { message: "Namespace Tag is required" }),
});

export const SpXOverlayDeletionWorkflowForm = () => {
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const { toast } = useToast();
  const searchParams = useSearchParams();
  const querySite = searchParams?.get("site") || "";
  const queryOverlayId = searchParams?.get("overlay_id") || "";
  const queryNamespaceTag =
    searchParams?.get("namespace_tag") ||
    searchParams?.get("namespace") ||
    "spectrumx";
  const {
    data: { siteData: sites },
    isLoading: { siteIsLoading },
  } = useEnvData();

  const form = useForm<z.infer<typeof SpXOverlayDeletionFormSchema>>({
    resolver: zodResolver(SpXOverlayDeletionFormSchema),
    defaultValues: {
      site: querySite,
      overlay_id: queryOverlayId,
      namespace_tag: queryNamespaceTag,
    },
  });
  const selectedSite = form.watch("site");
  const previousSite = useRef(selectedSite);
  const {
    namespaceTags,
    hasLoaded: namespaceTagsHasLoaded,
    isLoading: namespaceTagsIsLoading,
  } = useNamespaceTags(selectedSite);
  const {
    overlays: spxOverlays,
    hasLoaded: spxOverlaysHaveLoaded,
    isLoading: spxOverlaysAreLoading,
  } = useOverlays({
    enabled: Boolean(selectedSite),
    isolationType: SPX_OVERLAY_ISOLATION_TYPE,
    location: selectedSite,
  });

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

  useEffect(() => {
    if (previousSite.current !== selectedSite) {
      form.setValue("overlay_id", "");
      previousSite.current = selectedSite;
    }
  }, [selectedSite, form]);

  useSyncSelectFromQuery({
    fieldName: "overlay_id",
    form,
    hasLoaded: spxOverlaysHaveLoaded,
    isLoading: spxOverlaysAreLoading,
    options: spxOverlays,
    queryValue: queryOverlayId,
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

  const onSubmit = async (data: z.infer<typeof SpXOverlayDeletionFormSchema>) => {
    setIsSubmitting(true);
    const location = resolveLocationOption(sites, data.site);
    const submissionData: SpXOverlayDeletionWorkflowInput = {
      site: location?.id ?? data.site,
      site_model: location?.model,
      overlay_id: data.overlay_id,
      namespace_tag: data.namespace_tag,
    };
    await startWorkflow(
      "/v1/workflow/ngc/spx_overlay_deletion",
      submissionData
    ).catch((error) => {
      toast({
        variant: "destructive",
        title: "SpX Overlay Deletion Workflow Failed",
        description: getErrorMessage(error),
      });
    });
    setIsSubmitting(false);
  };

  return (
    <div className="flex items-center justify-center p-6">
      <Card className="h-full border-2 shadow-md justify-center">
        <CardHeader>
          <CardTitle>New SpX Overlay Deletion Workflow</CardTitle>
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
                type="select"
                control={form.control}
                name="overlay_id"
                label="Overlay ID"
                options={spxOverlays}
                isLoading={spxOverlaysAreLoading}
                isSubmitting={isSubmitting}
                disabled={!selectedSite}
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
