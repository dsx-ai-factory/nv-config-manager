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
import { useEnvData, useDevices, useCommandCatalogGrouped } from "@/hooks";
import type { CommandGroup } from "@/hooks";
import { startWorkflow } from "@/lib/utils";
import { DiagnosticsWorkflowInput } from "@/types/data-table.types";

const TICKETING_PLATFORMS = [{ key: "Jira", value: "jira" }];

interface CommandGroupListProps {
  commandGroups: CommandGroup[];
  selected: string[];
  isSubmitting: boolean;
  onToggle: (name: string) => void;
}

const CommandGroupList = ({ commandGroups, selected, isSubmitting, onToggle }: CommandGroupListProps) => (
  <div className="space-y-4">
    {commandGroups.map((group) => (
      <div key={group.label || "__all__"}>
        {group.label && (
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {group.label}
          </p>
        )}
        <div className="space-y-2">
          {group.commands.map((cmd) => (
            <div key={cmd.name} className="flex items-start space-x-3">
              <Checkbox
                id={`cmd-${cmd.name}`}
                checked={selected.includes(cmd.name)}
                onCheckedChange={() => onToggle(cmd.name)}
                disabled={isSubmitting}
              />
              <label
                htmlFor={`cmd-${cmd.name}`}
                className="cursor-pointer space-y-0.5 leading-none"
              >
                <span className="text-sm font-medium">{cmd.name}</span>
                <p className="text-xs text-muted-foreground">{cmd.description}</p>
              </label>
            </div>
          ))}
        </div>
      </div>
    ))}
  </div>
);

const formSchema = z.object({
  site: z.string().optional(),
  device_ids: z
    .union([z.string(), z.array(z.string())])
    .transform((val) => {
      if (Array.isArray(val)) return val;
      return val ? [val] : [];
    })
    .pipe(z.array(z.string()).min(1, { message: "At least one device is required" })),
  commands: z.array(z.string()).min(1, { message: "At least one command is required" }),
  ticketing_platform: z.string().trim().default(""),
  issue_key: z.string().trim().default(""),
  include_tech_support: z.boolean().default(false),
});

type FormData = z.infer<typeof formSchema>;

interface DiagnosticsWorkflowFormProps {
  submitPath?: string;
}

