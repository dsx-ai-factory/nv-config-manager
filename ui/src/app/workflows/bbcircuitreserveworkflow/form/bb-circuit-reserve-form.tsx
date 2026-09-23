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

import { WorkflowFormField } from "@/components/forms/formfield";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Form } from "@/components/ui/form";
import { useToast } from "@/components/ui/use-toast";
import { useBBDevices } from "@/hooks";
import { getErrorMessage, startWorkflow } from "@/lib/utils";

const schema = z
  .object({
    local_device: z.string().min(1, "Local device is required"),
    remote_device: z.string().min(1, "Remote device is required"),
    jira: z
      .string()
      .regex(
        /^[A-Za-z][A-Za-z0-9]+-\d+$/,
        "Use a Jira key"
      ),
    circuit_id: z.string(),
    path_metric: z.number().int().positive(),
    macsec: z.enum(["true", "false"]),
    minimum_links: z.number().int().positive("Minimum links must be at least one"),
  })
  .superRefine((data, context) => {
    if (data.local_device === data.remote_device) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["remote_device"],
        message: "Remote device must differ from local device",
      });
    }
  });

type CircuitReserveFormData = z.infer<typeof schema>;

export const BBCircuitReserveForm = () => {
  const [isSubmitting, setIsSubmitting] = React.useState(false);
  const { toast } = useToast();
  const { options: devices, isLoading: devicesLoading } = useBBDevices();
  const form = useForm<CircuitReserveFormData>({
    resolver: zodResolver(schema),
    defaultValues: {
      local_device: "",
      remote_device: "",
      jira: "",
      circuit_id: "",
      path_metric: 750,
      macsec: "true",
      minimum_links: 1,
    },
  });
  const localDevice = form.watch("local_device");
  const remoteDevices = devices.filter(
    (device) => device.value !== localDevice
  );

  const onSubmit = async (data: CircuitReserveFormData) => {
    setIsSubmitting(true);
    try {
      await startWorkflow("/v1/workflow/bb_sandbox/circuit_reserve", {
        local_device: data.local_device,
        remote_device: data.remote_device,
        jira: data.jira,
        circuit_id: data.circuit_id.trim() || null,
        path_metric: data.path_metric,
        macsec: data.macsec === "true",
        minimum_links: data.minimum_links,
      });
    } catch (error) {
      toast({
        variant: "destructive",
        title: "Workflow Failed",
        description: getErrorMessage(error),
      });
      setIsSubmitting(false);
    }
  };

  return (
    <div className="flex items-center justify-center p-6">
      <Card className="h-full w-full max-w-4xl border-2 shadow-md">
        <CardHeader>
          <CardTitle>BB Sandbox: Circuit Reserve</CardTitle>
          <p className="text-sm text-muted-foreground">
            Allocates unused ports, LAG names, and a /31, then writes them as
            Planned after approval.
          </p>
        </CardHeader>
        <CardContent>
          <Form {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
              <div className="grid gap-6 md:grid-cols-2">
                <WorkflowFormField
                  type="select"
                  control={form.control}
                  name="local_device"
                  label="Local Device"
                  options={devices}
                  isLoading={devicesLoading}
                  isSubmitting={isSubmitting}
                />
                <WorkflowFormField
                  type="select"
                  control={form.control}
                  name="remote_device"
                  label="Remote Device"
                  options={remoteDevices}
                  isLoading={devicesLoading}
                  isSubmitting={isSubmitting}
                />
                <WorkflowFormField
                  type="input"
                  control={form.control}
                  name="jira"
                  label="Jira"
                  placeholder="GNINWP-1274"
                  isSubmitting={isSubmitting}
                />
                <WorkflowFormField
                  type="input"
                  control={form.control}
                  name="circuit_id"
                  label="Existing Planned circuit"
                  placeholder="Leave blank to create BB-RESERVE-{jira}"
                  isSubmitting={isSubmitting}
                />
                <WorkflowFormField
                  type="number"
                  control={form.control}
                  name="path_metric"
                  label="Path IS-IS metric"
                  isSubmitting={isSubmitting}
                />
                <WorkflowFormField
                  type="number"
                  control={form.control}
                  name="minimum_links"
                  label="Minimum Links"
                  isSubmitting={isSubmitting}
                />
                <WorkflowFormField
                  type="select"
                  control={form.control}
                  name="macsec"
                  label="MACsec"
                  options={[
                    { key: "Yes", value: "true" },
                    { key: "No", value: "false" },
                  ]}
                  isSubmitting={isSubmitting}
                />
              </div>
              <Button type="submit" disabled={isSubmitting}>
                {isSubmitting ? "Submitting..." : "Review Circuit Reservation"}
              </Button>
            </form>
          </Form>
        </CardContent>
      </Card>
    </div>
  );
};
