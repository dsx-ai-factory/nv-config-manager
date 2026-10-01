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
  FormMessage,
} from "@/components/ui/form";
import { useToast } from "@/components/ui/use-toast";
import { Checkbox } from "@/components/ui/checkbox";
import { WorkflowFormField } from "@/components/forms/formfield";
import { useEnvData, useDevices } from "@/hooks";
import { startWorkflow } from "@/lib/utils";
import { IBPortGuidDiscoveryWorkflowInput } from "@/types/data-table.types";

const formSchema = z.object({
  site: z.string().min(1, { message: "Site is required" }),
  ufm_device_id: z.string().min(1, { message: "UFM device is required" }),
  switch_device_ids: z
    .array(z.string())
    .min(1, { message: "At least one switch device is required" }),
  dry_run: z.boolean().default(true),
});

type FormData = z.infer<typeof formSchema>;

export const IBPortGuidDiscoveryWorkflowForm = () => {
  const [isSubmitting, setIsSubmitting] = React.useState<boolean>(false);
  const { toast } = useToast();
  const { data: envData } = useEnvData();

  const form = useForm<FormData>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      site: "",
      ufm_device_id: "",
      switch_device_ids: [],
      dry_run: true,
    },
  });

  const site = form.watch("site");

  const switchFilterParams = site
    ? [
        ["site", site],
        ["managed_only", "true"],
      ]
    : [];
  const { devices, isLoading: devicesLoading } = useDevices({
    site: site || "",
    filterParams: switchFilterParams,
  });

  // UFM appliances are identified by role, not platform, so query by role=UFM
  // instead of the managed-switch list.
  const ufmFilterParams = site
    ? [
        ["site", site],
        ["role", "UFM"],
      ]
    : [];
  const { devices: ufmDevices, isLoading: ufmDevicesLoading } = useDevices({
    site: site || "",
    filterParams: ufmFilterParams,
  });

  // Clear device selections when site changes, since the device list is
  // scoped to the chosen site.
  React.useEffect(() => {
    form.setValue("ufm_device_id", "");
    form.setValue("switch_device_ids", []);
  }, [site, form]);

  const onSubmit = async (data: FormData) => {
    setIsSubmitting(true);
    const endpoint = "/v1/workflow/ngc/ib_port_guid_discovery";
    const params: IBPortGuidDiscoveryWorkflowInput = {
      ufm_device_id: data.ufm_device_id,
      switch_device_ids: data.switch_device_ids,
      dry_run: data.dry_run,
    };

    try {
      await startWorkflow(endpoint, params);
      toast({
        title: "Workflow Started",
        description: "IB Port GUID Discovery workflow has been initiated.",
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

  return (
    <div className="flex items-center justify-center p-6">
      <Card className="w-full max-w-4xl border-2 shadow-md">
        <CardHeader>
          <CardTitle>New InfiniBand Port GUID Discovery Workflow</CardTitle>
        </CardHeader>
        <CardContent>
          <Form {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
              <WorkflowFormField
                type="select"
                control={form.control}
                name="site"
                label="Site"
                options={envData.siteData || []}
                isSubmitting={isSubmitting}
              />

              <WorkflowFormField
                type="select"
                control={form.control}
                name="ufm_device_id"
                label="UFM Device"
                options={ufmDevices}
                searchable={true}
                isLoading={ufmDevicesLoading}
                isSubmitting={isSubmitting}
              />

              <WorkflowFormField
                type="select"
                control={form.control}
                name="switch_device_ids"
                label="Switch Devices"
                options={devices}
                multiple={true}
                searchable={true}
                isLoading={devicesLoading}
                isSubmitting={isSubmitting}
              />

              <FormField
                control={form.control}
                name="dry_run"
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
                      <FormLabel>Dry run</FormLabel>
                      <FormDescription>
                        Compute the UFM-to-DCIM GUID mappings without
                        writing ib_guid back onto any interface. Uncheck to
                        apply the changes.
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