export const DiagnosticsWorkflowForm = ({
  submitPath = "/v1/workflow/ngc/diagnostics",
}: DiagnosticsWorkflowFormProps) => {
  const [isSubmitting, setIsSubmitting] = React.useState<boolean>(false);
  const { toast } = useToast();
  const { data: envData } = useEnvData();

  const form = useForm<FormData>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      site: "",
      device_ids: [],
      commands: [],
      ticketing_platform: "",
      issue_key: "",
      include_tech_support: false,
    },
  });

  const site = form.watch("site");
  const device_ids = form.watch("device_ids");

  const filterParams = site
    ? [
        ["site", site],
        ["managed_only", "true"],
      ]
    : [];
  const { devices, isLoading: devicesLoading } = useDevices({
    site: site || "",
    filterParams,
  });

  // Derive the unique platforms of the currently selected devices
  const selectedPlatforms = React.useMemo(() => {
    const selected = devices.filter((d) => device_ids.includes(d.value));
    return [...new Set(selected.map((d) => d.platform).filter(Boolean))] as string[];
  }, [devices, device_ids]);

  const { groups: commandGroups, allCommands, isLoading: commandsLoading } =
    useCommandCatalogGrouped(selectedPlatforms);

  // Clear device and command selections when site changes
  React.useEffect(() => {
    form.setValue("device_ids", []);
    form.setValue("commands", []);
  }, [site, form]);

  // Drop any selected commands that are no longer in the available catalog
  // (happens when device selection changes to a different platform)
  React.useEffect(() => {
    if (allCommands.length === 0) return;
    const available = new Set(allCommands.map((c) => c.name));
    const current: string[] = form.getValues("commands");
    const filtered = current.filter((c) => available.has(c));
    if (filtered.length !== current.length) {
      form.setValue("commands", filtered);
    }
  }, [allCommands, form]);

  const onSubmit = async (data: FormData) => {
    setIsSubmitting(true);
    const params: DiagnosticsWorkflowInput = {
      device_ids: data.device_ids,
      commands: data.commands,
      ticketing_platform: data.ticketing_platform,
      issue_key: data.issue_key,
      include_tech_support: data.include_tech_support,
      user: "",
    };

    try {
      await startWorkflow(submitPath, params);
      toast({
        title: "Workflow Started",
        description: "Diagnostics workflow has been initiated.",
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
          <CardTitle>New Device Diagnostics Workflow</CardTitle>
        </CardHeader>
        <CardContent>
          <Form {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
              {/* Site — optional filter for devices */}
              <WorkflowFormField
                type="select"
                control={form.control}
                name="site"
                label="Site (optional filter)"
                options={envData.siteData || []}
                isSubmitting={isSubmitting}
              />

              {/* Devices — multi-select */}
              <WorkflowFormField
                type="select"
                control={form.control}
                name="device_ids"
                label="Devices"
                options={devices}
                multiple={true}
                searchable={true}
                isLoading={devicesLoading}
                isSubmitting={isSubmitting}
              />

              {/* Commands — catalog-driven checkboxes */}
              <FormField
                control={form.control}
                name="commands"
                render={({ field }) => {
                  const selected: string[] = field.value ?? [];

                  const toggleAll = () => {
                    if (selected.length === allCommands.length) {
                      field.onChange([]);
                    } else {
                      field.onChange(allCommands.map((c) => c.name));
                    }
                  };

                  const toggle = (name: string) => {
                    if (selected.includes(name)) {
                      field.onChange(selected.filter((c) => c !== name));
                    } else {
                      field.onChange([...selected, name]);
                    }
                  };

                  let commandsContent: React.ReactNode;
                  if (commandsLoading) {
                    commandsContent = (
                      <p className="text-sm text-muted-foreground">
                        Loading commands...
                      </p>
                    );
                  } else if (device_ids.length === 0) {
                    commandsContent = (
                      <p className="text-sm text-muted-foreground">
                        Select devices above to see available commands.
                      </p>
                    );
                  } else if (allCommands.length === 0) {
                    commandsContent = (
                      <p className="text-sm text-muted-foreground">
                        No commands available for the selected platform(s).
                      </p>
                    );
                  } else {
                    commandsContent = (
                      <CommandGroupList
                        commandGroups={commandGroups}
                        selected={selected}
                        isSubmitting={isSubmitting}
                        onToggle={toggle}
                      />
                    );
                  }

                  return (
                    <FormItem>
                      <div className="flex items-center justify-between">
                        <FormLabel>Commands</FormLabel>
                        {allCommands.length > 0 && (
                          <button
                            type="button"
                            onClick={toggleAll}
                            disabled={isSubmitting}
                            className="text-xs text-muted-foreground hover:text-foreground underline"
                          >
                            {selected.length === allCommands.length
                              ? "Deselect all"
                              : "Select all"}
                          </button>
                        )}
                      </div>
                      <FormMessage />
                      <div className="space-y-2 rounded-md border p-3">
                        {commandsContent}
                      </div>
                    </FormItem>
                  );
                }}
              />

              {/* Ticketing platform */}
              <WorkflowFormField
                type="select"
                control={form.control}
                name="ticketing_platform"
                label="Ticketing Platform"
                options={TICKETING_PLATFORMS}
                isSubmitting={isSubmitting}
              />

              {/* Issue key */}
              <WorkflowFormField
                type="input"
                control={form.control}
                name="issue_key"
                label="Issue Key (optional — leave blank for ticketless mode)"
                placeholder="NETSUPPORT-1234"
                isSubmitting={isSubmitting}
              />


              {/* Include tech support bundle */}
              <FormField
                control={form.control}
                name="include_tech_support"
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
                      <FormLabel>Include tech support bundle</FormLabel>
                      <FormDescription>
                        Collect and upload a full tech support archive for each device.
                        This significantly increases runtime.
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
