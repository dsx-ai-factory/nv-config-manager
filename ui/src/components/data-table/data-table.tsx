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
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import useSWRInfinite from "swr/infinite";
import {
  Column,
  ColumnDef,
  ColumnFiltersState,
  flexRender,
  getCoreRowModel,
  getPaginationRowModel,
  RowData,
  Table as TanstackTable,
  VisibilityState,
  useReactTable,
} from "@tanstack/react-table";
import { Columns3, RefreshCw } from "lucide-react";
import { fetcher } from "@/lib/fetcher";
import { TokenError } from "@/lib/errors";
import { useRuntimeConfig } from "@/config/runtime";
import { cn, sanitizeUrl } from "@/lib/utils";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Checkbox } from "@/components/ui/checkbox";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  DataTableProps,
  Workflow,
  WorkflowListResponse,
  WORKFLOW_STATUS,
} from "@/types/data-table.types";
import { DebouncedInput } from "@/components/ui/debounced-input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { useHeaderContext } from "@/app/contexts/header";
import { LoadingSpinner } from "@/components/ui/loading-spinner";

declare module "@tanstack/react-table" {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars -- required by module augmentation contract
  interface ColumnMeta<TData extends RowData, TValue> {
    className?: string;
    columnLabel?: string;
    filterOptions?: { label: string; value: string }[];
    filterVariant?: "select";
    placeholder?: string;
  }
}

const defaultColumnVisibility: VisibilityState = {
  search_attributes_DeviceID: false,
  search_attributes_DevicePlatform: false,
  search_attributes_DeviceRole: false,
};
const workflowPageSizeStorageKey = "nvcm.workflowTable.pageSize";
const workflowPageSizeOptions = [10, 50, 100];

const logWorkflowFetchError = (error: unknown): void => {
  console.error("Failed to update workflow data", error);
};

const isWorkflowPageSize = (pageSize: number): boolean =>
  workflowPageSizeOptions.includes(pageSize);

const getStoredWorkflowPageSize = (): number => {
  if (typeof globalThis.window === "undefined") {
    return workflowPageSizeOptions[0];
  }

  const pageSize = Number(
    globalThis.localStorage.getItem(workflowPageSizeStorageKey)
  );
  return isWorkflowPageSize(pageSize) ? pageSize : workflowPageSizeOptions[0];
};

const setStoredWorkflowPageSize = (pageSize: number) => {
  if (
    typeof globalThis.window === "undefined" ||
    !isWorkflowPageSize(pageSize)
  ) {
    return;
  }

  globalThis.localStorage.setItem(workflowPageSizeStorageKey, String(pageSize));
};

const workflowApiFilterParams: Record<string, string> = {
  id: "workflow_id",
  search_attributes_DeviceID: "device_id",
  search_attributes_DeviceName: "device_name",
  search_attributes_DevicePlatform: "device_platform",
  search_attributes_DeviceRole: "device_role",
  search_attributes_Site: "site",
  search_attributes_User: "user",
  status: "status",
  workflow_type: "workflow_type",
};

const normalizeWorkflowStatusFilterValue = (value: string): string =>
  value.trim().replaceAll("-", "_").replaceAll(" ", "_").toUpperCase();

const getWorkflowApiFilterString = (columnFilters: ColumnFiltersState): string => {
  const params = new URLSearchParams();

  columnFilters.forEach((filter) => {
    const value = String(filter.value ?? "").trim();
    const columnId = filter.id.replaceAll(".", "_");
    const apiParam = workflowApiFilterParams[columnId];

    if (apiParam && value) {
      const apiValue =
        columnId == "status" ? normalizeWorkflowStatusFilterValue(value) : value;

      if (columnId == "status" && apiValue == WORKFLOW_STATUS.pending_approval) {
        params.set("status", WORKFLOW_STATUS.running);
        params.set("pending_approval", "true");
      } else {
        params.set(apiParam, apiValue);
      }
    }
  });

  return params.toString();
};

type DateTimeFilterParts = {
  date: string;
  time: string;
};

const formatDateTimeFilterParts = (date: Date): DateTimeFilterParts => {
  const pad = (value: number) => String(value).padStart(2, "0");
  const year = date.getFullYear();
  const month = pad(date.getMonth() + 1);
  const day = pad(date.getDate());
  const hours = pad(date.getHours());
  const minutes = pad(date.getMinutes());

  return {
    date: `${year}-${month}-${day}`,
    time: `${hours}:${minutes}`,
  };
};

