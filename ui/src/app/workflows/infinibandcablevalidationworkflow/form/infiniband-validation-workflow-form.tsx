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
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Form } from "@/components/ui/form";
import { useToast } from "@/components/ui/use-toast";
import { useEnvData, useDevices } from "@/hooks";
import { getErrorMessage, startWorkflow } from "@/lib/utils";
import { WorkflowFormField } from "@/components/forms/formfield";
import { InfinibandCableValidationWorkflowInput } from "@/types/data-table.types";
import { DeviceOption } from "@/types/workflow-form.types";

const InfinibandValidationFormSchema = z.object({
  site: z.string().trim().min(1, { message: "Site is required" }),
  device: z.string().trim().min(1, { message: "Device is required" }),
  deviceIds: z.array(z.string()).min(1, { message: "Device IDs is required" }),
});

export const InfinibandValidationWorkflowForm = () => {
  const [isSubmitting, setIsSubmitting] = React.useState<boolean>(false);
  const [isManualChange, setIsManualChange] = React.useState<boolean>(false);
  const { toast } = useToast();
  const searchParams = useSearchParams();
  const querySite = searchParams?.get("site") || "";
  const queryDevice = searchParams?.get("device") || "";
  const queryDeviceIds = React.useMemo(
    () => searchParams?.getAll("device-id") ?? [],
    [searchParams]
  );

  const {
    data: { siteData: sites },
    isLoading: { siteIsLoading },
  } = useEnvData();

  const form = useForm<z.infer<typeof InfinibandValidationFormSchema>>({
    resolver: zodResolver(InfinibandValidationFormSchema),
    defaultValues: {
      site: querySite,
      device: queryDevice,
      deviceIds: queryDeviceIds,
    },
  });

  const watchSite = form.watch("site") as string;

  const { devices: deviceData, isLoading: deviceIsLoading } = useDevices({
    site: watchSite,
    filterParams: [
      ["site", watchSite],
      ["platform", "UFM"],
    ],
  });
  const { devices: deviceIdsData, isLoading: deviceIdsIsLoading } = useDevices({
    site: watchSite,
    filterParams: [
      ["site", watchSite],
      ["platform", "MLNX-OS"],
    ],
  });

  React.useEffect(() => {
    if (querySite && !isManualChange) {
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
  }, [querySite, sites, form, isManualChange]);

  React.useEffect(() => {
    if (deviceData && queryDevice && !isManualChange) {
      const isDeviceValid = deviceData.some(
        (item: DeviceOption) => item.value === queryDevice
      );

      if (isDeviceValid) {
        if (form.getValues("device") !== queryDevice) {
          form.setValue("device", queryDevice); // Set valid device from URL
        }
      } else if (form.getValues("device") !== "") {
        form.setValue("device", ""); // Clear device if invalid
      }
    }
  }, [queryDevice, deviceData, form, isManualChange]);

  React.useEffect(() => {
    if (deviceIdsData && queryDeviceIds.length > 0 && !isManualChange) {
      // Validate each deviceId in the query params
      const validDeviceIds = queryDeviceIds.filter((deviceId) =>
        deviceIdsData.some((item: DeviceOption) => item.value === deviceId)
      );

      if (validDeviceIds.length > 0) {
        form.setValue("deviceIds", validDeviceIds);
      } else {
        form.setValue("deviceIds", []);
      }
    }
  }, [queryDeviceIds, deviceIdsData, form, isManualChange]);

  React.useEffect(() => {
    form.setValue("device", "");
    form.setValue("deviceIds", []);
  }, [watchSite, form]);

  const onSubmit = async (
    data: z.infer<typeof InfinibandValidationFormSchema>
  ) => {
    setIsSubmitting(true);
    const workflowParams: InfinibandCableValidationWorkflowInput = {
      ufm_device_id: data.device,
      switch_device_ids: data.deviceIds,
    };

    await startWorkflow(
      "/v1/workflow/ngc/infiniband_cable_validation",
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

  const handleChange = () => {
    setIsManualChange(true);
  };

  return (
    <div className="flex items-center justify-center p-6">
      <Card className="h-full border-2 shadow-md justify-center">
        <CardHeader>
          <CardTitle>New InfiniBand Cable Validation Workflow</CardTitle>
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
                handleChange={handleChange}
              />
              <WorkflowFormField
                type="select"
                control={form.control}
                name="device"
                label="Device"
                options={deviceData}
                isLoading={deviceIsLoading}
                isSubmitting={isSubmitting}
                handleChange={handleChange}
              />
              <WorkflowFormField
                type="select"
                control={form.control}
                name="deviceIds"
                label="Device IDs"
                options={deviceIdsData}
                isLoading={deviceIdsIsLoading}
                isSubmitting={isSubmitting}
                handleChange={handleChange}
                multiple={true}
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
