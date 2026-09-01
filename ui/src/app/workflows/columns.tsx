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

import { ColumnDef } from "@tanstack/react-table";
import type { ReactNode } from "react";
import { SortableHeaderButton } from "@/components/data-table";
import { WorkflowColumns, WorkflowMetadata } from "@/types/data-table.types";
import { renderDeviceNameField } from "@/lib/utils";
import { useRuntimeConfig } from "@/config/runtime";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { CircleMinus, Search } from "lucide-react";

type SearchParamsLike = {
  get: (name: string) => string | null;
  toString: () => string;
};

function normalizeWorkflowStatusParam(value: string): string {
  return value.trim().replaceAll("-", "_").replaceAll(" ", "_").toUpperCase();
}

function isFilterActive(
  searchParams: SearchParamsLike,
  param: string,
  value: string
): boolean {
  if (param != "status") {
    return searchParams.get(param) == value;
  }

  const status = normalizeWorkflowStatusParam(searchParams.get("status") ?? "");
  const pendingApproval =
    searchParams.get("pending_approval")?.toLowerCase() == "true";

  if (value == "PENDING_APPROVAL") {
    return status == "RUNNING" && pendingApproval;
  }

  return status == value && !pendingApproval;
}

function filterHref(
  currentSearchParams: SearchParamsLike,
  param: string,
  value: string
): string {
  const params = new URLSearchParams(currentSearchParams.toString());

  if (isFilterActive(currentSearchParams, param, value)) {
    params.delete(param);
    if (param == "status") {
      params.delete("pending_approval");
    }
  } else if (param == "status" && value == "PENDING_APPROVAL") {
    params.set("status", "RUNNING");
    params.set("pending_approval", "true");
  } else {
    params.set(param, value);
    if (param == "status") {
      params.delete("pending_approval");
    }
  }

  const queryString = params.toString();
  return queryString ? `/workflows?${queryString}` : "/workflows";
}

const workflowStatusOptions = [
  { label: "Running", value: "RUNNING" },
  { label: "Pending Approval", value: "PENDING_APPROVAL" },
  { label: "Completed", value: "COMPLETED" },
  { label: "Failed", value: "FAILED" },
  { label: "Terminated", value: "TERMINATED" },
  { label: "Not Started", value: "NOT_STARTED" },
];

const workflowStatusLabels = new Map(
  workflowStatusOptions.map((option) => [option.value, option.label])
);

function getWorkflowStatusLabel(status: string): string {
  return workflowStatusLabels.get(status) ?? status;
}

function getWorkflowDisplayStatus(workflow: WorkflowColumns): string {
  if (workflow.status !== "RUNNING") {
    return workflow.status;
  }
  if (workflow.failed_stage) {
    return "FAILED";
  }
  if (workflow.pending_approval) {
    return "PENDING_APPROVAL";
  }
  return workflow.status;
}

function FilterValueIcon({
  label,
  param,
  value,
}: Readonly<{
  label: string;
  param: string;
  value: string;
}>) {
  const searchParams = useSearchParams();
  const isActive = isFilterActive(searchParams, param, value);
  const ariaLabel = isActive
    ? `Remove ${label} filter: ${value}`
    : `Filter by ${label}: ${value}`;

  return (
    <Link
      aria-label={ariaLabel}
      className="relative -top-1 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-sm !border-b-0 align-super text-muted-foreground no-underline hover:!border-b-0 hover:bg-accent hover:text-accent-foreground hover:no-underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
      href={filterHref(searchParams, param, value)}
      title={ariaLabel}
    >
      {isActive ? (
        <CircleMinus className="h-2.5 w-2.5" />
      ) : (
        <Search className="h-2.5 w-2.5" />
      )}
    </Link>
  );
}

function FilterableValue({
  children,
  label,
  param,
  value,
}: Readonly<{
  children: ReactNode;
  label: string;
  param: string;
  value: string;
}>) {
  const title =
    typeof children === "string" || typeof children === "number"
      ? String(children)
      : value;

  return (
    <span className="inline-flex max-w-full min-w-0 items-center gap-0.5">
      <span className="min-w-0 truncate" title={title}>
        {children}
      </span>
      <FilterValueIcon label={label} param={param} value={value} />
    </span>
  );
}

