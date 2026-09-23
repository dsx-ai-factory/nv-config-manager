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
import {
  useBBCircuits,
  useBBDevices,
  useBBInterfaces,
  useBBNextPrefix,
} from "@/hooks";
import { getErrorMessage, startWorkflow } from "@/lib/utils";

const schema = z
  .object({
    circuit_id: z.string().min(1, "Circuit is required"),
    jira: z
      .string()
      .regex(
        /^[A-Za-z][A-Za-z0-9]+-\d+$/,
        "Use a Jira key"
      ),
    local_device: z.string().min(1, "Local device is required"),
    local_ports: z.array(z.string()).min(1, "Select at least one local port"),
    local_lag: z.string().regex(/^ae\d+$/, "Use a LAG name such as ae1201"),
    remote_device: z.string().min(1, "Remote device is required"),
    remote_ports: z.array(z.string()).min(1, "Select at least one remote port"),
    remote_lag: z.string().regex(/^ae\d+$/, "Use a LAG name such as ae1200"),
    ipv4_prefix: z
      .string()
      .cidr({ version: "v4", message: "Enter an IPv4 /31 prefix" }),
    minimum_links: z
      .number()
      .int()
      .positive("Minimum links must be at least one"),
    path_metric: z.number().int().positive(),
    macsec: z.enum(["true", "false"]),
    related_metrics: z.string(),
  })
  .superRefine((data, context) => {
    if (data.local_device === data.remote_device) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["remote_device"],
        message: "Remote device must differ from local device",
      });
    }
    if (!data.ipv4_prefix.endsWith("/31")) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["ipv4_prefix"],
        message: "IPv4 prefix must be a /31",
      });
    }
    if (
      data.minimum_links >
      Math.min(data.local_ports.length, data.remote_ports.length)
    ) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["minimum_links"],
        message:
          "Minimum links cannot exceed either side's selected port count",
      });
    }
  });

type CircuitTurnupFormData = z.infer<typeof schema>;

const parseRelatedMetrics = (value: string) =>
  value
    .split(/[\n,]+/)
    .map((entry) => entry.trim())
    .filter(Boolean)
    .map((entry) => {
      const [pair, metric] = entry.split("=").map((part) => part.trim());
      const separator = pair.lastIndexOf("-");
      return {
        local_pop: pair.slice(0, separator),
        remote_pop: pair.slice(separator + 1),
        metric: Number(metric),
        macsec: true,
      };
    });

export const BBCircuitTurnupForm = () => {
  const [isSubmitting, setIsSubmitting] = React.useState(false);
  const { toast } = useToast();
  const { options: circuits, isLoading: circuitsLoading } = useBBCircuits();
  const { options: devices, isLoading: devicesLoading } = useBBDevices();
  const form = useForm<CircuitTurnupFormData>({
    resolver: zodResolver(schema),
    defaultValues: {
      circuit_id: "",
      jira: "",
      local_device: "",
      local_ports: [],
      local_lag: "ae1201",
      remote_device: "",
      remote_ports: [],
      remote_lag: "ae1200",
      ipv4_prefix: "",
      minimum_links: 1,
      path_metric: 750,
      macsec: "true",
      related_metrics: "ATL0A-IAD0B=595\nIAD0A-IAD0B=10",
    },
  });
  const localDevice = form.watch("local_device");
  const remoteDevice = form.watch("remote_device");
  const { options: localPorts, isLoading: localPortsLoading } = useBBInterfaces(
    localDevice,
    "lag-member"
  );
  const { options: remotePorts, isLoading: remotePortsLoading } =
    useBBInterfaces(remoteDevice, "lag-member");
  const ipv4Suggestion = useBBNextPrefix(31);

  React.useEffect(() => {
    form.setValue("local_ports", []);
  }, [localDevice, form]);
  React.useEffect(() => {
    form.setValue("remote_ports", []);
  }, [remoteDevice, form]);
  React.useEffect(() => {
    if (ipv4Suggestion.data)
      form.setValue("ipv4_prefix", ipv4Suggestion.data.prefix);
  }, [ipv4Suggestion.data, form]);

  const remoteDevices = devices.filter(
    (device) => device.value !== localDevice
  );

  const onSubmit = async (data: CircuitTurnupFormData) => {
    setIsSubmitting(true);
    try {
      await startWorkflow("/v1/workflow/bb_sandbox/circuit_turnup", {
        circuit_id: data.circuit_id,
        jira: data.jira,
        local_device: data.local_device,
        local_ports: data.local_ports,
        local_lag: data.local_lag,
        remote_device: data.remote_device,
        remote_ports: data.remote_ports,
        remote_lag: data.remote_lag,
        ipv4_prefix: data.ipv4_prefix,
        minimum_links: data.minimum_links,
        path_metric: data.path_metric,
        macsec: data.macsec === "true",
        related_metrics: parseRelatedMetrics(data.related_metrics),
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
          <CardTitle>BB Sandbox: Circuit Turn-up</CardTitle>
          <p className="text-sm text-muted-foreground">
            Plans a WAN circuit and related POP-pair IS-IS metric changes.
          </p>
        </CardHeader>
        <CardContent>
          <Form {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
              <div className="grid gap-6 md:grid-cols-2">
                <WorkflowFormField
                  type="select"
                  control={form.control}
                  name="circuit_id"
                  label="Circuit"
                  options={circuits}
                  isLoading={circuitsLoading}
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
                  type="select"
                  control={form.control}
                  name="local_ports"
                  label="Local Ports"
                  options={localPorts}
                  multiple
                  isLoading={localPortsLoading}
                  disabled={!localDevice}
                  isSubmitting={isSubmitting}
                />
                <WorkflowFormField
                  type="select"
                  control={form.control}
                  name="remote_ports"
                  label="Remote Ports"
                  options={remotePorts}
                  multiple
                  isLoading={remotePortsLoading}
                  disabled={!remoteDevice}
                  isSubmitting={isSubmitting}
                />
                <WorkflowFormField
                  type="input"
                  control={form.control}
                  name="local_lag"
                  label="Local LAG"
                  placeholder="ae1201"
                  isSubmitting={isSubmitting}
                />
                <WorkflowFormField
                  type="input"
                  control={form.control}
                  name="remote_lag"
                  label="Remote LAG"
                  placeholder="ae1200"
                  isSubmitting={isSubmitting}
                />
                <WorkflowFormField
                  type="input"
                  control={form.control}
                  name="ipv4_prefix"
                  label="IPv4 /31"
                  placeholder="Allocated from BB-P2P"
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
                  type="number"
                  control={form.control}
                  name="path_metric"
                  label="New path IS-IS metric"
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
              <WorkflowFormField
                type="textarea"
                control={form.control}
                name="related_metrics"
                label="Related IGP (POP-POP=metric)"
                placeholder="ATL0A-IAD0B=595"
                isSubmitting={isSubmitting}
              />
              <Button type="submit" disabled={isSubmitting}>
                {isSubmitting ? "Submitting..." : "Review Circuit Turn-up"}
              </Button>
            </form>
          </Form>
        </CardContent>
      </Card>
    </div>
  );
};
