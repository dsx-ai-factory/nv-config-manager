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

import {
  useState,
  useEffect,
  type Dispatch,
  type SetStateAction,
} from "react";
import { useSearchParams } from "next/navigation";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm, type UseFormReturn } from "react-hook-form";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Form } from "@/components/ui/form";
import { useToast } from "@/components/ui/use-toast";
import { useEnvData, useDevices } from "@/hooks";
import { getErrorMessage, startWorkflow } from "@/lib/utils";
import { WorkflowFormField } from "@/components/forms/formfield";
import { PortLLDPInfoWorkflowInput } from "@/types/data-table.types";
import { DeviceOption } from "@/types/workflow-form.types";

const PortLLDPFormSchema = z
  .object({
    site: z.string().trim(),
    device: z.string().trim(),
    interface: z.string().trim(),
    remote_mac_address: z.string().trim(),
  })
  .superRefine((data, ctx) => {
    const hasAllDeviceInfo = Boolean(
      data.site && data.device && data.interface
    );
    const hasMacAddress = Boolean(data.remote_mac_address);

    // Case 1: Neither complete device info nor MAC address
    if (!hasAllDeviceInfo && !hasMacAddress) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message:
          "Please provide either all device information or a MAC address",
        path: ["remote_mac_address"],
      });
    }

    // Case 2: Partial device info (some fields filled but not all)
    if ((data.site || data.device || data.interface) && !hasAllDeviceInfo) {
      if (!data.site) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Site is required when providing device information",
          path: ["site"],
        });
      }
      if (!data.device) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Device is required when providing device information",
          path: ["device"],
        });
      }
      if (!data.interface) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Interface is required when providing device information",
          path: ["interface"],
        });
      }
    }

    // Case 3: Both device info and MAC address provided
    if (hasAllDeviceInfo && hasMacAddress) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message:
          "Please provide either device information OR MAC address, not both",
        path: ["remote_mac_address"],
      });
    }
  });

type PortLLDPFormData = z.infer<typeof PortLLDPFormSchema>;
type DeviceFieldName = "site" | "device" | "interface";
type BooleanSetter = Dispatch<SetStateAction<boolean>>;

const DEVICE_FIELDS: DeviceFieldName[] = ["site", "device", "interface"];

/** Clears all device-identifying fields when MAC-address mode is selected. */
const clearDeviceFields = (form: UseFormReturn<PortLLDPFormData>) => {
  DEVICE_FIELDS.forEach((fieldName) => form.setValue(fieldName, ""));
};

/** Activates MAC-address mode and clears incompatible device information. */
const setMacAddressMode = (
  value: string,
  form: UseFormReturn<PortLLDPFormData>,
  setHasDeviceInfo: BooleanSetter,
  setHasMacAddress: BooleanSetter
) => {
  setHasMacAddress(Boolean(value));
  setHasDeviceInfo(false);

  if (value) {
    clearDeviceFields(form);
  }
};

/** Reports whether any device field will remain populated after a change. */
const hasDeviceFieldsAfterChange = (
  fieldName: string,
  value: string,
  form: UseFormReturn<PortLLDPFormData>
) =>
  DEVICE_FIELDS.some((deviceField) =>
    Boolean(deviceField === fieldName ? value : form.getValues(deviceField))
  );

/** Activates device-information mode and clears an incompatible MAC address. */
const setDeviceInfoMode = (
  fieldName: string,
  value: string,
  form: UseFormReturn<PortLLDPFormData>,
  setHasDeviceInfo: BooleanSetter,
  setHasMacAddress: BooleanSetter
) => {
  if (value) {
    setHasMacAddress(false);
    setHasDeviceInfo(true);
    form.setValue("remote_mac_address", "");
    return;
  }

  setHasDeviceInfo(hasDeviceFieldsAfterChange(fieldName, value, form));
};