const getDateTimePartsFromSearchParam = (
  value: string | null
): DateTimeFilterParts => {
  if (!value) {
    return { date: "", time: "" };
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return { date: "", time: "" };
  }

  return formatDateTimeFilterParts(date);
};

const getIsoFromDateTimeParts = (
  dateValue: string,
  timeValue: string,
  fallbackTime: string
): string | null => {
  if (!dateValue) {
    return null;
  }

  const date = new Date(`${dateValue}T${timeValue || fallbackTime}`);
  if (Number.isNaN(date.getTime())) {
    return null;
  }

  return date.toISOString();
};

const getWorkflowRouteFilterString = ({
  columnFilters,
  hideCompleted,
  workflowEndClock,
  workflowEndDate,
  workflowStartClock,
  workflowStartDate,
}: {
  columnFilters: ColumnFiltersState;
  hideCompleted: boolean;
  workflowEndClock: string;
  workflowEndDate: string;
  workflowStartClock: string;
  workflowStartDate: string;
}): string => {
  const params = new URLSearchParams(getWorkflowApiFilterString(columnFilters));
  const startTime = getIsoFromDateTimeParts(
    workflowStartDate,
    workflowStartClock,
    "00:00"
  );
  const endTime = getIsoFromDateTimeParts(
    workflowEndDate,
    workflowEndClock,
    "23:59"
  );

  if (startTime) {
    params.set("start_time", startTime);
  }
  if (endTime) {
    params.set("end_time", endTime);
  }
  if (hideCompleted) {
    params.set("hidecompleted", "true");
  }

  return params.toString();
};

const getColumnFiltersFromSearchParams = (
  searchParams: { get: (name: string) => string | null } | null
): ColumnFiltersState => {
  if (!searchParams) {
    return [];
  }

  const pendingApprovalFilter =
    searchParams.get("pending_approval")?.toLowerCase() == "true";

  return Object.entries(workflowApiFilterParams).reduce<ColumnFiltersState>(
    (filters, [columnId, apiParam]) => {
      const value = searchParams.get(apiParam);
      const filterValue =
        columnId == "status" && value
          ? normalizeWorkflowStatusFilterValue(value)
          : value;

      if (
        columnId == "status" &&
        pendingApprovalFilter &&
        (!filterValue || filterValue == WORKFLOW_STATUS.running)
      ) {
        filters.push({ id: columnId, value: WORKFLOW_STATUS.pending_approval });
        return filters;
      }

      if (filterValue) {
        filters.push({ id: columnId, value: filterValue });
      }
      return filters;
    },
    []
  );
};

const areColumnFiltersEqual = (
  left: ColumnFiltersState,
  right: ColumnFiltersState
): boolean => {
  if (left.length !== right.length) {
    return false;
  }

  return left.every(
    (filter, index) =>
      filter.id === right[index].id && filter.value === right[index].value
  );
};

function Filter({ column }: { column: Column<Workflow, unknown> }) {
  const columnFilterValue = (column.getFilterValue() ?? "") as string;
  const { filterOptions, filterVariant, placeholder } = column.columnDef.meta ?? {};
  const selectValues = filterOptions?.map((option) => option.value) ?? [];

  const handleSelect = (value: string) => {
    if (value == "all") {
      column.setFilterValue("");
    } else if (selectValues.includes(value)) {
      column.setFilterValue(value);
    }
  };

  return filterVariant == "select" ? (
    <Select
      onValueChange={handleSelect}
      value={selectValues.includes(columnFilterValue) ? columnFilterValue : ""}
    >
      <SelectTrigger className="w-full">
        <SelectValue placeholder={placeholder ?? "Select"} />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="all">All</SelectItem>
        {filterOptions?.map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  ) : (
    <DebouncedInput
      type="text"
      value={columnFilterValue}
      onChange={(value) => column.setFilterValue(value)}
      placeholder={`Search...`}
      className="h-9 w-full min-w-0 rounded border shadow"
    />
  );
}

function ColumnVisibilityMenu({
  table,
}: Readonly<{ table: TanstackTable<Workflow> }>) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button className="gap-2" size="sm" variant="outline">
          <Columns3 size={16} />
          Columns
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-64">
        <div className="space-y-2">
          {table
            .getAllLeafColumns()
            .filter((column) => column.getCanHide())
            .map((column) => {
              const columnLabel = column.columnDef.meta?.columnLabel ?? column.id;

              return (
                <label
                  className="flex items-center gap-2 rounded-sm px-2 py-1.5 text-sm hover:bg-accent"
                  key={column.id}
                >
                  <Checkbox
                    aria-label={`Toggle ${columnLabel} column`}
                    checked={column.getIsVisible()}
                    onCheckedChange={(checked) =>
                      column.toggleVisibility(checked === true)
                    }
                  />
                  <span>{columnLabel}</span>
                </label>
              );
            })}
        </div>
      </PopoverContent>
    </Popover>
  );
}