function WorkflowDateTimeCell({
  value,
}: Readonly<{ value?: string | null }>) {
  if (!value) {
    return null;
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return null;
  }

  const dateText = date.toLocaleDateString(undefined, {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });
  const timeText = date.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  });

  return (
    <time
      className="block leading-tight"
      dateTime={value}
      title={date.toLocaleString()}
    >
      <span className="block whitespace-nowrap">{dateText}</span>
      <span className="block whitespace-nowrap text-xs text-muted-foreground">
        {timeText}
      </span>
    </time>
  );
}

// Component wrapper to use runtime config in cell renderer
function DeviceNameCell({
  workflow,
}: Readonly<{ workflow: WorkflowColumns }>) {
  const { config } = useRuntimeConfig();
  const deviceName = workflow.search_attributes.DeviceName?.[0];
  const deviceNameValue = deviceName ? String(deviceName) : "";

  if (!deviceNameValue) {
    return null;
  }

  return (
    <FilterableValue
      label="Device Name"
      param="device_name"
      value={deviceNameValue}
    >
      {renderDeviceNameField(
        workflow,
        config?.dcimUrl,
        config?.dcimDisplayName
      )}
    </FilterableValue>
  );
}

function SearchAttributeCell({
  label,
  param,
  values,
}: Readonly<{
  label: string;
  param: string;
  values?: WorkflowColumns["search_attributes"][string];
}>) {
  if (!values || values.length === 0) {
    return null;
  }

  return (
    <div className="flex max-w-full min-w-0 flex-wrap gap-x-2 gap-y-1">
      {values.map((value, index) => {
        const stringValue = String(value);

        return (
          <FilterableValue
            key={`${stringValue}-${index}`}
            label={label}
            param={param}
            value={stringValue}
          >
            {stringValue}
          </FilterableValue>
        );
      })}
    </div>
  );
}