/** Renders the mutually exclusive device-info and MAC-address workflow form. */
export const PortLLDPInfoWorkflowForm = () => {
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [isManualChange, setIsManualChange] = useState<boolean>(false);
  const [hasDeviceInfo, setHasDeviceInfo] = useState<boolean>(false);
  const [hasMacAddress, setHasMacAddress] = useState<boolean>(false);
  const { toast } = useToast();
  const searchParams = useSearchParams();
  const querySite = searchParams?.get("site") || "";
  const queryDevice = searchParams?.get("device-id") || "";
  const queryInterface = searchParams?.get("interface") || "";
  const queryMacAddress = searchParams?.get("remote_mac_address") || "";
  const {
    data: { siteData: sites },
    isLoading: { siteIsLoading },
  } = useEnvData();
  const form = useForm<PortLLDPFormData>({
    resolver: zodResolver(PortLLDPFormSchema),
    defaultValues: {
      site: querySite,
      device: queryDevice,
      interface: queryInterface,
      remote_mac_address: queryMacAddress,
    },
  });

  const watchSite = form.watch("site") as string;
  const filterParams: string[][] = [
    ["site", watchSite],
    ["managed_only", "true"],
  ];
  const { devices: deviceData, isLoading: deviceIsLoading } = useDevices({
    site: watchSite,
    filterParams,
  });

  useEffect(() => {
    if (querySite && !isManualChange) {
      const isSiteValid = sites.some((option) => option.key === querySite);
      const siteId = sites.find((option) => option.key === querySite)?.value;

      if (isSiteValid) {
        if (siteId && form.getValues("site") !== siteId && !hasMacAddress) {
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
  }, [querySite, sites, form, isManualChange, hasMacAddress]);

  useEffect(() => {
    if (deviceData && queryDevice && !isManualChange) {
      const isDeviceValid = deviceData.some(
        (item: DeviceOption) => item.value === queryDevice
      );

      if (isDeviceValid) {
        if (form.getValues("device") !== queryDevice && !hasMacAddress) {
          form.setValue("device", queryDevice); // Set valid device from URL
        }
      } else if (form.getValues("device") !== "") {
        form.setValue("device", ""); // Clear device if invalid
      }
    }
  }, [queryDevice, deviceData, form, isManualChange, hasMacAddress]);

  useEffect(() => {
    form.setValue("device", "");
    console.log("changing");
  }, [watchSite, form]);

  useEffect(() => {
    if (queryMacAddress) {
      form.reset({
        site: "",
        device: "",
        interface: "",
        remote_mac_address: queryMacAddress,
      });
      setHasMacAddress(true);
      setHasDeviceInfo(false);
    } else if (querySite || queryDevice || queryInterface) {
      form.reset({
        site: querySite,
        device: queryDevice,
        interface: queryInterface,
        remote_mac_address: "",
      });
      setHasMacAddress(false);
      setHasDeviceInfo(true);
    }
  }, [queryMacAddress, querySite, queryDevice, queryInterface, form]);

  /** Selects the input mode associated with the field the user changed. */
  const handleChange = (fieldName: string, value: string) => {
    setIsManualChange(true);

    if (fieldName === "remote_mac_address") {
      setMacAddressMode(value, form, setHasDeviceInfo, setHasMacAddress);
      return;
    }

    setDeviceInfoMode(
      fieldName,
      value,
      form,
      setHasDeviceInfo,
      setHasMacAddress
    );
  };

  /** Starts the workflow with only the active input mode's data. */
  const onSubmit = async (data: PortLLDPFormData) => {
    setIsSubmitting(true);

    const submissionData: PortLLDPInfoWorkflowInput = data.remote_mac_address
      ? { remote_mac_address: data.remote_mac_address }
      : {
          device_id: data.device,
          interface: data.interface,
        };

    await startWorkflow(
      "/v1/workflow/ngc/port_lldp_info",
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
          <CardTitle>New Port LLDP Info Workflow</CardTitle>
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
                disabled={hasMacAddress}
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
                disabled={hasMacAddress}
                isSubmitting={isSubmitting}
                handleChange={handleChange}
              />
              <WorkflowFormField
                type="input"
                control={form.control}
                name="interface"
                label="Interface"
                disabled={hasMacAddress}
                isSubmitting={isSubmitting}
                handleChange={handleChange}
              />
              <WorkflowFormField
                type="input"
                control={form.control}
                name="remote_mac_address"
                label="MAC Address"
                disabled={hasDeviceInfo}
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