export function DataTable<TData, TValue>({
  columns,
}: Readonly<DataTableProps<TData, TValue>>) {
  const { config } = useRuntimeConfig();
  const apiURL = config?.workflowApiUrl;
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const searchParamString = React.useMemo(
    () => searchParams?.toString() ?? "",
    [searchParams]
  );
  const [appliedSearchParamString, setAppliedSearchParamString] =
    React.useState(searchParamString);

  const [columnFilters, setColumnFilters] = React.useState<ColumnFiltersState>(
    () => getColumnFiltersFromSearchParams(searchParams)
  );
  const [workflowStartDate, setWorkflowStartDate] = React.useState<string>(() =>
    getDateTimePartsFromSearchParam(searchParams?.get("start_time") ?? null).date
  );
  const [workflowStartClock, setWorkflowStartClock] = React.useState<string>(() =>
    getDateTimePartsFromSearchParam(searchParams?.get("start_time") ?? null).time
  );
  const [workflowEndDate, setWorkflowEndDate] = React.useState<string>(() =>
    getDateTimePartsFromSearchParam(searchParams?.get("end_time") ?? null).date
  );
  const [workflowEndClock, setWorkflowEndClock] = React.useState<string>(() =>
    getDateTimePartsFromSearchParam(searchParams?.get("end_time") ?? null).time
  );
  const [columnVisibility, setColumnVisibility] = React.useState<VisibilityState>(
    defaultColumnVisibility
  );
  const [isHideCompletedChecked, setIsHideCompletedChecked] =
    React.useState<boolean>(
      () => searchParams?.get("hidecompleted")?.toLowerCase() == "true"
    );

  const { refreshPaused, setRefreshPaused } = useHeaderContext();
  const [pendingPageIndex, setPendingPageIndex] = React.useState<number | null>(
    null
  );
  const [pagination, setPagination] = React.useState({
    pageIndex: 0,
    pageSize: getStoredWorkflowPageSize(),
  });
  const apiFilterString = React.useMemo(() => {
    const params = new URLSearchParams(getWorkflowApiFilterString(columnFilters));
    const startTime = getIsoFromDateTimeParts(
      workflowStartDate,
      workflowStartClock,
      "00:00"
    );
    const endTime = getIsoFromDateTimeParts(
      workflowEndDate,
      workflowEndClock,
      "23:59"
    );

    if (startTime) {
      params.set("start_time", startTime);
    }
    if (endTime) {
      params.set("end_time", endTime);
    }
    if (isHideCompletedChecked) {
      params.set("hide_completed", "true");
    }

    return params.toString();
  }, [
    columnFilters,
    isHideCompletedChecked,
    workflowEndClock,
    workflowEndDate,
    workflowStartClock,
    workflowStartDate,
  ]);
  const routeFilterString = React.useMemo(
    () =>
      getWorkflowRouteFilterString({
        columnFilters,
        hideCompleted: isHideCompletedChecked,
        workflowEndClock,
        workflowEndDate,
        workflowStartClock,
        workflowStartDate,
      }),
    [
      columnFilters,
      isHideCompletedChecked,
      workflowEndClock,
      workflowEndDate,
      workflowStartClock,
      workflowStartDate,
    ]
  );

  const getWorkflowPageKey = React.useCallback(
    (
      pageIndex: number,
      previousPageData: WorkflowListResponse | null
    ): string | null => {
      if (refreshPaused || !apiURL) {
        return null;
      }

      const params = new URLSearchParams(apiFilterString);
      params.set("limit", String(pagination.pageSize));

      if (pageIndex > 0) {
        const nextPageToken = previousPageData?.next_page_token;
        if (!nextPageToken) {
          return null;
        }
        params.set("next_page_token", nextPageToken);
      }

      return sanitizeUrl(`${apiURL}/v1/workflow/?${params.toString()}`);
    },
    [apiFilterString, apiURL, pagination.pageSize, refreshPaused]
  );

  const {
    data: workflowPages,
    error: workflowError,
    isLoading,
    isValidating,
    mutate,
    setSize,
  } = useSWRInfinite<WorkflowListResponse>(
    getWorkflowPageKey,
    fetcher
  );

  const workflowData = React.useMemo(
    () => (workflowPages ?? []).flatMap((page) => page.workflows),
    [workflowPages]
  );
  const lastWorkflowPage = workflowPages?.[workflowPages.length - 1];
  const hasMoreData = Boolean(lastWorkflowPage?.next_page_token);

  React.useEffect(() => {
    if (workflowError instanceof TokenError) {
      setRefreshPaused(true);
    }
  }, [workflowError, setRefreshPaused]);

  React.useEffect(() => {
    const queryParam = searchParams?.get("hidecompleted")?.toLowerCase();
    setIsHideCompletedChecked(queryParam == "true");
  }, [searchParams]);

  const tableData = workflowData;

  const resetWorkflowFetchState = React.useCallback(() => {
    setPendingPageIndex(null);
    setSize(1).catch(logWorkflowFetchError);
    setPagination((currentPagination) => ({ ...currentPagination, pageIndex: 0 }));
  }, [setSize]);
  const didMountFilters = React.useRef(false);

  const handleColumnFiltersChange = React.useCallback(
    (
      updater:
        | ColumnFiltersState
        | ((old: ColumnFiltersState) => ColumnFiltersState)
    ) => {
      setColumnFilters((currentFilters) => {
        const nextFilters =
          typeof updater === "function" ? updater(currentFilters) : updater;

        if (areColumnFiltersEqual(currentFilters, nextFilters)) {
          return currentFilters;
        }

        return nextFilters;
      });
    },
    []
  );

  const table = useReactTable({
    data: tableData,
    columns: columns as ColumnDef<Workflow, unknown>[],
    state: {
      columnFilters,
      columnVisibility,
      pagination,
    },
    autoResetPageIndex: false,
    enableSorting: false,
    onColumnVisibilityChange: setColumnVisibility,
    onPaginationChange: setPagination,
    onColumnFiltersChange: handleColumnFiltersChange,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
  });

  React.useEffect(() => {
    const nextSearchParams = new URLSearchParams(searchParamString);
    const nextFilters = getColumnFiltersFromSearchParams(nextSearchParams);
    const nextStartTime = getDateTimePartsFromSearchParam(
      nextSearchParams.get("start_time")
    );
    const nextEndTime = getDateTimePartsFromSearchParam(
      nextSearchParams.get("end_time")
    );

    setWorkflowStartDate(nextStartTime.date);
    setWorkflowStartClock(nextStartTime.time);
    setWorkflowEndDate(nextEndTime.date);
    setWorkflowEndClock(nextEndTime.time);
    setAppliedSearchParamString(searchParamString);

    setColumnFilters((currentFilters) => {
      if (areColumnFiltersEqual(currentFilters, nextFilters)) {
        return currentFilters;
      }

      return nextFilters;
    });
  }, [searchParamString]);

  React.useEffect(() => {
    if (appliedSearchParamString != searchParamString) {
      return;
    }
    if (searchParamString == routeFilterString) {
      return;
    }

    router.replace(
      routeFilterString ? `${pathname}?${routeFilterString}` : pathname,
      { scroll: false }
    );
  }, [
    appliedSearchParamString,
    pathname,
    routeFilterString,
    router,
    searchParamString,
  ]);

  React.useEffect(() => {
    if (!didMountFilters.current) {
      didMountFilters.current = true;
      return;
    }

    resetWorkflowFetchState();
    mutate().catch(logWorkflowFetchError);
  }, [apiFilterString, mutate, resetWorkflowFetchState]);

  React.useEffect(() => {
    if (
      pendingPageIndex !== null &&
      tableData.length > pendingPageIndex * pagination.pageSize
    ) {
      setPagination((currentPagination) => ({
        ...currentPagination,
        pageIndex: pendingPageIndex,
      }));
      setPendingPageIndex(null);
    }
  }, [pagination.pageSize, pendingPageIndex, tableData.length]);

  const handleToggleHideCompleted = (checked: boolean | "indeterminate") => {
    setIsHideCompletedChecked(checked === true);
  };

  const handleClearFilters = () => {
    setColumnFilters([]);
    setWorkflowStartDate("");
    setWorkflowStartClock("");
    setWorkflowEndDate("");
    setWorkflowEndClock("");
    setIsHideCompletedChecked(false);
    resetWorkflowFetchState();
    if (searchParamString) {
      router.replace(pathname, { scroll: false });
    }
  };

  const handleRefresh = () => {
    mutate().catch(logWorkflowFetchError);
  };

  const handlePageSizeChange = (value: string) => {
    const pageSize = Number(value);
    if (!isWorkflowPageSize(pageSize)) {
      return;
    }

    setStoredWorkflowPageSize(pageSize);
    setPendingPageIndex(null);
    setPagination({ pageIndex: 0, pageSize });
    setSize(1).catch(logWorkflowFetchError);
  };

  const nextPageIndex = pagination.pageIndex + 1;
  const hasLoadedNextPage =
    tableData.length > nextPageIndex * pagination.pageSize;
  const hasNextPage = hasMoreData || hasLoadedNextPage;
  const totalWorkflowCount = lastWorkflowPage?.total_count ?? tableData.length;
  const totalPageCount =
    lastWorkflowPage?.page_count ??
    (hasNextPage ? nextPageIndex + 1 : pagination.pageIndex + 1);
  const pageLabel =
    totalPageCount === 0
      ? "Page 0 of 0"
      : `Page ${table.getState().pagination.pageIndex + 1} of ${totalPageCount}`;
  const workflowCountLabel = `${totalWorkflowCount.toLocaleString()} ${
    totalWorkflowCount === 1 ? "workflow" : "workflows"
  }`;

  const handleNextPage = () => {
    if (hasLoadedNextPage) {
      setPagination((currentPagination) => ({
        ...currentPagination,
        pageIndex: nextPageIndex,
      }));
      return;
    }

    if (hasMoreData && pendingPageIndex === null) {
      setPendingPageIndex(nextPageIndex);
      setSize((currentSize) => currentSize + 1).catch((error: unknown) => {
        logWorkflowFetchError(error);
        setPendingPageIndex(null);
      });
    }
  };

  const isFetchingNextPage = pendingPageIndex !== null;

  return (
    <>
      <div className="mb-4 mt-2 rounded-md border border-border/70 bg-card p-2 shadow-sm">
        <div className="flex w-full flex-wrap items-center justify-end gap-1.5">
          <label className="flex h-9 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md border border-input bg-background px-2 text-sm font-medium shadow-sm">
            <Checkbox
              aria-label="Hide completed workflows"
              onCheckedChange={handleToggleHideCompleted}
              checked={isHideCompletedChecked}
            />
            <span>Hide Completed</span>
          </label>
          <div className="flex max-w-full flex-wrap items-center justify-end gap-x-1.5 gap-y-2">
            <div className="flex items-center gap-1.5">
              <span className="text-xs font-medium text-muted-foreground">
                Start
              </span>
              <Input
                aria-label="Start date"
                className="h-9 w-36 shrink-0 bg-background px-2 shadow-sm"
                onChange={(event) => setWorkflowStartDate(event.target.value)}
                type="date"
                value={workflowStartDate}
              />
              <Input
                aria-label="Start time"
                className="h-9 w-28 shrink-0 bg-background px-2 shadow-sm"
                onChange={(event) => setWorkflowStartClock(event.target.value)}
                type="time"
                value={workflowStartClock}
              />
            </div>
            <div className="flex items-center gap-1.5">
              <span className="text-xs font-medium text-muted-foreground">
                End
              </span>
              <Input
                aria-label="End date"
                className="h-9 w-36 shrink-0 bg-background px-2 shadow-sm"
                onChange={(event) => setWorkflowEndDate(event.target.value)}
                type="date"
                value={workflowEndDate}
              />
              <Input
                aria-label="End time"
                className="h-9 w-28 shrink-0 bg-background px-2 shadow-sm"
                onChange={(event) => setWorkflowEndClock(event.target.value)}
                type="time"
                value={workflowEndClock}
              />
            </div>
          </div>

          <Button
            aria-label="Clear All Filters"
            onClick={handleClearFilters}
            size="sm"
            title="Clear all filters"
            variant="outline"
          >
            Clear
          </Button>
          <Button
            aria-label="Refresh workflows"
            className="gap-2"
            disabled={isValidating}
            onClick={handleRefresh}
            size="sm"
            variant="outline"
          >
            <RefreshCw
              className={isValidating ? "animate-spin" : ""}
              size={16}
            />
            Refresh
          </Button>
          <ColumnVisibilityMenu table={table} />
        </div>
      </div>
      <div className="overflow-hidden rounded-md border border-border/70 bg-card shadow-sm">
        <Table>
          <TableHeader className="bg-muted/40">
            {table.getHeaderGroups().map((headerGroup) => (
              <TableRow
                className="border-border/70 hover:bg-transparent"
                key={headerGroup.id}
              >
                {headerGroup.headers.map((header) => {
                  return (
                    <TableHead
                      className={cn(
                        "border-r border-border/50 px-3 pb-4 pt-3 align-top last:border-r-0",
                        header.column.columnDef.meta?.className
                      )}
                      key={header.id}
                    >
                      <div className="flex min-h-[4.25rem] flex-col gap-2">
                        <div className="flex min-h-8 items-center">
                          {header.isPlaceholder
                            ? null
                            : flexRender(
                                header.column.columnDef.header,
                                header.getContext()
                              )}
                        </div>
                        {header.column.getCanFilter() && (
                          <div>
                            <Filter column={header.column} />
                          </div>
                        )}
                      </div>
                    </TableHead>
                  );
                })}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody className="bg-card">
            {isLoading &&
            table.getState().pagination.pageIndex *
              table.getState().pagination.pageSize >=
              tableData.length ? (
              <TableRow>
                <TableCell
                  colSpan={table.getVisibleLeafColumns().length}
                  className="h-24 text-center"
                >
                  <div className="flex items-center justify-center h-full">
                    <LoadingSpinner />
                  </div>
                </TableCell>
              </TableRow>
            ) : table.getRowModel().rows?.length ? (
              table.getRowModel().rows.map((row) => (
                <TableRow
                  className="border-border/60 odd:bg-background/35 even:bg-muted/10 hover:bg-muted/30"
                  key={row.id}
                  data-state={row.getIsSelected() && "selected"}
                >
                  {row.getVisibleCells().map((cell) => (
                    <TableCell
                      className={cn(
                        "overflow-hidden border-r border-border/30 px-3 py-3 last:border-r-0",
                        cell.column.columnDef.meta?.className
                      )}
                      key={cell.id}
                    >
                      {flexRender(
                        cell.column.columnDef.cell,
                        cell.getContext()
                      )}
                    </TableCell>
                  ))}
                </TableRow>
              ))
            ) : (
              <TableRow>
                <TableCell
                  colSpan={table.getVisibleLeafColumns().length}
                  className="h-24 text-center"
                >
                  No results.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
        <div className="flex items-center justify-between w-full border-t border-border/70 bg-muted/20 p-4">
          <div className="flex items-center space-x-2">
            <Select
              value={String(table.getState().pagination.pageSize)}
              onValueChange={handlePageSizeChange}
            >
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Rows per page" />
              </SelectTrigger>
              <SelectContent>
                {workflowPageSizeOptions.map((pageSize) => (
                  <SelectItem key={pageSize} value={String(pageSize)}>
                    {pageSize} Rows
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="flex flex-grow flex-col items-center text-center">
            <div className="text-sm font-medium">
              {pageLabel}
            </div>
            <div className="text-xs text-muted-foreground">
              {workflowCountLabel}
            </div>
          </div>

          <div className="flex items-center space-x-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => table.previousPage()}
              disabled={!table.getCanPreviousPage()}
              className="px-4 py-2 border rounded"
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={handleNextPage}
              disabled={isFetchingNextPage || !hasNextPage}
              className="px-4 py-2 border rounded"
            >
              {isFetchingNextPage ? "Loading..." : "Next"}
            </Button>
          </div>
        </div>
      </div>
    </>
  );
}
