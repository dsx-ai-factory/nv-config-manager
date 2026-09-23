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
import { useBBCircuits } from "@/hooks";
import { getErrorMessage, startWorkflow } from "@/lib/utils";

const schema = z.object({
  circuit_id: z.string().min(1, "Circuit is required"),
  jira: z
    .string()
    .regex(
      /^[A-Za-z][A-Za-z0-9]+-\d+$/,
      "Use a Jira key"
    ),
});

type CircuitActivateFormData = z.infer<typeof schema>;

export const BBCircuitActivateForm = () => {
  const [isSubmitting, setIsSubmitting] = React.useState(false);
  const { toast } = useToast();
  const { options: circuits, isLoading: circuitsLoading } =
    useBBCircuits("Planned");
  const form = useForm<CircuitActivateFormData>({
    resolver: zodResolver(schema),
    defaultValues: {
      circuit_id: "",
      jira: "",
    },
  });

  const onSubmit = async (data: CircuitActivateFormData) => {
    setIsSubmitting(true);
    try {
      await startWorkflow("/v1/workflow/bb_sandbox/circuit_activate", {
        circuit_id: data.circuit_id,
        jira: data.jira,
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
          <CardTitle>BB Sandbox: Circuit Activate</CardTitle>
          <p className="text-sm text-muted-foreground">
            Loads a Planned reservation, then sets those objects Active after
            approval. Device push stays mocked.
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
                  label="Planned circuit"
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
              </div>
              <Button type="submit" disabled={isSubmitting}>
                {isSubmitting ? "Submitting..." : "Review Circuit Activation"}
              </Button>
            </form>
          </Form>
        </CardContent>
      </Card>
    </div>
  );
};