export const getWorkflowColumns = (
  workflowMetadata: WorkflowMetadata[]
): ColumnDef<WorkflowColumns>[] => {
  const workflowMetadataByName = new Map(
    workflowMetadata.map((metadata) => [metadata.name, metadata])
  );
  const workflowTypeOptions = workflowMetadata.map((metadata) => ({
    label: metadata.display_name,
    value: metadata.name,
  }));

  return [
    {
      accessorKey: "id",
      filterFn: "includesString",
      meta: {
        className: "w-[18rem] min-w-[18rem] max-w-[18rem]",
        columnLabel: "Workflow ID",
      },
      header: ({ column }) => {
        return <SortableHeaderButton column={column} title="Workflow ID" />;
      },
      cell: ({ row }) => {
        const id = row.original.id;
        return (
          <Link
            className="block max-w-full min-w-0"
            href={`/workflows/${id}`}
            title="View workflow details"
          >
            <span className="block truncate" title={id}>
              {id}
            </span>
          </Link>
        );
      },
      enableHiding: false,
    },
    {
      accessorKey: "workflow_type",
      filterFn: "includesString",
      meta: {
        className: "w-[10rem] min-w-[10rem] max-w-[10rem]",
        columnLabel: "Workflow Type",
        filterOptions: workflowTypeOptions,
        filterVariant: "select",
        placeholder: "Workflow Type",
      },
      header: ({ column }) => {
        return <SortableHeaderButton column={column} title="Workflow Type" />;
      },
      cell: ({ row }) => {
        const workflowType = row.original.workflow_type;
        const displayName =
          workflowMetadataByName.get(row.original.workflow_type)?.display_name ??
          workflowType;

        return (
          <FilterableValue
            label="Workflow Type"
            param="workflow_type"
            value={workflowType}
          >
            {displayName}
          </FilterableValue>
        );
      },
    },
    {
      accessorFn: (workflow) => getWorkflowDisplayStatus(workflow),
      id: "status",
      meta: {
        className: "w-[10rem] min-w-[10rem] max-w-[10rem]",
        columnLabel: "Status",
        filterOptions: workflowStatusOptions,
        filterVariant: "select",
        placeholder: "Status",
      },
      header: ({ column }) => {
        return <SortableHeaderButton column={column} title="Status" />;
      },
      cell: ({ row }) => {
        const status = getWorkflowDisplayStatus(row.original);

        return (
          <FilterableValue
            label="Status"
            param="status"
            value={status}
          >
            {getWorkflowStatusLabel(status)}
          </FilterableValue>
        );
      },
    },
    {
      accessorKey: "search_attributes.User",
      filterFn: "includesString",
      meta: {
        className: "w-[7rem] min-w-[7rem] max-w-[7rem]",
        columnLabel: "User",
      },
      header: ({ column }) => {
        return <SortableHeaderButton column={column} title="User" />;
      },
      cell: ({ row }) => {
        return (
          <SearchAttributeCell
            label="User"
            param="user"
            values={row.original.search_attributes.User}
          />
        );
      },
    },
    {
      accessorKey: "search_attributes.Site",
      filterFn: "includesString",
      meta: {
        className: "w-[6rem] min-w-[6rem] max-w-[6rem]",
        columnLabel: "Site",
      },
      header: ({ column }) => {
        return <SortableHeaderButton column={column} title="Site" />;
      },
      cell: ({ row }) => {
        return (
          <SearchAttributeCell
            label="Site"
            param="site"
            values={row.original.search_attributes.Site}
          />
        );
      },
    },
    {
      accessorKey: "search_attributes.DeviceName",
      filterFn: "includesString",
      meta: {
        className: "w-[10rem] min-w-[10rem] max-w-[10rem]",
        columnLabel: "Device Name",
      },
      header: ({ column }) => {
        return <SortableHeaderButton column={column} title="Device Name" />;
      },
      cell: ({ row }) => {
        return <DeviceNameCell workflow={row.original} />;
      },
    },
    {
      accessorKey: "search_attributes.DeviceID",
      filterFn: "includesString",
      meta: {
        className: "w-[8rem] min-w-[8rem] max-w-[8rem]",
        columnLabel: "Device ID",
      },
      header: ({ column }) => {
        return <SortableHeaderButton column={column} title="Device ID" />;
      },
      cell: ({ row }) => {
        return (
          <SearchAttributeCell
            label="Device ID"
            param="device_id"
            values={row.original.search_attributes.DeviceID}
          />
        );
      },
    },
    {
      accessorKey: "search_attributes.DeviceRole",
      filterFn: "includesString",
      meta: {
        className: "w-[9rem] min-w-[9rem] max-w-[9rem]",
        columnLabel: "Device Role",
      },
      header: ({ column }) => {
        return <SortableHeaderButton column={column} title="Device Role" />;
      },
      cell: ({ row }) => {
        return (
          <SearchAttributeCell
            label="Device Role"
            param="device_role"
            values={row.original.search_attributes.DeviceRole}
          />
        );
      },
    },
    {
      accessorKey: "search_attributes.DevicePlatform",
      filterFn: "includesString",
      meta: {
        className: "w-[9rem] min-w-[9rem] max-w-[9rem]",
        columnLabel: "Device Platform",
      },
      header: ({ column }) => {
        return <SortableHeaderButton column={column} title="Device Platform" />;
      },
      cell: ({ row }) => {
        return (
          <SearchAttributeCell
            label="Device Platform"
            param="device_platform"
            values={row.original.search_attributes.DevicePlatform}
          />
        );
      },
    },
    {
      accessorKey: "start_time",
      enableColumnFilter: false,
      meta: {
        className: "w-[7.5rem] min-w-[7.5rem] max-w-[7.5rem] whitespace-nowrap",
        columnLabel: "Start Time",
      },
      header: ({ column }) => {
        return <SortableHeaderButton column={column} title="Start Time" />;
      },
      cell: ({ row }) => {
        return <WorkflowDateTimeCell value={row.original.start_time} />;
      },
    },
    {
      accessorKey: "close_time",
      enableColumnFilter: false,
      meta: {
        className: "w-[7.5rem] min-w-[7.5rem] max-w-[7.5rem] whitespace-nowrap",
        columnLabel: "End Time",
      },
      header: ({ column }) => {
        return <SortableHeaderButton column={column} title="End Time" />;
      },
      cell: ({ row }) => {
        return <WorkflowDateTimeCell value={row.original.close_time} />;
      },
    },
  ];
};
