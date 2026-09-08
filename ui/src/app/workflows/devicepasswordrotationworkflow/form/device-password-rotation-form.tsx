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
import { Form } from "@/components/ui/form";
import { useToast } from "@/components/ui/use-toast";
import { DevicePasswordRotationWorkflowInput } from "@/types/data-table.types";
import { useEnvData, useDevices } from "@/hooks";
import { sanitizeUrl, startWorkflow } from "@/lib/utils";
import { useRuntimeConfig } from "@/config/runtime";
import { WorkflowFormField } from "@/components/forms/formfield";
import useSWR from "swr";
import { fetcher } from "@/lib/fetcher";

const formSchema = z.object({
  site: z.string().trim().min(1, { message: "Site is required" }),
  device: z.string().trim().min(1, { message: "Device is required" }),
  selected_secret: z.string().trim().min(1, { message: "Secret is required" }),
});

type FormData = z.infer<typeof formSchema>;

export default function DevicePasswordRotationForm() {
  const [isSubmitting, setIsSubmitting] = React.useState<boolean>(false);
  const [isManualSiteChange, setIsManualSiteChange] = React.useState(false);
  const { config: runtimeConfig } = useRuntimeConfig();
  const { toast } = useToast();
  const searchParams = useSearchParams();

  const form = useForm<FormData>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      site: searchParams?.get("site") || "",
      device: searchParams?.get("device") || "",
      selected_secret: searchParams?.get("selected_secret") || "",
    },
  });

  const { data: envData } = useEnvData();
  
  // Get devices filtered by site
  const site = form.watch("site");
  const filterParams = site
    ? [
        ["site", site],
        ["managed_only", "true"],
      ]
    : [];
  const { devices } = useDevices({
    site,
    filterParams: filterParams
  });

  // Fetch password users when a device is selected
  const selectedDevice = form.watch("device");
  const { data: passwordUsers } = useSWR(
    selectedDevice && runtimeConfig ? sanitizeUrl(`${runtimeConfig.workflowApiUrl}/v1/parameter/device/${selectedDevice}/password_users`) : null,
    fetcher
  );

  // Clear device when site changes
  React.useEffect(() => {
    if (site && !isManualSiteChange) {
      form.setValue("device", "");
      form.setValue("selected_secret", "");
    }
    setIsManualSiteChange(false);
  }, [site, form, isManualSiteChange]);

  // Clear selected_secret when device changes
  React.useEffect(() => {
    if (selectedDevice) {
      form.setValue("selected_secret", "");
    }
  }, [selectedDevice, form]);

  const onSubmit = async (data: FormData) => {
    setIsSubmitting(true);
    const endpoint = "/v1/workflow/ngc/device_password_rotation";
    const params: DevicePasswordRotationWorkflowInput = {
      device_id: data.device,
      selected_secret: data.selected_secret,
    };

    try {
      await startWorkflow(endpoint, params);
      toast({
        title: "Workflow Started",
        description: "Device password rotation workflow has been initiated.",
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

  const handleSiteChange = () => {
    setIsManualSiteChange(true);
    form.setValue("device", "");
    form.setValue("selected_secret", "");
  };

  // Transform password users data for the dropdown
  const secretOptions = React.useMemo(() => {
    if (!passwordUsers) return [];
    return passwordUsers.map((user: { name: string; description: string }) => ({
      value: user.name,
      key: user.name
    }));
  }, [passwordUsers]);

  return (
    <Card className="w-full max-w-4xl mx-auto">
      <CardHeader>
        <CardTitle>New Device Password Rotation Workflow</CardTitle>
      </CardHeader>
      <CardContent>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
            {/* Site Selection */}
            <WorkflowFormField
              type="select"
              control={form.control}
              name="site"
              label="Site"
              options={envData.siteData || []}
              isSubmitting={isSubmitting}
              handleChange={handleSiteChange}
            />

            {/* Device Selection - filtered by site */}
            <WorkflowFormField
              type="select"
              control={form.control}
              name="device"
              label="Device"
              options={devices || []}
              disabled={!site || isSubmitting}
              isSubmitting={isSubmitting}
              handleChange={() => {
                // Device selection will trigger secret loading
              }}
            />

            {/* Secret Selection - loaded based on device */}
            <WorkflowFormField
              type="select"
              control={form.control}
              name="selected_secret"
              label="Secret to Rotate"
              options={secretOptions}
              disabled={!selectedDevice || isSubmitting}
              isSubmitting={isSubmitting}
              handleChange={() => {
                // Mark that user has made manual changes
              }}
            />

            <Button type="submit" disabled={isSubmitting || !site || !selectedDevice || !form.watch("selected_secret")}>
              {isSubmitting ? "Submitting..." : "Submit"}
            </Button>
          </form>
        </Form>
      </CardContent>
    </Card>
  );
}
